"""
Fase 9 — detecção na volta em andamento.

A propriedade mais valiosa aqui é a **concordância com o debrief**. Se o rádio
anuncia um travamento e a análise da mesma volta não lista nenhum, o piloto
deixa de confiar nos dois — e nenhum teste de unidade isolado pegaria isso,
porque cada lado, sozinho, estaria certo. Por isso os dois detectores são
rodados sobre a mesma volta e comparados.

As outras duas propriedades vêm da restrição de onde o código roda: 60 Hz na
thread de captura, ao lado da gravação. Custo por amostra tem de ser constante,
e o detector tem de calar a boca quando não sabe.
"""

from __future__ import annotations

import time
from collections import Counter

import pytest

from gt7core.analytics.live import (
    CONVENTION_WARMUP_SAMPLES,
    DELTA_WARN_MS,
    REARM_MS,
    LiveEventDetector,
)
from gt7core.analytics.tyres import SlipConvention, detect_tyre_events
from gt7core.domain.models import TelemetryPoint
from gt7core.events.bus import EventBus
from gt7core.telemetry.engine import TelemetryEngine, TelemetryReceived
from gt7core.telemetry.sources.mock import synthetic_lap


def build_lap(**kwargs: object) -> list[TelemetryPoint]:
    bus = EventBus()
    engine = TelemetryEngine(bus)
    points: list[TelemetryPoint] = []
    bus.subscribe(TelemetryReceived, lambda e: points.append(e.point))
    for frame in synthetic_lap(**kwargs):  # type: ignore[arg-type]
        engine.on_frame(frame)
    return points


def run(points: list[TelemetryPoint]) -> list:  # noqa: ANN201
    detector = LiveEventDetector()
    found = []
    for point in points:
        found.extend(detector.feed(point))
    return found


@pytest.fixture(scope="module")
def lap() -> list[TelemetryPoint]:
    return build_lap(lap_time_ms=102_000)


def make_point(**overrides: object) -> TelemetryPoint:
    defaults: dict[str, object] = {
        "elapsed_ms": 0,
        "distance_m": 0.0,
        "speed_kmh": 150.0,
        "rpm": 5000.0,
        "gear": 4,
        "throttle": 0.0,
        "brake": 0.0,
        "fuel_level": 50.0,
        "tire_temp_fl": 80.0,
        "tire_temp_fr": 80.0,
        "tire_temp_rl": 80.0,
        "tire_temp_rr": 80.0,
        "position_x": 0.0,
        "position_z": 0.0,
        "g_lateral": 0.0,
        "g_longitudinal": 0.0,
        "suspension_fl": 0.1,
        "suspension_fr": 0.1,
        "suspension_rl": 0.1,
        "suspension_rr": 0.1,
        "tire_slip_fl": 1.0,
        "tire_slip_fr": 1.0,
        "tire_slip_rl": 1.0,
        "tire_slip_rr": 1.0,
        "turbo_boost": 1.0,
        "oil_temp": 100.0,
        "water_temp": 88.0,
    }
    defaults.update(overrides)
    return TelemetryPoint(**defaults)  # type: ignore[arg-type]


def warm(detector: LiveEventDetector, **overrides: object) -> None:
    """Roda o aquecimento da convenção com amostras limpas."""
    for i in range(CONVENTION_WARMUP_SAMPLES + 1):
        detector.feed(make_point(elapsed_ms=i * 16, **overrides))


class TestConcordanciaComODebrief:
    """O rádio e o relatório não podem se contradizer sobre a mesma volta."""

    def test_o_numero_de_incidentes_bate(self, lap: list[TelemetryPoint]) -> None:
        ao_vivo = Counter(e.kind for e in run(lap))

        # O detector pós-volta é **por roda** de propósito: travar só a
        # dianteira esquerda é outro diagnóstico. O rádio fala de incidentes,
        # então cada incidente aparece uma vez ali e duas aqui (as duas rodas
        # do eixo). A conta tem de fechar exatamente.
        por_roda = Counter(e.kind for e in detect_tyre_events(lap))
        incidentes = {
            kind: len({(e.start_distance_m, e.end_distance_m)
                       for e in detect_tyre_events(lap) if e.kind == kind})
            for kind in por_roda
        }

        assert ao_vivo["travamento"] == incidentes["travamento"]
        assert ao_vivo["patinagem"] == incidentes["patinagem"]

    def test_usa_os_mesmos_limiares(self) -> None:
        """Importados, não recopiados: mudar um muda os dois lados juntos."""
        from gt7core.analytics import live, tyres

        assert live.LOCKUP_RATIO is tyres.LOCKUP_RATIO
        assert live.WHEELSPIN_RATIO is tyres.WHEELSPIN_RATIO
        assert live.MIN_EVENT_DURATION_MS is tyres.MIN_EVENT_DURATION_MS

    def test_a_convencao_bate_com_a_da_volta_inteira(
        self, lap: list[TelemetryPoint]
    ) -> None:
        from gt7core.analytics.tyres import infer_slip_convention

        detector = LiveEventDetector()
        for point in lap:
            detector.feed(point)
        assert detector.convention == infer_slip_convention(lap)


class TestSilencioQuandoNaoSabe:
    def test_nao_emite_durante_o_aquecimento(self) -> None:
        """Ler razão como m/s encheria a volta de eventos falsos.

        Alguns segundos de silêncio custam menos que uma volta inteira de ruído.
        """
        detector = LiveEventDetector()
        for i in range(CONVENTION_WARMUP_SAMPLES - 1):
            # Amostras que **seriam** travamento se a convenção já existisse.
            events = detector.feed(
                make_point(elapsed_ms=i * 16, brake=100.0, tire_slip_fl=0.5,
                           tire_slip_fr=0.5)
            )
            assert events == []
        assert not detector.is_ready

    def test_decide_a_convencao_e_passa_a_emitir(self) -> None:
        detector = LiveEventDetector()
        warm(detector)
        assert detector.is_ready
        assert detector.convention is SlipConvention.RATIO

    def test_carro_parado_nao_conta_para_a_convencao(self) -> None:
        """Abaixo do limiar de velocidade o canal não significa nada."""
        detector = LiveEventDetector()
        for i in range(CONVENTION_WARMUP_SAMPLES * 2):
            detector.feed(make_point(elapsed_ms=i * 16, speed_kmh=5.0))
        assert not detector.is_ready


class TestEventos:
    def _lockup_sequence(self, detector: LiveEventDetector, *, start_ms: int,
                         duration_ms: int) -> list:  # noqa: ANN202
        found = []
        step = 16
        for offset in range(0, duration_ms, step):
            found.extend(detector.feed(make_point(
                elapsed_ms=start_ms + offset, distance_m=float(offset),
                brake=100.0, tire_slip_fl=0.5, tire_slip_fr=0.5)))
        # Solta o freio: é o fechamento que emite.
        found.extend(detector.feed(make_point(
            elapsed_ms=start_ms + duration_ms, distance_m=float(duration_ms))))
        return found

    def test_travamento_longo_e_um_evento_so(self) -> None:
        """Segurar o freio travado por dois segundos não são trinta avisos."""
        detector = LiveEventDetector()
        warm(detector)
        found = self._lockup_sequence(detector, start_ms=10_000, duration_ms=2000)
        assert len(found) == 1
        assert found[0].kind == "travamento"

    def test_repique_curto_e_ignorado(self) -> None:
        """Abaixo do limiar de duração é ruído de leitura, não travamento."""
        detector = LiveEventDetector()
        warm(detector)
        found = self._lockup_sequence(detector, start_ms=10_000, duration_ms=32)
        assert found == []

    def test_rearme_evita_metralhadora(self) -> None:
        detector = LiveEventDetector()
        warm(detector)
        primeiro = self._lockup_sequence(detector, start_ms=10_000, duration_ms=400)
        logo_depois = self._lockup_sequence(detector, start_ms=10_500, duration_ms=400)
        bem_depois = self._lockup_sequence(
            detector, start_ms=10_500 + REARM_MS * 2, duration_ms=400
        )
        assert len(primeiro) == 1
        assert logo_depois == [], "disparou de novo antes do rearme"
        assert len(bem_depois) == 1

    def test_alivio_de_acelerador(self) -> None:
        detector = LiveEventDetector()
        warm(detector)
        detector.feed(make_point(elapsed_ms=20_000, throttle=90.0))
        found = detector.feed(make_point(elapsed_ms=20_100, throttle=40.0))
        assert [e.kind for e in found] == ["alivio"]
        assert "90%" in found[0].detail

    def test_soltar_o_pe_para_frear_nao_e_alivio(self) -> None:
        detector = LiveEventDetector()
        warm(detector)
        detector.feed(make_point(elapsed_ms=20_000, throttle=95.0))
        detector.feed(make_point(elapsed_ms=20_050, throttle=0.0, brake=80.0))
        found = detector.feed(make_point(elapsed_ms=20_100, throttle=10.0))
        assert found == []

    def test_alivio_a_partir_de_pouco_acelerador_nao_conta(self) -> None:
        """Sair de 30% é transição normal de curva, não erro de saída."""
        detector = LiveEventDetector()
        warm(detector)
        detector.feed(make_point(elapsed_ms=20_000, throttle=30.0))
        assert detector.feed(make_point(elapsed_ms=20_100, throttle=5.0)) == []


class TestDelta:
    def test_delta_crescendo_vira_evento(self) -> None:
        detector = LiveEventDetector()
        point = make_point(elapsed_ms=30_000, distance_m=1500.0)
        assert detector.feed_delta(0.0, point) is None, "primeira leitura é a base"

        later = make_point(elapsed_ms=32_000, distance_m=1800.0)
        event = detector.feed_delta(DELTA_WARN_MS + 50, later)
        assert event is not None
        assert event.kind == "perdendo"

    def test_delta_estavel_nao_avisa(self) -> None:
        detector = LiveEventDetector()
        detector.feed_delta(0.0, make_point(elapsed_ms=30_000))
        assert detector.feed_delta(10.0, make_point(elapsed_ms=32_000)) is None

    def test_delta_melhorando_nao_avisa(self) -> None:
        detector = LiveEventDetector()
        detector.feed_delta(500.0, make_point(elapsed_ms=30_000))
        assert detector.feed_delta(100.0, make_point(elapsed_ms=32_000)) is None


class TestCicloDeVida:
    def test_nova_volta_preserva_a_convencao(self) -> None:
        """Reaprender a cada volta traria o silêncio inicial toda vez."""
        detector = LiveEventDetector()
        warm(detector)
        assert detector.is_ready
        detector.new_lap()
        assert detector.is_ready

    def test_nova_sessao_esquece_tudo(self) -> None:
        """O piloto pode ter trocado de carro, e a convenção é do carro."""
        detector = LiveEventDetector()
        warm(detector)
        detector.reset()
        assert not detector.is_ready

    def test_nova_volta_fecha_evento_em_andamento(self) -> None:
        """Sem isso, um travamento na linha de chegada vazaria para a volta seguinte."""
        detector = LiveEventDetector()
        warm(detector)
        for offset in range(0, 500, 16):
            detector.feed(make_point(elapsed_ms=40_000 + offset, brake=100.0,
                                     tire_slip_fl=0.5, tire_slip_fr=0.5))
        detector.new_lap()
        assert detector.feed(make_point(elapsed_ms=41_000)) == []


class TestCustoConstante:
    """60 Hz na thread de captura: nada aqui pode varrer histórico."""

    def test_o_custo_por_amostra_nao_cresce_com_a_volta(self) -> None:
        detector = LiveEventDetector()
        warm(detector)

        def medir(inicio: int, quantas: int = 400) -> float:
            t0 = time.perf_counter()
            for i in range(quantas):
                detector.feed(make_point(elapsed_ms=(inicio + i) * 16,
                                         distance_m=float(inicio + i)))
            return (time.perf_counter() - t0) / quantas

        cedo = medir(1_000)
        for bloco in range(2, 12):
            medir(bloco * 1_000)
        tarde = medir(20_000)

        # Quadrático faria o custo tardio explodir. O limite é generoso para não
        # ficar sensível a máquina ocupada e ainda assim pega a regressão.
        assert tarde < max(cedo * 5, 50e-6), (
            f"cedo {cedo * 1e6:.1f} µs, tarde {tarde * 1e6:.1f} µs"
        )

    def test_uma_volta_inteira_cabe_no_orcamento_de_60_hz(
        self, lap: list[TelemetryPoint]
    ) -> None:
        detector = LiveEventDetector()
        t0 = time.perf_counter()
        for point in lap:
            detector.feed(point)
        media = (time.perf_counter() - t0) / len(lap)

        # 16,7 ms é o quadro inteiro a 60 Hz, e o detector divide isso com o
        # motor, a gravação e o delta.
        assert media < 1e-3, f"{media * 1e6:.0f} µs por amostra"
