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

#: Comprimento mínimo do vetor lateral (marcha × normal) para ele ser usado
#: como direção. Vai a zero só quando o carro anda paralelo à normal do
#: asfalto — o que não é pilotagem —, e a trava existe para não dividir por
#: zero em vez de para filtrar dado ruim.
MIN_LATERAL_NORM = 1e-6

# Coerência interna de uma volta: distância percorrida contra o que a velocidade
# média e o tempo prometem. Uma volta pode ter distância um pouco menor que a
# integral ingênua (pausa, quadro perdido), mas não uma ordem de grandeza —
# abaixo de 10% do esperado, os dois números não descrevem a mesma volta.
MIN_PLAUSIBLE_DISTANCE_RATIO = 0.10

# Abaixo disto não há o que contradizer: carro parado no box a volta inteira
# tem distância baixa legitimamente, e comparar razões perto de zero produziria
# descarte por ruído.
MIN_EXPECTED_DISTANCE_M = 50.0


def _expected_distance_m(points: list[TelemetryPoint], lap_time_ms: int) -> float:
    """Distância que a velocidade média promete para o tempo da volta.

    Deliberadamente grosseira — média simples, sem trapézio. Ela não substitui a
    integração; existe só para detectar contradição de ordem de grandeza, e para
    isso precisa ser calculada por um caminho **independente** daquele que
    produziu o número sob suspeita. Um verificador que reusasse a integração
    concordaria com ela inclusive quando ela está errada, que foi exatamente
    como o defeito original passou pelos testes.
    """
    if not points:
        return 0.0
    mean_speed_ms = sum(p.speed_kmh for p in points) / len(points) / 3.6
    return mean_speed_ms * (lap_time_ms / 1000.0)


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

    def __init__(self, event_bus: EventBus, *, sample_rate_hz: int = 60) -> None:
        self._bus = event_bus
        self._buffer: list[TelemetryPoint] = []
        self._cumulative_distance = 0.0

        # Milissegundos por tick do jogo. O GT7 transmite a 60 Hz, e é daqui que
        # sai todo o tempo — ver `_elapsed_ms`.
        self._ms_per_tick = 1000.0 / max(1, sample_rate_hz)
        self._lap_start_packet_id: int | None = None

        self._last_lap_count: int | None = None
        self._last_elapsed_ms: float | None = None
        self._last_speed_ms: float | None = None
        self._last_packet_id: int | None = None
        #: Quadros gastos em pausa/carregamento nesta volta. Descontados do
        #: tempo decorrido — ver `_elapsed_ms`.
        self._paused_ticks = 0

        self._prev_velocity_x: float | None = None
        self._prev_velocity_y: float | None = None
        self._prev_velocity_z: float | None = None
        self._prev_velocity_ms: float | None = None

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
        self._lap_start_packet_id = None
        self._last_packet_id = None
        self._paused_ticks = 0
        self._prev_velocity_x = None
        self._prev_velocity_y = None
        self._prev_velocity_z = None
        self._prev_velocity_ms = None

    # ---------- caminho quente (~60 Hz) ----------

    def _elapsed_ms(self, frame: TelemetryFrame) -> float:
        """Tempo decorrido na volta corrente, em ms — **em ponto flutuante**.

        **Derivado, porque o GT7 não o transmite.** O pacote traz o melhor tempo
        e o último, mas não o corrente — e ler 0x78 achando que era o corrente
        foi o que zerou a distância de toda volta capturada de um console real
        (ver `TelemetryFrame.packet_id`).

        A conta é o número de quadros do jogo desde o início da volta vezes o
        período de amostragem. Contar quadros, e não relógio de parede, é o que
        mantém o valor correto quando um pacote UDP se perde: o tick pula, o
        intervalo derivado pula junto, e a integração de distância não engole a
        lacuna como se o carro tivesse ficado parado.

        Float, e não inteiro, porque a 60 Hz o período é 16,666… ms: truncar a
        cada quadro somava 3,999 s numa freada de 4 s, e a distância — que é
        integrada justamente sobre esse `Δt` — herdava o erro no pior lugar, o
        ponto de frenagem. O arredondamento acontece uma vez só, quando o tempo
        vira campo da amostra gravada.
        """
        if self._lap_start_packet_id is None:
            self._lap_start_packet_id = frame.packet_id
        ticks = frame.packet_id - self._lap_start_packet_id
        if ticks < 0:
            # Contador reiniciou (sessão nova, ou o jogo voltou ao menu). Sem
            # isto, o tempo ficaria negativo e a integração pararia em silêncio.
            self._lap_start_packet_id = frame.packet_id
            self._paused_ticks = 0
            ticks = 0
        # O tick do GT7 **continua correndo com o jogo pausado**. Sem
        # descontar, despausar fazia o tempo da volta saltar o tamanho da
        # pausa de uma vez: o delta ao vivo dava um pulo que não veio de
        # pilotagem nenhuma, e a distância integrada herdava o erro.
        return max(0, ticks - self._paused_ticks) * self._ms_per_tick

    def on_frame(self, frame: TelemetryFrame) -> None:
        """Processa um quadro. Chamado ~60x/s — tudo aqui precisa ser barato."""

        # Virada de volta: o contador mudou, então a volta anterior fechou.
        if self._last_lap_count is not None and frame.lap_count != self._last_lap_count:
            self._finalize_lap(frame.last_lap_ms)
            # O relógio da volta nova começa aqui, não no início da sessão.
            self._lap_start_packet_id = frame.packet_id
            # A pausa da volta anterior não é dívida da volta nova.
            self._paused_ticks = 0

        elapsed_ms = self._elapsed_ms(frame)

        # Pausado ou carregando: o tempo do jogo não corre, então acumular
        # amostras inflaria a distância e distorceria o delta. O contador de
        # volta ainda é atualizado, para não perder a virada.
        #
        # O tick, porém, **corre na pausa** — é contador de quadros do jogo,
        # não de simulação. Cada quadro pausado é somado a `_paused_ticks` e
        # descontado em `_elapsed_ms`, senão despausar faz o tempo da volta
        # saltar o tamanho da pausa de uma vez.
        if frame.is_paused or frame.is_loading:
            if self._last_packet_id is not None:
                salto = frame.packet_id - self._last_packet_id
                if salto > 0:
                    self._paused_ticks += salto
            self._last_packet_id = frame.packet_id
            self._last_lap_count = frame.lap_count
            return

        g_lateral, g_longitudinal = self._compute_g_forces(frame, elapsed_ms)
        self._integrate_distance(frame, elapsed_ms)

        point = self._frame_to_point(frame, g_lateral, g_longitudinal, elapsed_ms)
        self._buffer.append(point)

        self._last_lap_count = frame.lap_count
        self._last_elapsed_ms = elapsed_ms
        self._last_speed_ms = frame.speed_kmh / 3.6
        self._last_packet_id = frame.packet_id

        self._bus.publish(TelemetryReceived(point=point, frame=frame))

    def _integrate_distance(self, frame: TelemetryFrame, elapsed_ms: float) -> None:
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
        if elapsed_ms < self._last_elapsed_ms:
            return  # relógio andou para trás: descarta em vez de somar lixo

        dt_s = (elapsed_ms - self._last_elapsed_ms) / 1000.0
        if dt_s <= 0:
            return

        speed_ms = frame.speed_kmh / 3.6
        average_speed = (self._last_speed_ms + speed_ms) / 2.0
        self._cumulative_distance += average_speed * dt_s

    def _compute_g_forces(
        self, frame: TelemetryFrame, elapsed_ms: float
    ) -> tuple[float, float]:
        """Força G lateral e longitudinal — o que os pneus estão fazendo.

        O pacote traz velocidade, não aceleração; a derivada é projetada nos
        eixos do carro (o vetor velocidade normalizado dá o "para frente", e o
        produto vetorial dele com a normal do asfalto dá o "para o lado"). Sem
        a projeção o valor seria aceleração no referencial do mundo, que muda
        de significado a cada curva.

        **A gravidade entra na conta.** Pela segunda lei, o que os pneus fazem
        é `a − g⃗`, e não `a` — numa descida a passo constante a aceleração é
        zero e mesmo assim os pneus estão freando, senão o carro ganharia
        velocidade. A versão anterior media só `a`, no plano XZ, e por isso
        relatava freada mais fraca do que a real em toda descida e mais forte
        em toda subida. Numa pista plana as duas contas dão exatamente o mesmo
        número: o termo novo entra multiplicado pela componente vertical, que
        ali é zero.

        Sem normal medida — volta antiga, fonte sintética —, cai no plano
        horizontal, que reproduz o comportamento de antes. Cair no horizontal é
        uma escolha ruim mas explícita; inventar uma inclinação seria pior.
        """
        if (
            self._prev_velocity_x is None
            or self._prev_velocity_y is None
            or self._prev_velocity_z is None
            or self._prev_velocity_ms is None
            or elapsed_ms <= self._prev_velocity_ms
            or frame.speed_kmh <= MIN_SPEED_FOR_G_KMH
        ):
            self._remember_velocity(frame, elapsed_ms)
            return 0.0, 0.0

        dt = (elapsed_ms - self._prev_velocity_ms) / 1000.0
        if dt <= MIN_DT_S:
            self._remember_velocity(frame, elapsed_ms)
            return 0.0, 0.0

        speed_xz = math.hypot(frame.velocity_x, frame.velocity_z)
        if speed_xz <= MIN_SPEED_XZ_MS:
            self._remember_velocity(frame, elapsed_ms)
            return 0.0, 0.0

        ax = (frame.velocity_x - self._prev_velocity_x) / dt
        ay = (frame.velocity_y - self._prev_velocity_y) / dt
        az = (frame.velocity_z - self._prev_velocity_z) / dt

        # Direção de marcha em três dimensões: numa rampa o carro anda para
        # frente **e** para cima, e um "para frente" achatado no plano XZ
        # projetaria a subida fora da conta.
        speed_3d = math.sqrt(
            frame.velocity_x**2 + frame.velocity_y**2 + frame.velocity_z**2
        )
        if speed_3d <= MIN_SPEED_XZ_MS:
            self._remember_velocity(frame, elapsed_ms)
            return 0.0, 0.0
        ux = frame.velocity_x / speed_3d
        uy = frame.velocity_y / speed_3d
        uz = frame.velocity_z / speed_3d

        nx, ny, nz = frame.road_normal or (0.0, 1.0, 0.0)

        # Lateral = marcha × normal. Na pista plana isto devolve exatamente a
        # perpendicular de antes, (−fz, 0, fx); numa curva com sobrelevação ela
        # se inclina junto com o asfalto, que é o que faz o banking aparecer
        # como alívio de carga lateral em vez de sumir da conta.
        rx = uy * nz - uz * ny
        ry = uz * nx - ux * nz
        rz = ux * ny - uy * nx
        norma_lateral = math.sqrt(rx * rx + ry * ry + rz * rz)
        if norma_lateral <= MIN_LATERAL_NORM:
            # Marcha paralela à normal — carro subindo na vertical. Não
            # acontece em pista; existe para não dividir por zero.
            self._remember_velocity(frame, elapsed_ms)
            return 0.0, 0.0
        rx, ry, rz = rx / norma_lateral, ry / norma_lateral, rz / norma_lateral

        # Força específica dos pneus, por unidade de massa: a − g⃗, com a
        # gravidade apontando para baixo.
        fx = ax
        fy = ay + GRAVITY
        fz = az

        g_longitudinal = (fx * ux + fy * uy + fz * uz) / GRAVITY
        g_lateral = (fx * rx + fy * ry + fz * rz) / GRAVITY

        self._remember_velocity(frame, elapsed_ms)
        return g_lateral, g_longitudinal

    def _remember_velocity(self, frame: TelemetryFrame, elapsed_ms: float) -> None:
        self._prev_velocity_x = frame.velocity_x
        self._prev_velocity_y = frame.velocity_y
        self._prev_velocity_z = frame.velocity_z
        self._prev_velocity_ms = elapsed_ms

    def _frame_to_point(
        self,
        frame: TelemetryFrame,
        g_lateral: float,
        g_longitudinal: float,
        elapsed_ms: float,
    ) -> TelemetryPoint:
        """DTO de fio → modelo de domínio. Única tradução entre os dois."""
        # A normal só atravessa se passar na própria validação; reprovada, os
        # três campos vão `None` **juntos**. Meio vetor não tem significado, e
        # um zero no lugar de uma componente que não foi medida seria a
        # afirmação "pista horizontal aqui".
        normal = frame.road_normal
        road_x, road_y, road_z = normal if normal is not None else (None, None, None)
        return TelemetryPoint(
            elapsed_ms=int(round(elapsed_ms)),
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
            position_y=frame.position_y,
            road_plane_x=road_x,
            road_plane_y=road_y,
            road_plane_z=road_z,
            g_lateral=g_lateral,
            g_longitudinal=g_longitudinal,
            suspension_fl=frame.suspension_fl,
            suspension_fr=frame.suspension_fr,
            suspension_rl=frame.suspension_rl,
            suspension_rr=frame.suspension_rr,
            flags=frame.flags,
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

        expected = _expected_distance_m(points, lap_time_ms)
        if expected > MIN_EXPECTED_DISTANCE_M and (
            distance < expected * MIN_PLAUSIBLE_DISTANCE_RATIO
        ):
            # A volta é internamente contraditória: o carro andou a essa
            # velocidade por esse tempo e não saiu do lugar.
            #
            # Esta guarda existe por causa de um defeito real. Dois offsets
            # trocados no protocolo faziam o tempo derivado ficar constante, e
            # toda volta capturada de um PS5 era gravada com distância 0,0 m —
            # com tempo certo, amostras certas e nenhum erro em lugar nenhum. O
            # dono do programa rodou uma sessão inteira antes de perceber,
            # porque nada denunciou.
            #
            # Descartar perde a volta, e é de propósito: distância é o índice de
            # toda a análise, então uma volta sem distância não é uma volta com
            # menos informação — é uma que produz curvas, freadas e perdas
            # inventadas. Guardá-la poluiria histórico e melhor-volta com dados
            # que parecem bons. Um aviso alto e nenhuma volta é recuperável;
            # análise confiante sobre lixo, não.
            _log.warning(
                "volta descartada: distância incoerente com velocidade e tempo",
                extra={
                    "lap": lap_number,
                    "distance_m": round(distance, 1),
                    "expected_m": round(expected, 1),
                    "time_ms": lap_time_ms,
                    "samples": len(points),
                },
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
