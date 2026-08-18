"""
Testes do delta e da consulta de canais.

O alinhamento por **distância** (e não por tempo) é a decisão de engenharia que
a auditoria classificou como correta e não óbvia: comparando por tempo, um
trecho onde o piloto freia mais cedo desalinha toda a comparação dali em diante.
Os testes abaixo fixam essa propriedade para que ninguém "simplifique" para
tempo depois.
"""

from __future__ import annotations

import pytest

from gt7core.analytics.delta import LapComparator
from gt7core.analytics.series import LapSeries
from gt7core.domain.models import TelemetryPoint


def make_point(distance_m: float, elapsed_ms: int, **overrides: float) -> TelemetryPoint:
    values: dict[str, float] = dict(
        speed_kmh=150.0, rpm=6000.0, gear=4, throttle=80.0, brake=0.0,
        fuel_level=50.0, tire_temp_fl=80.0, tire_temp_fr=80.0,
        tire_temp_rl=80.0, tire_temp_rr=80.0, position_x=0.0, position_z=0.0,
        g_lateral=0.0, g_longitudinal=0.0,
        suspension_fl=0.1, suspension_fr=0.1, suspension_rl=0.1, suspension_rr=0.1,
        tire_slip_fl=1.0, tire_slip_fr=1.0, tire_slip_rl=1.0, tire_slip_rr=1.0,
        turbo_boost=1.0, oil_temp=100.0, water_temp=90.0,
    )
    values.update(overrides)
    return TelemetryPoint(
        elapsed_ms=elapsed_ms, distance_m=distance_m, **values  # type: ignore[arg-type]
    )


def reference_lap(total_ms: int = 100_000, length_m: float = 4000.0, steps: int = 400):
    """Volta de referência a ritmo constante."""
    return [
        make_point(length_m * i / steps, int(total_ms * i / steps))
        for i in range(steps + 1)
    ]


class TestDelta:
    def test_sem_referencia_devolve_none(self) -> None:
        assert LapComparator([]).delta_ms_at(100.0, 5_000) is None
        assert LapComparator([]).has_reference is False

    def test_ritmo_identico_da_delta_zero(self) -> None:
        reference = reference_lap()
        comparator = LapComparator(reference)

        assert comparator.delta_ms_at(2000.0, 50_000) == pytest.approx(0.0, abs=1.0)

    def test_volta_mais_lenta_da_delta_positivo(self) -> None:
        """Positivo = mais devagar que a referência neste ponto."""
        comparator = LapComparator(reference_lap())

        # Na metade da pista a referência marca 50 s; chegar com 53 s = +3 s.
        assert comparator.delta_ms_at(2000.0, 53_000) == pytest.approx(3000.0, abs=1.0)

    def test_volta_mais_rapida_da_delta_negativo(self) -> None:
        comparator = LapComparator(reference_lap())

        assert comparator.delta_ms_at(2000.0, 47_500) == pytest.approx(-2500.0, abs=1.0)

    def test_alem_da_referencia_devolve_none(self) -> None:
        """Sem isto, o delta inventaria número depois do fim da volta gravada."""
        comparator = LapComparator(reference_lap(length_m=4000.0))

        assert comparator.delta_ms_at(5000.0, 120_000) is None

    def test_interpola_entre_amostras(self) -> None:
        """A distância consultada quase nunca cai exatamente numa amostra."""
        points = [make_point(0.0, 0), make_point(100.0, 4_000)]
        comparator = LapComparator(points)

        # A 25 m a referência estaria em 1000 ms; chegar com 1000 ms = delta 0.
        assert comparator.delta_ms_at(25.0, 1_000) == pytest.approx(0.0, abs=1.0)

    def test_alinha_por_distancia_e_nao_por_tempo(self) -> None:
        """A propriedade que justifica o desenho todo.

        Duas voltas com o **mesmo tempo total** mas perfis diferentes: uma
        acelera cedo, a outra tarde. Comparadas por tempo pareceriam idênticas
        no fim; por distância, o delta no meio revela quem estava na frente.
        """
        # Referência: ritmo constante, 100 s para 4000 m.
        comparator = LapComparator(reference_lap())

        # Piloto que abriu vantagem no primeiro setor: aos 1000 m já tinha
        # ganho 2 s (referência passaria em 25 s, ele passou em 23 s).
        assert comparator.delta_ms_at(1000.0, 23_000) == pytest.approx(-2000.0, abs=1.0)
        # ...e devolveu tudo até o fim: aos 4000 m está empatado.
        assert comparator.delta_ms_at(3999.0, 99_975) == pytest.approx(0.0, abs=30.0)

    def test_referencia_com_carro_parado_nao_estoura(self) -> None:
        """Amostras repetidas na mesma distância acontecem de verdade: carro
        parado no grid, ou travado num muro. Não podem virar divisão por zero.

        Note que a consulta é em 150 m, não em 100: em 100 m — a última
        distância do trecho parado — o comparador cairia no caminho "já passou
        do que a referência cobre" e devolveria None por outro motivo.
        """
        points = [
            make_point(0.0, 0),
            make_point(100.0, 1_000),
            make_point(100.0, 2_000),   # parado: mesma distância, tempo corre
            make_point(200.0, 3_000),
        ]
        comparator = LapComparator(points)

        delta = comparator.delta_ms_at(150.0, 2_500)
        assert delta is not None
        assert delta == pytest.approx(0.0, abs=1.0)


class TestLapSeries:
    def test_volta_vazia_e_reconhecida(self) -> None:
        assert LapSeries([]).is_empty is True
        assert LapSeries([make_point(0.0, 0)]).is_empty is True

    def test_consulta_canal_por_distancia(self) -> None:
        points = [
            make_point(0.0, 0, speed_kmh=100.0),
            make_point(100.0, 2_000, speed_kmh=200.0),
        ]
        series = LapSeries(points)

        assert series.value_at(50.0, "speed_kmh") == pytest.approx(150.0)

    def test_fora_do_trecho_devolve_none(self) -> None:
        series = LapSeries([make_point(0.0, 0), make_point(100.0, 2_000)])

        assert series.value_at(-10.0, "speed_kmh") is None
        assert series.value_at(500.0, "speed_kmh") is None

    def test_canal_desconhecido_falha_na_hora(self) -> None:
        """Erro de digitação em nome de canal falha com mensagem clara, em vez
        de virar AttributeError no meio da renderização."""
        series = LapSeries([make_point(0.0, 0), make_point(100.0, 1_000)])

        with pytest.raises(KeyError, match="speeed_kmh"):
            series.points("speeed_kmh")

    def test_max_distance_e_max_time(self) -> None:
        series = LapSeries([make_point(0.0, 0), make_point(3500.0, 95_000)])

        assert series.max_distance == pytest.approx(3500.0)
        assert series.max_time == pytest.approx(95.0)

    def test_pares_por_tempo_e_por_distancia(self) -> None:
        series = LapSeries([make_point(0.0, 0), make_point(100.0, 2_000)])

        assert series.points("speed_kmh")[0][0] == 0.0
        assert series.points_by_time("speed_kmh")[1][0] == pytest.approx(2.0)

    def test_volta_sintetica_completa(self) -> None:
        """Integração: uma volta do gerador atravessa o LapSeries inteiro."""
        from gt7core.events.bus import EventBus
        from gt7core.telemetry.engine import TelemetryEngine, TelemetryReceived
        from gt7core.telemetry.sources.mock import synthetic_lap

        bus = EventBus()
        engine = TelemetryEngine(bus)
        points: list[TelemetryPoint] = []
        bus.subscribe(TelemetryReceived, lambda e: points.append(e.point))

        for frame in synthetic_lap():
            engine.on_frame(frame)

        series = LapSeries(points)
        assert series.is_empty is False
        assert series.has_channel("speed_kmh")
        assert series.has_channel("brake")

        mid_speed = series.value_at(series.max_distance / 2, "speed_kmh")
        assert mid_speed is not None and 20 < mid_speed < 400
