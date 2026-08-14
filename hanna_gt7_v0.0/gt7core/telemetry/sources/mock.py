"""
Fonte de telemetria sintética — §39 do briefing.

**Esta é a peça que destrava os testes.** A auditoria registrou como P4 que a
única implementação de `TelemetrySource` era o UDP real, e apontou isso como a
causa raiz de P1 (zero testes): sem fonte sintética, exercitar o pipeline exigia
um PS5 ligado na mesma rede.

O gerador produz `TelemetryFrame` — o mesmo DTO de fio que sai do decodificador
Salsa20 — então tudo a jusante (engine, analytics, gravação, UI) roda idêntico
ao ao vivo. Nenhum `if modo_teste` em lugar nenhum.

Dois modos, de propósito:

- `synthetic_lap()` — gerador puro, determinístico, sem thread. É o que os
  testes usam: mesma semente, mesmos números, sem `sleep`.
- `MockTelemetrySource` — envolve o gerador numa thread com ritmo real (60 Hz).
  É o que a aplicação usa para rodar sem console.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Iterator

from ..protocol import FLAG_CAR_ON_TRACK, FLAG_IN_GEAR, TelemetryFrame
from .base import ConnectionState, TelemetrySource

# Perfil de um circuito sintético: uma reta longa, três curvas de velocidades
# diferentes e uma chicane. Cada entrada é (fração da volta, velocidade alvo em
# km/h). A velocidade entre pontos é interpolada, o que produz zonas de frenagem
# e aceleração contínuas — não degraus.
_SPEED_PROFILE: tuple[tuple[float, float], ...] = (
    (0.00, 235.0),   # reta principal
    (0.12, 240.0),
    (0.18, 95.0),    # curva 1 — freada forte
    (0.26, 130.0),   # saída
    (0.38, 205.0),   # reta 2
    (0.44, 115.0),   # curva 2
    (0.52, 160.0),
    (0.61, 190.0),
    (0.66, 75.0),    # curva 3 — a mais lenta
    (0.74, 140.0),
    (0.82, 200.0),
    (0.88, 105.0),   # chicane
    (0.94, 180.0),
    (1.00, 235.0),   # volta ao início
)

DEFAULT_TRACK_LENGTH_M = 3800.0
DEFAULT_SAMPLE_RATE_HZ = 60
DEFAULT_CAR_ID = 2001


def _target_speed(lap_fraction: float) -> float:
    """Velocidade alvo na fração informada da volta, interpolada linearmente."""
    fraction = lap_fraction % 1.0
    previous_f, previous_v = _SPEED_PROFILE[0]
    for point_f, point_v in _SPEED_PROFILE[1:]:
        if fraction <= point_f:
            span = point_f - previous_f
            if span <= 0:
                return point_v
            ratio = (fraction - previous_f) / span
            return previous_v + ratio * (point_v - previous_v)
        previous_f, previous_v = point_f, point_v
    return _SPEED_PROFILE[-1][1]


def _gear_for_speed(speed_kmh: float) -> int:
    """Marcha plausível para a velocidade. Não modela a caixa de verdade — só
    precisa ser coerente o bastante para exercitar os gráficos."""
    for gear, ceiling in enumerate((60.0, 100.0, 140.0, 180.0, 215.0), start=1):
        if speed_kmh < ceiling:
            return gear
    return 6


def synthetic_lap(
    lap_number: int = 1,
    *,
    lap_time_ms: int = 102_000,
    track_length_m: float = DEFAULT_TRACK_LENGTH_M,
    sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ,
    pace_factor: float = 1.0,
    car_id: int = DEFAULT_CAR_ID,
    best_lap_ms: int = -1,
    last_lap_ms: int = -1,
) -> Iterator[TelemetryFrame]:
    """Gera os quadros de uma volta completa. Puro e determinístico.

    `pace_factor` escala a velocidade: 1.0 é o ritmo de referência, 0.97 é uma
    volta ~3% mais lenta. É assim que os testes de delta produzem duas voltas
    comparáveis sem depender de aleatoriedade.

    A posição x/z traça um circuito fechado (uma lemniscata, que dá um traçado
    com cruzamento e curvas de raios diferentes) para que o mapa de pista e a
    futura detecção de curvas tenham geometria real com que trabalhar.
    """
    frame_count = max(2, int(lap_time_ms / 1000 * sample_rate_hz))
    dt_ms = lap_time_ms / frame_count
    lap_time_s = lap_time_ms / 1000.0

    # Calibra o perfil para que a distância percorrida feche com o comprimento
    # da pista. Sem isto o gerador é fisicamente incoerente: a velocidade sai de
    # uma tabela e o comprimento de outra, e as duas discordam — o que quebraria
    # justamente os testes de alinhamento por distância que o mock existe para
    # viabilizar. O fator é derivado da média do perfil, então a forma das zonas
    # de frenagem é preservada; só a escala muda.
    profile_mean_ms = (
        sum(_target_speed(i / 720) for i in range(720)) / 720 / 3.6
    )
    speed_scale = track_length_m / (profile_mean_ms * pace_factor * lap_time_s)

    fuel = 60.0
    previous_speed_ms = 0.0

    for index in range(frame_count):
        elapsed_ms = int(index * dt_ms)
        fraction = index / frame_count

        speed_kmh = max(20.0, _target_speed(fraction) * pace_factor * speed_scale)
        speed_ms = speed_kmh / 3.6

        # Pedais derivados da variação de velocidade: acelerando → throttle,
        # desacelerando → brake. É o que produz zonas de frenagem coerentes com
        # o perfil, em vez de valores inventados.
        accel = (speed_ms - previous_speed_ms) / (dt_ms / 1000.0)
        previous_speed_ms = speed_ms
        if accel >= 0:
            throttle = min(100.0, 45.0 + accel * 18.0)
            brake = 0.0
        else:
            throttle = 0.0
            brake = min(100.0, -accel * 22.0)

        gear = _gear_for_speed(speed_kmh)
        rpm = 2200.0 + (speed_kmh / max(1, gear)) * 52.0

        # Traçado: lemniscata de Gerono, escalada para o comprimento da pista.
        theta = fraction * 2.0 * math.pi
        radius = track_length_m / (2.0 * math.pi)
        position_x = radius * math.sin(theta)
        position_z = radius * math.sin(theta) * math.cos(theta)

        # Direção do movimento, para que o vetor velocidade seja consistente com
        # a trajetória — o cálculo de força G deriva desse vetor.
        heading = theta + math.pi / 2.0
        velocity_x = speed_ms * math.cos(heading)
        velocity_z = speed_ms * math.sin(heading)

        # Escorregamento cresce onde há carga lateral (curvas lentas) e sob
        # frenagem forte — o suficiente para os detectores de travamento e
        # patinagem terem sinal real para achar.
        cornering = 1.0 - (speed_kmh / 240.0)
        slip_front = 1.0 + cornering * 0.06 + (brake / 100.0) * 0.05
        slip_rear = 1.0 + cornering * 0.04 + (throttle / 100.0) * 0.05

        fuel = max(0.0, fuel - speed_ms * (dt_ms / 1000.0) * 0.00018)
        tire_base = 78.0 + cornering * 22.0

        yield TelemetryFrame(
            speed_kmh=speed_kmh,
            rpm=rpm,
            gear=gear,
            suggested_gear=gear,
            throttle=throttle,
            brake=brake,
            fuel=fuel,
            fuel_capacity=60.0,
            lap_count=lap_number,
            total_laps=0,
            position_x=position_x,
            position_y=0.0,
            position_z=position_z,
            velocity_x=velocity_x,
            velocity_y=0.0,
            velocity_z=velocity_z,
            body_height=0.12,
            best_lap_ms=best_lap_ms,
            last_lap_ms=last_lap_ms,
            current_lap_ms=elapsed_ms,
            tire_temp_fl=tire_base + 3.0,
            tire_temp_fr=tire_base + 5.0,
            tire_temp_rl=tire_base,
            tire_temp_rr=tire_base + 2.0,
            suspension_fl=0.10,
            suspension_fr=0.11,
            suspension_rl=0.10,
            suspension_rr=0.11,
            tire_slip_fl=slip_front,
            tire_slip_fr=slip_front,
            tire_slip_rl=slip_rear,
            tire_slip_rr=slip_rear,
            turbo_boost=1.0 + (throttle / 100.0) * 0.6,
            oil_pressure=5.2,
            oil_temp=104.0,
            water_temp=88.0,
            rpm_flashing_min=7200,
            rpm_flashing_max=7800,
            max_speed_kmh=320,
            flags=FLAG_CAR_ON_TRACK | FLAG_IN_GEAR,
            car_id=car_id,
        )


def synthetic_session(
    lap_count: int = 3,
    *,
    base_lap_time_ms: int = 102_000,
    **lap_kwargs: object,
) -> Iterator[TelemetryFrame]:
    """Várias voltas seguidas, com o contador subindo e ritmo levemente variável.

    A variação de ritmo não é ruído: é o que faz "melhor volta" e "delta contra a
    anterior" terem o que comparar. A volta 2 é a mais rápida da sequência.
    """
    paces = (0.985, 1.0, 0.995, 0.99, 1.005)
    best_ms = -1
    last_ms = -1

    for lap_index in range(lap_count):
        pace = paces[lap_index % len(paces)]
        lap_time_ms = int(base_lap_time_ms / pace)

        yield from synthetic_lap(
            lap_number=lap_index + 1,
            lap_time_ms=lap_time_ms,
            pace_factor=pace,
            best_lap_ms=best_ms,
            last_lap_ms=last_ms,
            **lap_kwargs,  # type: ignore[arg-type]
        )

        last_ms = lap_time_ms
        best_ms = lap_time_ms if best_ms < 0 else min(best_ms, lap_time_ms)


class MockTelemetrySource(TelemetrySource):
    """Fonte sintética com ritmo real, para rodar a aplicação sem PS5.

    Envolve `synthetic_session` numa thread que respeita o intervalo entre
    amostras. Como emite o mesmo `TelemetryFrame` da fonte real, a aplicação
    montada em cima não distingue as duas.
    """

    def __init__(
        self,
        *,
        lap_count: int = 50,
        sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ,
        speed_multiplier: float = 1.0,
    ) -> None:
        super().__init__()
        self._lap_count = lap_count
        self._sample_rate_hz = sample_rate_hz
        # >1.0 acelera o tempo simulado. Uma sessão de 50 voltas a 20x roda em
        # minutos, o que torna viável um teste de carga sem esperar horas.
        self._speed_multiplier = max(0.01, speed_multiplier)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return  # idempotente, conforme o contrato
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="MockTelemetrySource", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None
        self._emit_status(ConnectionState.DISCONNECTED)

    def _run(self) -> None:
        self._emit_status(ConnectionState.CONNECTING)
        self._emit_status(ConnectionState.RECEIVING)

        interval = 1.0 / (self._sample_rate_hz * self._speed_multiplier)
        next_at = time.monotonic()

        for frame in synthetic_session(
            lap_count=self._lap_count, sample_rate_hz=self._sample_rate_hz
        ):
            if self._stop_event.is_set():
                break
            self._emit_frame(frame)

            # Agenda por horário absoluto, não `sleep(interval)`: o custo do
            # processamento de cada quadro não acumula atraso ao longo da volta.
            next_at += interval
            delay = next_at - time.monotonic()
            if delay > 0:
                self._stop_event.wait(delay)
            else:
                next_at = time.monotonic()
