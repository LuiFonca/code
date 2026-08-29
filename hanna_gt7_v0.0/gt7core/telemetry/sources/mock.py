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

from ...observability.logging import get_logger
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
_log = get_logger(__name__)

DEFAULT_CAR_ID = 2001

# Aceleração abaixo da qual o piloto sintético está de inércia — nem freio nem
# acelerador. Em m/s².
COAST_ACCEL_MS2 = 0.35

#: Meia altura da colina sintética, em metros — o desnível da volta é o dobro.
#: 18 m dão rampas de até ~3%, na faixa de um circuito de verdade: o suficiente
#: para a correção de gravidade mudar a freada de forma visível, e longe do
#: absurdo que faria o gráfico parecer uma montanha-russa.
ELEVATION_AMPLITUDE_M = 18.0


def _target_speed(lap_fraction: float) -> float:
    """Velocidade alvo na fração informada da volta.

    A interpolação é **smoothstep**, não linear, e isso não é cosmético: com
    interpolação linear a velocidade é uma poligonal, a aceleração é constante
    dentro de cada trecho, e os pedais — que o gerador deriva da aceleração —
    saem como platôs perfeitamente retangulares. O resultado é um piloto
    sintético que pisa no freio de uma vez, mantém pressão constante e solta de
    uma vez: `trail_braking_ratio` zero em toda freada, throttle que nunca chega
    a fundo.

    Isso tornaria o mock inútil justamente para a análise da Fase 4. Com
    smoothstep a aceleração varia continuamente (zero nos extremos de cada
    trecho, máxima no meio), que é a forma que um piloto de verdade produz:
    pressão que sobe rápido e alivia progressivamente até o ápice.
    """
    fraction = lap_fraction % 1.0
    previous_f, previous_v = _SPEED_PROFILE[0]
    for point_f, point_v in _SPEED_PROFILE[1:]:
        if fraction <= point_f:
            span = point_f - previous_f
            if span <= 0:
                return point_v
            ratio = (fraction - previous_f) / span
            eased = ratio * ratio * (3.0 - 2.0 * ratio)
            return previous_v + eased * (point_v - previous_v)
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
    start_packet_id: int = 0,
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
    throttle_hold = 0.0

    for index in range(frame_count):
        elapsed_ms = int(index * dt_ms)
        fraction = index / frame_count

        speed_kmh = max(20.0, _target_speed(fraction) * pace_factor * speed_scale)
        speed_ms = speed_kmh / 3.6

        # Pedais derivados da variação de velocidade: acelerando → throttle,
        # desacelerando → brake. É o que produz zonas de frenagem coerentes com
        # o perfil, em vez de valores inventados.
        #
        # A faixa morta reproduz a **fase de inércia** entre soltar o freio e
        # abrir o acelerador. Sem ela os dois pedais se tocam: o acelerador já
        # estaria aberto no ápice, e a distância entre ápice e retomada — que é
        # o que o §14 mede — daria zero em toda curva.
        accel = (speed_ms - previous_speed_ms) / (dt_ms / 1000.0)
        previous_speed_ms = speed_ms
        # A catraca do acelerador corrige uma inversão de causalidade. Derivar o
        # pedal da aceleração é razoável na frenagem, mas errado na retomada: na
        # realidade o acelerador é a **entrada** e a aceleração é a resposta, e é
        # o arrasto que faz a aceleração cair enquanto o pé segue no fundo. Sem a
        # catraca, o gerador produzia um pedal que subia e descia a cada trecho
        # do perfil, e os detectores liam isso — corretamente — como dez alívios
        # por saída de curva.
        if accel > COAST_ACCEL_MS2:
            throttle = max(throttle_hold, min(100.0, 12.0 + accel * 30.0))
            throttle_hold = throttle
            brake = 0.0
        elif accel < -COAST_ACCEL_MS2:
            throttle = 0.0
            throttle_hold = 0.0
            brake = min(100.0, -accel * 22.0)
        else:
            # Inércia. O pedal **não** volta a zero aqui: numa reta longa a
            # aceleração cai a quase nada com o pé no fundo, e zerar o acelerador
            # nesse ponto inventaria um alívio que o piloto não fez. Quem zera a
            # catraca é a frenagem — e é isso que cria a fase de inércia de
            # verdade, entre soltar o freio e voltar a acelerar.
            throttle = throttle_hold
            brake = 0.0

        gear = _gear_for_speed(speed_kmh)
        rpm = 2200.0 + (speed_kmh / max(1, gear)) * 52.0

        # Traçado: lemniscata de Gerono, escalada para o comprimento da pista.
        theta = fraction * 2.0 * math.pi
        radius = track_length_m / (2.0 * math.pi)
        position_x = radius * math.sin(theta)
        position_z = radius * math.sin(theta) * math.cos(theta)

        # Relevo: uma colina por volta, com o ponto alto no meio do traçado.
        # Não é enfeite — sem subida e descida o gerador não exercita a correção
        # de gravidade da força G nem o perfil de elevação, e um caminho que
        # nunca roda é um caminho que ninguém sabe se funciona.
        position_y = ELEVATION_AMPLITUDE_M * (1.0 - math.cos(theta))

        # Rumo: a **tangente de verdade** do traçado, derivando a lemniscata.
        #
        #     dx/dθ = r·cos θ        dz/dθ = r·cos 2θ
        #
        # Aqui havia `heading = theta + π/2`, que é a tangente de um círculo e
        # não desta curva: o vetor velocidade apontava para um lado e o carro
        # desenhava para outro. Passava despercebido porque força G e mapa nunca
        # eram conferidos um contra o outro — até a rampa medida discordar da
        # rampa construída (2,50% contra 2,98%) e denunciar a diferença.
        tangente_x = math.cos(theta)
        tangente_z = math.cos(2.0 * theta)
        norma_tangente = math.hypot(tangente_x, tangente_z) or 1.0
        rumo_x = tangente_x / norma_tangente
        rumo_z = tangente_z / norma_tangente

        # Rampa: altitude por **distância percorrida**, e não por θ. O passo de
        # θ cobre pedaços de pista de comprimentos diferentes ao longo da
        # lemniscata, e ignorar isso produziria rampa demais nos trechos curtos.
        d_percurso_d_theta = radius * norma_tangente
        rampa = (
            ELEVATION_AMPLITUDE_M * math.sin(theta) / max(d_percurso_d_theta, 1e-6)
        )

        # A rampa reparte a velocidade entre o plano e a vertical: o carro anda
        # ao longo do asfalto, não do mapa visto de cima.
        cos_rampa = 1.0 / math.hypot(1.0, rampa)
        velocity_x = speed_ms * rumo_x * cos_rampa
        velocity_z = speed_ms * rumo_z * cos_rampa
        velocity_y = speed_ms * rampa * cos_rampa

        # Normal unitária do asfalto: perpendicular à rampa, na direção da
        # marcha. Sem sobrelevação — a lemniscata é uma pista plana de lado.
        normal_x = -rampa * rumo_x * cos_rampa
        normal_z = -rampa * rumo_z * cos_rampa
        normal_y = cos_rampa

        # Escorregamento: o campo `tire_slip_*` do pacote GT7 é a **velocidade
        # da superfície do pneu em m/s**, não uma razão adimensional (ver
        # `analytics/tyres.py`, que trata a ambiguidade num lugar só). O gerador
        # emite m/s para que a análise de pneus rode aqui exatamente como roda
        # com um PS5 — que é a razão de o mock existir.
        #
        # A razão é construída primeiro, porque é nela que o comportamento é
        # legível: sob freio forte a roda gira mais devagar que o carro
        # (travamento), sob acelerador a traseira gira mais rápido (patinagem).
        cornering = 1.0 - (speed_kmh / 240.0)
        # Os coeficientes são calibrados para que a patinagem apareça só na saída
        # das curvas lentas — onde o carro de fato não aceita o acelerador — e
        # não na saída de qualquer curva. Um gerador que patina o tempo todo não
        # testa o detector, só o satura.
        # O quadrado da pressão faz o travamento aparecer só nas freadas de
        # pressão máxima, não em toda frenagem: é a diferença entre um detector
        # exercitado e um detector saturado.
        front_ratio = 1.0 - (brake / 100.0) ** 2 * 0.11 + cornering * 0.01
        rear_ratio = 1.0 + (throttle / 100.0) * 0.07 + cornering * 0.035
        slip_front = front_ratio * speed_ms
        slip_rear = rear_ratio * speed_ms

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
            position_y=position_y,
            position_z=position_z,
            velocity_x=velocity_x,
            velocity_y=velocity_y,
            velocity_z=velocity_z,
            road_plane_x=normal_x,
            road_plane_y=normal_y,
            road_plane_z=normal_z,
            body_height=0.12,
            best_lap_ms=best_lap_ms,
            last_lap_ms=last_lap_ms,
            # O tick é global e monotônico, como no console: é dele que o motor
            # deriva o tempo da volta. Reiniciá-lo a cada volta esconderia um
            # defeito real — o motor precisa saber achar o início da volta
            # sozinho, e um contador que já vem zerado faria isso de graça.
            packet_id=start_packet_id + index,
            day_progression_ms=elapsed_ms,
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
    packet_id = 0

    for lap_index in range(lap_count):
        pace = paces[lap_index % len(paces)]
        lap_time_ms = int(base_lap_time_ms / pace)

        emitted = 0
        for frame in synthetic_lap(
            lap_number=lap_index + 1,
            lap_time_ms=lap_time_ms,
            pace_factor=pace,
            best_lap_ms=best_ms,
            last_lap_ms=last_ms,
            start_packet_id=packet_id,
            **lap_kwargs,  # type: ignore[arg-type]
        ):
            emitted += 1
            yield frame

        packet_id += emitted
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
            if thread.is_alive():
                # Órfã: `join` expirou e a thread continua publicando quadros
                # num grafo que quem chamou `stop()` considera desligado. Antes
                # isto passava despercebido porque um quadro atrasado só
                # engordava uma lista; virou visível quando um assinante passou
                # a tocar recursos que `close()` já havia liberado.
                #
                # Não dá para matar uma thread em Python, então o que se pode
                # fazer é não mentir sobre o estado: avisa e **mantém** a
                # referência, para que `is_running` continue dizendo a verdade.
                _log.warning(
                    "a thread da fonte sintética não parou em 2 s; segue viva"
                )
                self._emit_status(ConnectionState.DISCONNECTED)
                return
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
