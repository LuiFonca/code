"""
Testes do motor de telemetria: distância, força G e detecção de volta.

O teste de integração de distância é o mais importante do arquivo. A auditoria
registrou como P5 que a distância era integrada pela regra do retângulo, com
erro monotônico — e como delta, setores e comparação de voltas dependem todos
dela, esse erro contaminava o analytics inteiro. Aqui a correção é medida contra
a resposta analítica, não contra a implementação.
"""

from __future__ import annotations

import math

import pytest

from gt7core.events.bus import EventBus
from gt7core.telemetry.engine import (
    LapBoundaryDetected,
    TelemetryEngine,
    TelemetryReceived,
)
from gt7core.telemetry.protocol import FLAG_CAR_ON_TRACK, FLAG_PAUSED, TelemetryFrame
from gt7core.telemetry.sources.mock import synthetic_lap


def make_frame(
    *,
    speed_kmh: float = 200.0,
    packet_id: int = 0,
    lap_count: int = 1,
    last_lap_ms: int = -1,
    velocity_x: float = 0.0,
    velocity_z: float = 0.0,
    flags: int = FLAG_CAR_ON_TRACK,
) -> TelemetryFrame:
    """Quadro mínimo, com só o que cada teste precisa variar."""
    return TelemetryFrame(
        speed_kmh=speed_kmh, rpm=6000.0, gear=4, suggested_gear=4,
        throttle=80.0, brake=0.0, fuel=50.0, fuel_capacity=60.0,
        lap_count=lap_count, total_laps=10,
        position_x=0.0, position_y=0.0, position_z=0.0,
        velocity_x=velocity_x, velocity_y=0.0, velocity_z=velocity_z,
        body_height=0.1, best_lap_ms=-1, last_lap_ms=last_lap_ms,
        packet_id=packet_id, day_progression_ms=0,
        tire_temp_fl=80.0, tire_temp_fr=80.0, tire_temp_rl=80.0, tire_temp_rr=80.0,
        suspension_fl=0.1, suspension_fr=0.1, suspension_rl=0.1, suspension_rr=0.1,
        tire_slip_fl=1.0, tire_slip_fr=1.0, tire_slip_rl=1.0, tire_slip_rr=1.0,
        turbo_boost=1.0, oil_pressure=5.0, oil_temp=100.0, water_temp=90.0,
        rpm_flashing_min=7000, rpm_flashing_max=7500, max_speed_kmh=330,
        flags=flags, car_id=42,
    )


class TestIntegracaoDeDistancia:
    def test_velocidade_constante_da_distancia_exata(self, bus: EventBus) -> None:
        engine = TelemetryEngine(bus)
        speed_kmh, duration_s, hz = 180.0, 10.0, 60

        for i in range(int(duration_s * hz) + 1):
            engine.on_frame(
                make_frame(speed_kmh=speed_kmh, packet_id=i)
            )

        expected = (speed_kmh / 3.6) * duration_s
        assert engine.current_distance_m == pytest.approx(expected, rel=1e-9)

    def test_desaceleracao_linear_da_distancia_analitica(self, bus: EventBus) -> None:
        """Freada de 240 para 60 km/h: a regra do trapézio é exata para
        velocidade linear, então o resultado bate com (v0+v1)/2 × T."""
        # A taxa agora é do motor, não do quadro: é ela que converte tick em
        # tempo. O comentário anterior avisava que a 60 Hz o timestamp inteiro
        # truncava 16,666 ms e o total virava 3,999 s — isso deixou de valer,
        # porque o tempo derivado é float e só arredonda ao virar amostra.
        v0_kmh, v1_kmh, duration_s, hz = 240.0, 60.0, 4.0, 50
        engine = TelemetryEngine(bus, sample_rate_hz=hz)
        steps = int(duration_s * hz)

        for i in range(steps + 1):
            ratio = i / steps
            speed = v0_kmh + (v1_kmh - v0_kmh) * ratio
            engine.on_frame(
                make_frame(speed_kmh=speed, packet_id=i)
            )

        analytic = ((v0_kmh + v1_kmh) / 2 / 3.6) * duration_s
        assert engine.current_distance_m == pytest.approx(analytic, rel=1e-6)

    def test_trapezio_supera_retangulo_na_freada(self, bus: EventBus) -> None:
        """O erro do método antigo dentro de uma zona de frenagem.

        A regra do retângulo usava a velocidade da amostra atual para o
        intervalo já decorrido; numa desaceleração isso subestima. O erro por
        trecho monotônico é `(v_fim - v_ini) * dt / 2` — aqui, meio metro.

        Meio metro é pouco em termos absolutos e muito no lugar onde acontece:
        o delta é lido no ponto de freada, e é ali que o deslocamento cai.
        """
        v0_kmh, v1_kmh, duration_s, hz = 240.0, 60.0, 4.0, 50
        steps = int(duration_s * hz)
        dt = 1.0 / hz
        analytic = ((v0_kmh + v1_kmh) / 2 / 3.6) * duration_s

        engine = TelemetryEngine(EventBus(), sample_rate_hz=hz)
        rectangle = 0.0
        for i in range(steps + 1):
            speed = v0_kmh + (v1_kmh - v0_kmh) * (i / steps)
            engine.on_frame(make_frame(speed_kmh=speed, packet_id=i))
            if i > 0:
                rectangle += (speed / 3.6) * dt

        trapezoid_error = abs(engine.current_distance_m - analytic)
        rectangle_error = abs(rectangle - analytic)

        # (240-60)/3.6 * 0.02/2 = 0,50 m — o valor analítico do erro.
        assert trapezoid_error < 1e-6, "trapézio é exato para velocidade linear"
        assert rectangle_error == pytest.approx(0.5, abs=0.01)
        assert trapezoid_error < rectangle_error

    def test_erro_do_retangulo_cancela_na_volta_fechada(self) -> None:
        """Corrige uma afirmação errada da auditoria de Fase 0.

        O documento dizia que o erro do retângulo "acumula ao longo da volta".
        Não acumula: numa volta fechada, que termina na mesma velocidade em que
        começou, os erros de aceleração e desaceleração telescopam e se
        cancelam. Este teste fixa o comportamento real para que ninguém volte a
        justificar uma mudança com a premissa errada.
        """
        frames = list(synthetic_lap(lap_time_ms=102_000))
        rectangle = trapezoid = 0.0
        previous_speed = previous_ms = None

        for frame in frames:
            speed = frame.speed_kmh / 3.6
            if previous_speed is not None and frame.packet_id > previous_ms:
                dt = (frame.packet_id - previous_ms) / 1000
                rectangle += speed * dt
                trapezoid += (previous_speed + speed) / 2 * dt
            previous_speed, previous_ms = speed, frame.packet_id

        assert abs(rectangle - trapezoid) < 0.01, "sobre a volta fechada, se cancelam"

    def test_relogio_para_tras_nao_soma_distancia(self, bus: EventBus) -> None:
        """Um pacote fora de ordem não pode inflar a distância."""
        engine = TelemetryEngine(bus)
        engine.on_frame(make_frame(packet_id=60))
        engine.on_frame(make_frame(packet_id=120))
        distance_before = engine.current_distance_m

        engine.on_frame(make_frame(packet_id=30))
        assert engine.current_distance_m == distance_before

    def test_pausa_nao_acumula_distancia(self, bus: EventBus) -> None:
        """§38: com o jogo pausado o tempo não corre — acumular aqui inflaria a
        distância e distorceria o delta."""
        engine = TelemetryEngine(bus)
        for i in range(60):
            engine.on_frame(make_frame(packet_id=i))
        distance_before = engine.current_distance_m
        samples_before = engine.buffered_points

        for i in range(60, 200):
            engine.on_frame(
                make_frame(
                    packet_id=i, flags=FLAG_CAR_ON_TRACK | FLAG_PAUSED
                )
            )

        assert engine.current_distance_m == distance_before
        assert engine.buffered_points == samples_before


class TestForcaG:
    def test_frenagem_produz_g_longitudinal_negativo(self, bus: EventBus) -> None:
        """50 → 40 m/s em 0,5 s = -2,04 g. É o número que o README afirmava ter
        verificado; agora é executável."""
        engine = TelemetryEngine(bus)
        received: list[TelemetryReceived] = []
        bus.subscribe(TelemetryReceived, received.append)

        engine.on_frame(
            make_frame(speed_kmh=180.0, packet_id=0, velocity_x=50.0, velocity_z=0.0)
        )
        engine.on_frame(
            make_frame(speed_kmh=144.0, packet_id=30, velocity_x=40.0, velocity_z=0.0)
        )

        assert received[-1].point.g_longitudinal == pytest.approx(-2.04, abs=0.01)

    def test_g_e_projetado_nos_eixos_do_carro(self, bus: EventBus) -> None:
        """Mesma frenagem, carro apontando para outra direção: o resultado tem
        de ser idêntico. Sem a projeção, seria aceleração no referencial do
        mundo e mudaria de significado a cada curva."""
        engine = TelemetryEngine(bus)
        received: list[TelemetryReceived] = []
        bus.subscribe(TelemetryReceived, received.append)

        angle = math.radians(37.0)
        # 30 ticks a 60 Hz = 0,5 s — o mesmo intervalo do teste anterior, agora
        # dito na unidade que o pacote carrega.
        for speed_ms, tick in ((50.0, 0), (40.0, 30)):
            engine.on_frame(
                make_frame(
                    speed_kmh=speed_ms * 3.6,
                    packet_id=tick,
                    velocity_x=speed_ms * math.cos(angle),
                    velocity_z=speed_ms * math.sin(angle),
                )
            )

        assert received[-1].point.g_longitudinal == pytest.approx(-2.04, abs=0.01)
        assert received[-1].point.g_lateral == pytest.approx(0.0, abs=0.01)

    def test_carro_parado_nao_gera_g_absurdo(self, bus: EventBus) -> None:
        """Abaixo do limiar de velocidade a derivada é ruído numérico."""
        engine = TelemetryEngine(bus)
        received: list[TelemetryReceived] = []
        bus.subscribe(TelemetryReceived, received.append)

        engine.on_frame(make_frame(speed_kmh=0.5, packet_id=0, velocity_x=0.1))
        engine.on_frame(make_frame(speed_kmh=0.5, packet_id=0, velocity_x=0.2))

        assert received[-1].point.g_longitudinal == 0.0
        assert received[-1].point.g_lateral == 0.0


class TestDeteccaoDeVolta:
    def test_virada_de_contador_fecha_a_volta(self, bus: EventBus) -> None:
        engine = TelemetryEngine(bus)
        completed: list[LapBoundaryDetected] = []
        bus.subscribe(LapBoundaryDetected, completed.append)

        # 100 quadros a 60 Hz = 1,67 s. O tempo declarado precisa bater com
        # isso: o motor agora descarta volta cuja distância contradiz
        # velocidade × tempo, e a versão anterior deste teste declarava 95 s
        # para 1,67 s de dados — impossível, e aceito em silêncio justamente
        # como o defeito que a guarda passou a pegar.
        for i in range(100):
            engine.on_frame(make_frame(lap_count=1, packet_id=i))
        engine.on_frame(make_frame(lap_count=2, packet_id=0, last_lap_ms=1_667))

        assert len(completed) == 1
        assert completed[0].lap_number == 1
        assert completed[0].lap_time_ms == 1_667
        assert len(completed[0].points) == 100

    def test_volta_sem_tempo_valido_e_descartada(self, bus: EventBus) -> None:
        """O GT7 manda -1 antes da primeira volta completa."""
        engine = TelemetryEngine(bus)
        completed: list[LapBoundaryDetected] = []
        bus.subscribe(LapBoundaryDetected, completed.append)

        for i in range(50):
            engine.on_frame(make_frame(lap_count=1, packet_id=i))
        engine.on_frame(make_frame(lap_count=2, packet_id=0, last_lap_ms=-1))

        assert completed == []
        assert engine.buffered_points <= 1  # estado limpo para a próxima

    def test_buffer_zera_entre_voltas(self, bus: EventBus) -> None:
        """Sem isto, a volta 2 herdaria as amostras da volta 1 — o vazamento de
        memória e de dados que o §34 pede para evitar."""
        engine = TelemetryEngine(bus)
        completed: list[LapBoundaryDetected] = []
        bus.subscribe(LapBoundaryDetected, completed.append)

        for lap in (1, 2, 3):
            # 40 quadros a 60 Hz = 667 ms — o tempo declarado acompanha,
            # para a volta ser fisicamente possível.
            for i in range(40):
                engine.on_frame(make_frame(lap_count=lap, packet_id=i))
            engine.on_frame(
                make_frame(lap_count=lap + 1, packet_id=0, last_lap_ms=667)
            )

        counts = [len(event.points) for event in completed]

        # 41 e não 40 a partir da segunda: o quadro que vira o contador já
        # pertence à volta nova e é bufferizado depois do fechamento. O que
        # importa é que não cresça (40, 80, 120) — isso seria o vazamento.
        assert counts == [40, 41, 41]


class TestPipelineComFonteSintetica:
    """O pipeline inteiro sobre a fonte mock — sem PS5, sem rede, sem Qt."""

    def test_volta_sintetica_atravessa_o_motor(self, bus: EventBus) -> None:
        engine = TelemetryEngine(bus)
        received: list[TelemetryReceived] = []
        bus.subscribe(TelemetryReceived, received.append)

        for frame in synthetic_lap(lap_time_ms=102_000):
            engine.on_frame(frame)

        assert len(received) == 6120  # 102 s a 60 Hz

        # A distância integrada tem de ficar perto do comprimento configurado da
        # pista: é o que valida a integração de ponta a ponta, não só num trecho.
        # 3800 m é o comprimento configurado; a integração fecha com ele.
        assert engine.current_distance_m == pytest.approx(3800, rel=0.01)

        # A escala de velocidade é derivada de comprimento/tempo, então o que
        # se verifica é a faixa dinâmica (retas vs curvas), não valores fixos.
        speeds = [event.point.speed_kmh for event in received]
        assert max(speeds) / min(speeds) > 2.5, "retas e curvas têm de diferir"
        assert 100 < sum(speeds) / len(speeds) < 200

        # Força G plausível: nenhum valor de carro de Fórmula 1 num carro de rua.
        g_values = [abs(event.point.g_longitudinal) for event in received]
        assert max(g_values) < 3.0

    def test_sessao_de_tres_voltas_fecha_tres(self, bus: EventBus) -> None:
        from gt7core.telemetry.sources.mock import synthetic_session

        engine = TelemetryEngine(bus)
        completed: list[LapBoundaryDetected] = []
        bus.subscribe(LapBoundaryDetected, completed.append)

        for frame in synthetic_session(lap_count=3):
            engine.on_frame(frame)

        # A última volta só fecha quando o contador vira de novo — por isso 2.
        assert len(completed) == 2
        assert all(event.lap_time_ms > 0 for event in completed)
        assert all(len(event.points) > 5000 for event in completed)


class TestPausaNaoContaTempo:
    """O tick do GT7 corre com o jogo pausado — o relógio da volta não pode.

    `packet_id` é contador de **quadros do jogo**, e o jogo continua produzindo
    quadros na tela de pausa. O motor já descartava esses quadros (não vira
    amostra), mas o tempo decorrido é derivado do tick, então despausar fazia o
    relógio saltar o tamanho da pausa de uma vez: o delta ao vivo dava um pulo
    que não veio de pilotagem nenhuma, e a distância integrada herdava o erro.
    """

    def _quadros(self, motor, inicio: int, quantos: int, *, pausado: bool):  # noqa: ANN001, ANN202
        flags = FLAG_CAR_ON_TRACK | (FLAG_PAUSED if pausado else 0)
        for i in range(quantos):
            motor.on_frame(
                make_frame(
                    packet_id=inicio + i,
                    lap_count=1,
                    speed_kmh=180.0,
                    flags=flags,
                )
            )

    def test_o_tempo_ignora_os_quadros_pausados(self) -> None:
        bus = EventBus()
        motor = TelemetryEngine(bus, sample_rate_hz=60)

        self._quadros(motor, 0, 60, pausado=False)      # 1 s rodando
        self._quadros(motor, 60, 600, pausado=True)     # 10 s pausado
        self._quadros(motor, 660, 60, pausado=False)    # mais 1 s rodando

        pontos = list(motor._buffer)  # noqa: SLF001
        assert pontos, "nenhuma amostra sobreviveu"
        # Dois segundos de pilotagem, não doze.
        assert pontos[-1].elapsed_ms == pytest.approx(2000, abs=40)

    def test_sem_pausa_o_tempo_e_o_de_sempre(self) -> None:
        """A correção não pode encurtar uma volta que nunca pausou."""
        bus = EventBus()
        motor = TelemetryEngine(bus, sample_rate_hz=60)

        self._quadros(motor, 0, 120, pausado=False)

        pontos = list(motor._buffer)  # noqa: SLF001
        assert pontos[-1].elapsed_ms == pytest.approx(1983, abs=40)
