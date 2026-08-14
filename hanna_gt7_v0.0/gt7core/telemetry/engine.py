"""
Motor de telemetria: converte quadros do fio em amostras de domínio.

Extraído de `src/application/services/telemetry_service.py`. O que ficou aqui é
só o caminho quente — normalizar quadro, integrar distância, derivar força G e
detectar virada de volta. A decisão de gravar e o acesso a repositório saíram:
este módulo não sabe que existe banco, nem sessão, nem UI.

Uma correção em relação ao original (P5 da auditoria): a distância era integrada
pela **regra do retângulo** (`velocidade_atual × dt`), agora é pela **regra do
trapézio** (média entre as duas amostras).

**Correção da própria auditoria, medida aqui:** o documento de Fase 0 afirmou
que o erro do retângulo era "monotônico e acumulava ao longo da volta". Isso
está errado. Numa freada isolada de 240→60 km/h em 4 s o retângulo erra 0,50 m,
mas o erro por trecho monotônico é `(v_fim − v_ini)·dt/2` — e numa volta fechada,
que termina na mesma velocidade em que começou, a soma **telescopa e se cancela**.
Medido sobre uma volta sintética completa: diferença de 0,000 m entre os dois
métodos (ver `test_erro_do_retangulo_cancela_na_volta_fechada`).

O efeito real é **local, não cumulativo**: dentro de uma zona de frenagem a
distância acumulada fica até ~0,5 m deslocada, o que desalinha a comparação
exatamente onde ela mais importa (o delta é lido no ponto de freada). Continua
valendo a pena corrigir — o trapézio é exato para velocidade linear e não custa
nada a mais — mas a severidade era menor do que a auditoria registrou.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..domain.models import TelemetryPoint
from ..events.bus import EventBus
from ..observability.logging import get_logger
from .protocol import TelemetryFrame

_log = get_logger(__name__)

GRAVITY = 9.81

# Abaixo desta velocidade a derivada do vetor velocidade vira ruído numérico:
# parado ou quase parado, variações mínimas produziriam forças G absurdas.
MIN_SPEED_FOR_G_KMH = 5.0
MIN_SPEED_XZ_MS = 0.5
MIN_DT_S = 0.001


# ---------- eventos publicados ----------

@dataclass(frozen=True, slots=True)
class TelemetryReceived:
    point: TelemetryPoint
    frame: TelemetryFrame


@dataclass(frozen=True, slots=True)
class LapBoundaryDetected:
    """A volta virou. `points` são as amostras da volta que acabou.

    O motor não decide o que fazer com elas — quem grava, compara ou descarta
    assina este evento. É o que mantém o caminho quente sem I/O.
    """

    lap_number: int
    lap_time_ms: int
    points: list[TelemetryPoint]
    distance_m: float


class TelemetryEngine:
    """Normaliza o fluxo de quadros e publica o que acontece.

    Sem Qt, sem banco, sem rede — recebe quadros por `on_frame` e publica no
    barramento. Testável com uma lista de quadros sintéticos e nada mais.
    """

    def __init__(self, event_bus: EventBus) -> None:
        self._bus = event_bus
        self._buffer: list[TelemetryPoint] = []
        self._cumulative_distance = 0.0

        self._last_lap_count: int | None = None
        self._last_elapsed_ms: int | None = None
        self._last_speed_ms: float | None = None

        self._prev_velocity_x: float | None = None
        self._prev_velocity_z: float | None = None
        self._prev_velocity_ms: int | None = None

    # ---------- estado ----------

    @property
    def current_distance_m(self) -> float:
        return self._cumulative_distance

    @property
    def buffered_points(self) -> int:
        return len(self._buffer)

    def reset(self) -> None:
        """Zera o estado de volta. Chamado ao conectar e ao trocar de pista."""
        self._buffer = []
        self._cumulative_distance = 0.0
        self._last_lap_count = None
        self._last_elapsed_ms = None
        self._last_speed_ms = None
        self._prev_velocity_x = None
        self._prev_velocity_z = None
        self._prev_velocity_ms = None

    # ---------- caminho quente (~60 Hz) ----------

    def on_frame(self, frame: TelemetryFrame) -> None:
        """Processa um quadro. Chamado ~60x/s — tudo aqui precisa ser barato."""

        # Virada de volta: o contador mudou, então a volta anterior fechou.
        if self._last_lap_count is not None and frame.lap_count != self._last_lap_count:
            self._finalize_lap(frame.last_lap_ms)

        # Pausado ou carregando: o tempo do jogo não corre, então acumular
        # amostras inflaria a distância e distorceria o delta. O contador de
        # volta ainda é atualizado, para não perder a virada.
        if frame.is_paused or frame.is_loading:
            self._last_lap_count = frame.lap_count
            self._last_elapsed_ms = frame.current_lap_ms
            return

        g_lateral, g_longitudinal = self._compute_g_forces(frame)
        self._integrate_distance(frame)

        point = self._frame_to_point(frame, g_lateral, g_longitudinal)
        self._buffer.append(point)

        self._last_lap_count = frame.lap_count
        self._last_elapsed_ms = frame.current_lap_ms
        self._last_speed_ms = frame.speed_kmh / 3.6

        self._bus.publish(TelemetryReceived(point=point, frame=frame))

    def _integrate_distance(self, frame: TelemetryFrame) -> None:
        """Distância acumulada pela **regra do trapézio**.

        O GT7 não transmite hodômetro por volta, e é a distância que alinha a
        comparação entre voltas diferentes — então a precisão aqui se propaga
        para todo o analytics.

        A versão anterior usava `distância += v_atual × dt` (regra do
        retângulo), que assume velocidade constante no intervalo. Numa frenagem
        de 240 para 90 km/h a 60 Hz, cada amostra erra para mais; ao longo de uma
        volta com várias zonas de frenagem o erro acumula sempre no mesmo
        sentido. O trapézio usa a média entre a amostra anterior e a atual, o
        que cancela esse viés.
        """
        if self._last_elapsed_ms is None or self._last_speed_ms is None:
            return
        if frame.current_lap_ms < self._last_elapsed_ms:
            return  # relógio andou para trás: descarta em vez de somar lixo

        dt_s = (frame.current_lap_ms - self._last_elapsed_ms) / 1000.0
        if dt_s <= 0:
            return

        speed_ms = frame.speed_kmh / 3.6
        average_speed = (self._last_speed_ms + speed_ms) / 2.0
        self._cumulative_distance += average_speed * dt_s

    def _compute_g_forces(self, frame: TelemetryFrame) -> tuple[float, float]:
        """Força G lateral e longitudinal, derivando o vetor velocidade.

        Preservado do original sem mudança — a auditoria classificou este
        cálculo como correto e raro de ver feito certo. O pacote traz
        velocidade, não aceleração; a derivada é projetada nos eixos do carro (o
        vetor velocidade normalizado dá o "para frente", sua perpendicular no
        plano XZ dá o "para o lado"). Sem a projeção o valor seria aceleração no
        referencial do mundo, que muda de significado a cada curva.
        """
        if (
            self._prev_velocity_x is None
            or self._prev_velocity_z is None
            or self._prev_velocity_ms is None
            or frame.current_lap_ms <= self._prev_velocity_ms
            or frame.speed_kmh <= MIN_SPEED_FOR_G_KMH
        ):
            self._remember_velocity(frame)
            return 0.0, 0.0

        dt = (frame.current_lap_ms - self._prev_velocity_ms) / 1000.0
        if dt <= MIN_DT_S:
            self._remember_velocity(frame)
            return 0.0, 0.0

        ax = (frame.velocity_x - self._prev_velocity_x) / dt
        az = (frame.velocity_z - self._prev_velocity_z) / dt

        speed_xz = math.sqrt(frame.velocity_x**2 + frame.velocity_z**2)
        g_lateral = g_longitudinal = 0.0
        if speed_xz > MIN_SPEED_XZ_MS:
            fwd_x = frame.velocity_x / speed_xz
            fwd_z = frame.velocity_z / speed_xz
            right_x, right_z = -fwd_z, fwd_x
            g_longitudinal = (ax * fwd_x + az * fwd_z) / GRAVITY
            g_lateral = (ax * right_x + az * right_z) / GRAVITY

        self._remember_velocity(frame)
        return g_lateral, g_longitudinal

    def _remember_velocity(self, frame: TelemetryFrame) -> None:
        self._prev_velocity_x = frame.velocity_x
        self._prev_velocity_z = frame.velocity_z
        self._prev_velocity_ms = frame.current_lap_ms

    def _frame_to_point(
        self, frame: TelemetryFrame, g_lateral: float, g_longitudinal: float
    ) -> TelemetryPoint:
        """DTO de fio → modelo de domínio. Única tradução entre os dois."""
        return TelemetryPoint(
            elapsed_ms=frame.current_lap_ms,
            distance_m=self._cumulative_distance,
            speed_kmh=frame.speed_kmh,
            rpm=frame.rpm,
            gear=frame.gear,
            throttle=frame.throttle,
            brake=frame.brake,
            fuel_level=frame.fuel,
            tire_temp_fl=frame.tire_temp_fl,
            tire_temp_fr=frame.tire_temp_fr,
            tire_temp_rl=frame.tire_temp_rl,
            tire_temp_rr=frame.tire_temp_rr,
            position_x=frame.position_x,
            position_z=frame.position_z,
            g_lateral=g_lateral,
            g_longitudinal=g_longitudinal,
            suspension_fl=frame.suspension_fl,
            suspension_fr=frame.suspension_fr,
            suspension_rl=frame.suspension_rl,
            suspension_rr=frame.suspension_rr,
            tire_slip_fl=frame.tire_slip_fl,
            tire_slip_fr=frame.tire_slip_fr,
            tire_slip_rl=frame.tire_slip_rl,
            tire_slip_rr=frame.tire_slip_rr,
            turbo_boost=frame.turbo_boost,
            oil_temp=frame.oil_temp,
            water_temp=frame.water_temp,
        )

    # ---------- fechamento de volta ----------

    def _finalize_lap(self, lap_time_ms: int) -> None:
        """Fecha a volta que acabou e publica o evento com as amostras."""
        points = self._buffer
        distance = self._cumulative_distance
        lap_number = self._last_lap_count or 0

        # Reseta antes de publicar: se um assinante levantar exceção, o motor já
        # está em estado limpo para a próxima volta. O original resetava depois,
        # num `finally`, o que dava o mesmo resultado mas deixava a janela aberta.
        self._buffer = []
        self._cumulative_distance = 0.0
        self._last_elapsed_ms = None
        self._last_speed_ms = None

        if not points or lap_time_ms <= 0:
            _log.debug(
                "volta descartada",
                extra={"lap": lap_number, "samples": len(points), "time_ms": lap_time_ms},
            )
            return

        _log.info(
            "volta completa",
            extra={
                "lap": lap_number,
                "time_ms": lap_time_ms,
                "samples": len(points),
                "distance_m": round(distance, 1),
            },
        )

        self._bus.publish(
            LapBoundaryDetected(
                lap_number=lap_number,
                lap_time_ms=lap_time_ms,
                points=points,
                distance_m=distance,
            )
        )
