"""
Testes dos módulos de análise da Fase 4.

A volta sintética é usada como **verdade conhecida**: o perfil de velocidade tem
exatamente quatro mínimos, então quatro curvas e quatro zonas de frenagem é o
resultado certo, não um número observado e depois transcrito para o teste.

Onde a propriedade a verificar não existe no gerador — pressão de freio
constante, um alívio de acelerador, uma perda de tempo concentrada num único
trecho — os pontos são construídos à mão, com o valor esperado derivado da
aritmética e não da execução.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from gt7core.analytics.braking import (
    BrakingZone,
    braking_consistency,
    compare_braking,
    detect_braking_zones,
)
from gt7core.analytics.corners import corner_at, detect_corners, match_corners
from gt7core.analytics.driver import MIN_LAPS_FOR_PROFILE, build_profile
from gt7core.analytics.matching import match_by_distance
from gt7core.analytics.throttle import analyse_throttle, compare_throttle
from gt7core.analytics.timeloss import analyse_time_loss
from gt7core.analytics.tyres import (
    SlipConvention,
    TyreEvent,
    detect_tyre_events,
    infer_slip_convention,
    slip_ratio,
    stint_degradation,
    temperature_balance,
)
from gt7core.domain.models import TelemetryPoint
from gt7core.events.bus import EventBus
from gt7core.telemetry.engine import TelemetryEngine, TelemetryReceived
from gt7core.telemetry.sources.mock import synthetic_lap

# O gerador sintético tem quatro mínimos de velocidade declarados em
# `_SPEED_PROFILE` (frações 0.18, 0.44, 0.66 e 0.88). Tudo neste arquivo que
# espera "quatro" deriva daí.
EXPECTED_CORNERS = 4


def build_lap(**kwargs: object) -> list[TelemetryPoint]:
    """Uma volta sintética já passada pelo motor, como a aplicação a vê."""
    bus = EventBus()
    engine = TelemetryEngine(bus)
    points: list[TelemetryPoint] = []
    bus.subscribe(TelemetryReceived, lambda e: points.append(e.point))
    for frame in synthetic_lap(**kwargs):  # type: ignore[arg-type]
        engine.on_frame(frame)
    return points


def make_point(**overrides: object) -> TelemetryPoint:
    """Uma amostra com valores neutros, para montar casos à mão."""
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
        "tire_slip_fl": 41.7,
        "tire_slip_fr": 41.7,
        "tire_slip_rl": 41.7,
        "tire_slip_rr": 41.7,
        "turbo_boost": 1.0,
        "oil_temp": 100.0,
        "water_temp": 88.0,
    }
    defaults.update(overrides)
    return TelemetryPoint(**defaults)  # type: ignore[arg-type]


def constant_speed_lap(
    *, samples: int = 600, speed_kmh: float = 150.0
) -> list[TelemetryPoint]:
    """Uma volta em velocidade constante: sem curva alguma a encontrar."""
    speed_ms = speed_kmh / 3.6
    return [
        make_point(
            elapsed_ms=i * 20,
            distance_m=speed_ms * (i * 20) / 1000.0,
            speed_kmh=speed_kmh,
            tire_slip_fl=speed_ms,
            tire_slip_fr=speed_ms,
            tire_slip_rl=speed_ms,
            tire_slip_rr=speed_ms,
        )
        for i in range(samples)
    ]


@pytest.fixture(scope="module")
def reference_lap() -> list[TelemetryPoint]:
    return build_lap()


class TestMatching:
    """A atribuição por distância, que todos os comparadores usam."""

    def test_ordem_e_tamanho_da_referencia_sao_preservados(self) -> None:
        pairs = match_by_distance(
            [10.0, 500.0, 900.0],
            [505.0],
            reference_key=lambda v: v,
            candidate_key=lambda v: v,
            tolerance_m=50.0,
        )
        assert [reference for reference, _ in pairs] == [10.0, 500.0, 900.0]
        assert [matched for _, matched in pairs] == [None, 505.0, None]

    def test_um_candidato_nao_serve_a_duas_referencias(self) -> None:
        """O defeito que motivou o módulo.

        Duas curvas de uma chicane, a 60 m uma da outra, e uma volta em que o
        detector achou só uma. Com "vizinho mais próximo" ingênuo as duas
        reclamariam o mesmo evento; aqui a mais próxima leva e a outra fica sem.
        """
        pairs = match_by_distance(
            [1000.0, 1060.0],
            [1010.0],
            reference_key=lambda v: v,
            candidate_key=lambda v: v,
            tolerance_m=150.0,
        )
        assert [matched for _, matched in pairs] == [1010.0, None]

    def test_os_pares_mais_proximos_escolhem_primeiro(self) -> None:
        pairs = match_by_distance(
            [100.0, 200.0],
            [205.0, 102.0],
            reference_key=lambda v: v,
            candidate_key=lambda v: v,
            tolerance_m=150.0,
        )
        assert [matched for _, matched in pairs] == [102.0, 205.0]

    def test_fora_da_tolerancia_nao_casa(self) -> None:
        pairs = match_by_distance(
            [100.0],
            [400.0],
            reference_key=lambda v: v,
            candidate_key=lambda v: v,
            tolerance_m=150.0,
        )
        assert pairs == [(100.0, None)]

    def test_listas_vazias_nao_quebram(self) -> None:
        assert (
            match_by_distance(
                [], [1.0], reference_key=lambda v: v, candidate_key=lambda v: v,
                tolerance_m=10.0,
            )
            == []
        )


class TestCorners:
    def test_detecta_as_quatro_curvas_do_perfil(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        assert len(detect_corners(reference_lap)) == EXPECTED_CORNERS

    def test_curvas_em_ordem_de_distancia_e_numeradas_de_um(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        corners = detect_corners(reference_lap)
        apexes = [c.apex_distance_m for c in corners]
        assert apexes == sorted(apexes)
        assert [c.index for c in corners] == [1, 2, 3, 4]

    def test_apice_e_o_ponto_mais_lento_da_curva(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        for corner in detect_corners(reference_lap):
            inside = [
                p.speed_kmh
                for p in reference_lap
                if corner.entry_distance_m <= p.distance_m <= corner.exit_distance_m
            ]
            # Tolerância de 2 km/h: o ápice é escolhido no perfil suavizado, e a
            # amostra crua mais lenta pode estar um quadro ao lado.
            assert corner.minimum_speed_kmh <= min(inside) + 2.0

    def test_velocidade_constante_nao_produz_curva(self) -> None:
        assert detect_corners(constant_speed_lap()) == []

    def test_volta_curta_demais_devolve_lista_vazia(self) -> None:
        assert detect_corners(constant_speed_lap(samples=10)) == []

    def test_severidade_segue_a_velocidade_minima(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        for corner in detect_corners(reference_lap):
            if corner.minimum_speed_kmh < 80:
                assert corner.severity == "lenta"
            elif corner.minimum_speed_kmh < 140:
                assert corner.severity == "média"
            else:
                assert corner.severity == "rápida"

    def test_corner_at_localiza_a_curva_do_ponto(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        corners = detect_corners(reference_lap)
        target = corners[2]
        found = corner_at(corners, target.apex_distance_m)
        assert found is not None and found.index == target.index

    def test_corner_at_fora_de_qualquer_curva(self) -> None:
        assert corner_at([], 100.0) is None

    def test_curva_faltando_nao_desalinha_as_seguintes(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        """A propriedade que justifica casar por distância em vez de por índice."""
        corners = detect_corners(reference_lap)
        without_second = [c for c in corners if c.index != 2]

        matches = match_corners(corners, without_second)
        assert matches[1][1] is None
        # As curvas 3 e 4 continuam casando consigo mesmas, não deslocadas.
        for reference, matched in (matches[2], matches[3]):
            assert matched is not None
            assert matched.apex_distance_m == reference.apex_distance_m


class TestBraking:
    def test_uma_zona_por_curva(self, reference_lap: list[TelemetryPoint]) -> None:
        assert len(detect_braking_zones(reference_lap)) == EXPECTED_CORNERS

    def test_zonas_precedem_os_apices(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        """Freia-se antes da curva — se isso não valer, algo está trocado."""
        zones = detect_braking_zones(reference_lap)
        for zone, corner in zip(zones, detect_corners(reference_lap), strict=True):
            assert zone.start_distance_m < corner.apex_distance_m

    def test_toque_curto_no_freio_e_ignorado(self) -> None:
        points = [
            make_point(
                elapsed_ms=i * 20,
                distance_m=i * 0.8,
                brake=80.0 if i in (5, 6) else 0.0,
            )
            for i in range(100)
        ]
        assert detect_braking_zones(points) == []

    def test_pressao_abaixo_do_limiar_nao_conta(self) -> None:
        points = [
            make_point(elapsed_ms=i * 20, distance_m=i * 0.8, brake=3.0)
            for i in range(100)
        ]
        assert detect_braking_zones(points) == []

    def test_pressao_constante_nao_e_trail_braking(self) -> None:
        points = [
            make_point(
                elapsed_ms=i * 20,
                distance_m=i * 0.8,
                brake=80.0 if i < 60 else 0.0,
                speed_kmh=200.0 - i,
            )
            for i in range(100)
        ]
        zones = detect_braking_zones(points)
        assert len(zones) == 1
        assert zones[0].trail_braking_ratio == 0.0

    def test_liberacao_monotonica_e_trail_braking_completo(self) -> None:
        points = [
            make_point(
                elapsed_ms=i * 20,
                distance_m=i * 0.8,
                brake=max(6.0, 100.0 - i * 1.5),
                speed_kmh=200.0 - i,
            )
            for i in range(60)
        ]
        zones = detect_braking_zones(points)
        assert len(zones) == 1
        assert zones[0].trail_braking_ratio == pytest.approx(1.0)

    def test_deceleracao_media_em_g(self) -> None:
        """100 km/h perdidos em 2 s são 1,416 g — conferível na mão."""
        zone = BrakingZone(
            start_distance_m=0.0,
            end_distance_m=100.0,
            start_time_ms=0,
            end_time_ms=2000,
            entry_speed_kmh=200.0,
            exit_speed_kmh=100.0,
            max_pressure_pct=100.0,
            average_pressure_pct=90.0,
            trail_braking_ratio=0.5,
        )
        assert zone.duration_ms == 2000
        assert zone.speed_drop_kmh == 100.0
        assert zone.average_deceleration_g == pytest.approx(
            (100.0 / 3.6) / 2.0 / 9.81, rel=1e-6
        )

    def test_zona_de_duracao_nula_nao_divide_por_zero(self) -> None:
        zone = BrakingZone(
            start_distance_m=0.0,
            end_distance_m=0.0,
            start_time_ms=500,
            end_time_ms=500,
            entry_speed_kmh=100.0,
            exit_speed_kmh=100.0,
            max_pressure_pct=50.0,
            average_pressure_pct=50.0,
            trail_braking_ratio=0.0,
        )
        assert zone.average_deceleration_g == 0.0

    def test_comparacao_acusa_frenagem_mais_tardia(self) -> None:
        early = [
            make_point(
                elapsed_ms=i * 20,
                distance_m=i * 1.0,
                brake=90.0 if 100 <= i < 160 else 0.0,
                speed_kmh=200.0,
            )
            for i in range(300)
        ]
        # Mesma volta com a freada 30 m adiante.
        late = [
            make_point(
                elapsed_ms=i * 20,
                distance_m=i * 1.0,
                brake=90.0 if 130 <= i < 190 else 0.0,
                speed_kmh=200.0,
            )
            for i in range(300)
        ]
        comparisons = compare_braking(early, late)
        assert len(comparisons) == 1
        assert comparisons[0].brake_point_delta_m == pytest.approx(30.0)
        assert "mais tarde" in comparisons[0].describe()

    def test_comparacao_sem_correspondente(self) -> None:
        braked = [
            make_point(
                elapsed_ms=i * 20, distance_m=i * 1.0, brake=90.0 if i < 60 else 0.0
            )
            for i in range(300)
        ]
        coasted = [make_point(elapsed_ms=i * 20, distance_m=i * 1.0) for i in range(300)]
        comparisons = compare_braking(braked, coasted)
        assert comparisons[0].analysed is None
        assert comparisons[0].brake_point_delta_m is None
        assert comparisons[0].describe() == "sem frenagem correspondente nesta volta"

    def test_consistencia_e_zero_entre_voltas_identicas(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        value = braking_consistency([reference_lap, reference_lap, reference_lap])
        assert value is not None
        assert value == pytest.approx(0.0, abs=1e-6)

    def test_consistencia_exige_duas_voltas(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        assert braking_consistency([reference_lap]) is None

    def test_consistencia_none_com_numero_diferente_de_freadas(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        assert braking_consistency([reference_lap, constant_speed_lap()]) is None


class TestTyres:
    def test_infere_velocidade_de_superficie_na_volta_sintetica(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        assert infer_slip_convention(reference_lap) is SlipConvention.SURFACE_SPEED_MS

    def test_infere_razao_quando_o_canal_fica_perto_de_um(self) -> None:
        points = [
            make_point(
                elapsed_ms=i * 20,
                distance_m=i * 1.0,
                speed_kmh=180.0,
                tire_slip_fl=1.02,
                tire_slip_fr=1.01,
                tire_slip_rl=1.03,
                tire_slip_rr=1.04,
            )
            for i in range(200)
        ]
        assert infer_slip_convention(points) is SlipConvention.RATIO

    def test_sem_amostras_uteis_assume_razao(self) -> None:
        """A leitura conservadora: silencia a detecção em vez de inventar eventos."""
        assert infer_slip_convention([]) is SlipConvention.RATIO

    def test_slip_ratio_none_em_baixa_velocidade(self) -> None:
        parked = make_point(speed_kmh=10.0)
        assert slip_ratio(parked, "fl", SlipConvention.SURFACE_SPEED_MS) is None

    def test_slip_ratio_converte_de_m_s(self) -> None:
        # 180 km/h = 50 m/s; superfície a 45 m/s = razão 0,9.
        point = make_point(speed_kmh=180.0, tire_slip_fl=45.0)
        ratio = slip_ratio(point, "fl", SlipConvention.SURFACE_SPEED_MS)
        assert ratio == pytest.approx(0.9)

    def test_detecta_travamento_e_patinagem(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        events = detect_tyre_events(reference_lap)
        kinds = {event.kind for event in events}
        assert kinds == {"travamento", "patinagem"}

    def test_patinagem_so_nas_rodas_de_tracao(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        spinning = {
            e.wheel for e in detect_tyre_events(reference_lap) if e.kind == "patinagem"
        }
        assert spinning <= {"rl", "rr"}

    def test_eventos_ordenados_por_distancia(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        events = detect_tyre_events(reference_lap)
        starts = [e.start_distance_m for e in events]
        assert starts == sorted(starts)

    def test_travamento_exige_freio_aplicado(self) -> None:
        """Roda girando devagar sem freio não é travamento — é o canal ruidoso."""
        points = [
            make_point(
                elapsed_ms=i * 20,
                distance_m=i * 1.0,
                speed_kmh=180.0,
                brake=0.0,
                tire_slip_fl=40.0,  # razão 0,8 — abaixo do limiar
            )
            for i in range(200)
        ]
        assert detect_tyre_events(points, convention=SlipConvention.SURFACE_SPEED_MS) == []

    def test_evento_curto_demais_e_ignorado(self) -> None:
        points = [
            make_point(
                elapsed_ms=i * 20,
                distance_m=i * 1.0,
                speed_kmh=180.0,
                brake=90.0,
                tire_slip_fl=40.0 if i in (10, 11) else 50.0,
                tire_slip_fr=50.0,
                tire_slip_rl=50.0,
                tire_slip_rr=50.0,
            )
            for i in range(200)
        ]
        assert detect_tyre_events(points, convention=SlipConvention.SURFACE_SPEED_MS) == []

    def test_equilibrio_termico_calcula_os_deltas(self) -> None:
        points = [
            make_point(
                tire_temp_fl=100.0,
                tire_temp_fr=100.0,
                tire_temp_rl=80.0,
                tire_temp_rr=80.0,
            )
            for _ in range(10)
        ]
        balance = temperature_balance(points)
        assert balance is not None
        assert balance.front_rear_delta_c == pytest.approx(20.0)
        assert balance.left_right_delta_c == pytest.approx(0.0)
        assert balance.peak_temp_c == pytest.approx(100.0)
        assert "dianteiros" in balance.describe()

    def test_equilibrio_termico_sem_amostras(self) -> None:
        assert temperature_balance([]) is None

    def test_temperaturas_equilibradas_nao_geram_diagnostico(self) -> None:
        balance = temperature_balance([make_point() for _ in range(5)])
        assert balance is not None
        assert balance.describe() == "temperaturas equilibradas entre eixos e lados"

    def test_degradacao_detecta_perda_de_ritmo(self) -> None:
        laps = [build_lap(lap_time_ms=t) for t in (102_000, 103_000, 104_000)]
        degradation = stint_degradation(laps)
        assert degradation is not None
        assert degradation.lap_count == 3
        assert degradation.pace_trend_ms_per_lap > 0
        assert "caindo" in degradation.describe()

    def test_degradacao_exige_duas_voltas(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        assert stint_degradation([reference_lap]) is None


class TestThrottle:
    def test_uma_saida_por_curva(self, reference_lap: list[TelemetryPoint]) -> None:
        corners = detect_corners(reference_lap)
        assert len(analyse_throttle(reference_lap, corners)) == EXPECTED_CORNERS

    def test_retomada_acontece_depois_do_apice(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        corners = detect_corners(reference_lap)
        for application in analyse_throttle(reference_lap, corners):
            assert application.delay_from_apex_m >= 0.0

    def test_sem_curvas_nao_ha_saida(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        assert analyse_throttle(reference_lap, []) == []

    def test_conta_os_alivios(self) -> None:
        """Sobe a 80%, cai a 40%, volta a 90%: exatamente um alívio."""
        curve = [20.0] * 5 + [80.0] * 10 + [40.0] * 10 + [90.0] * 15
        points = [
            make_point(
                elapsed_ms=i * 20,
                distance_m=100.0 + i * 1.0,
                speed_kmh=100.0 + i,
                throttle=value,
            )
            for i, value in enumerate(curve)
        ]
        corners = detect_corners(points)
        assert corners == []  # curto demais; a saída é montada direto

        from gt7core.analytics.corners import Corner

        corner = Corner(
            index=1,
            entry_distance_m=100.0,
            apex_distance_m=100.0,
            exit_distance_m=100.0 + (len(curve) - 1),
            entry_speed_kmh=100.0,
            minimum_speed_kmh=100.0,
            exit_speed_kmh=140.0,
            entry_time_ms=0,
            apex_time_ms=0,
            exit_time_ms=(len(curve) - 1) * 20,
            radius_m=None,
        )
        applications = analyse_throttle(points, [corner])
        assert len(applications) == 1
        assert applications[0].lift_count == 1

    def test_tempo_ate_pedal_cheio_none_quando_nao_atinge(self) -> None:
        from gt7core.analytics.corners import Corner

        points = [
            make_point(
                elapsed_ms=i * 20,
                distance_m=100.0 + i,
                speed_kmh=100.0 + i,
                throttle=60.0,
            )
            for i in range(40)
        ]
        corner = Corner(
            index=1,
            entry_distance_m=100.0,
            apex_distance_m=100.0,
            exit_distance_m=139.0,
            entry_speed_kmh=100.0,
            minimum_speed_kmh=100.0,
            exit_speed_kmh=140.0,
            entry_time_ms=0,
            apex_time_ms=0,
            exit_time_ms=780,
            radius_m=None,
        )
        application = analyse_throttle(points, [corner])[0]
        assert application.time_to_full_ms is None
        assert application.time_to_half_ms == 0

    def test_comparacao_acusa_aceleracao_tardia(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        corners = detect_corners(reference_lap)
        reference = analyse_throttle(reference_lap, corners)
        # Mesma análise com a retomada 40 m adiante em todas as curvas.
        delayed = [
            replace(
                application,
                application_distance_m=application.application_distance_m + 40.0,
            )
            for application in reference
        ]
        comparisons = compare_throttle(reference, delayed)
        assert len(comparisons) == EXPECTED_CORNERS
        for comparison in comparisons:
            assert comparison.application_delta_m == pytest.approx(40.0)
            assert "mais tarde" in comparison.describe()

    def test_comparacao_sem_correspondente(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        reference = analyse_throttle(reference_lap, detect_corners(reference_lap))
        comparisons = compare_throttle(reference, [])
        assert all(c.analysed is None for c in comparisons)
        assert comparisons[0].describe() == "sem saída correspondente nesta volta"


class TestTimeLoss:
    def test_total_bate_com_a_diferenca_entre_as_voltas(self) -> None:
        reference = build_lap(lap_time_ms=102_000)
        slower = build_lap(lap_time_ms=104_000)
        report = analyse_time_loss(reference, slower)
        # As duas voltas cobrem a mesma distância, então a diferença total é a
        # diferença de tempo de volta.
        assert report.total_delta_ms == pytest.approx(2000.0, abs=60.0)

    def test_soma_dos_segmentos_bate_com_o_total(self) -> None:
        reference = build_lap(lap_time_ms=102_000)
        slower = build_lap(lap_time_ms=104_000)
        report = analyse_time_loss(reference, slower)
        soma = sum(segment.time_delta_ms for segment in report.segments)
        assert soma == pytest.approx(report.total_delta_ms, abs=5.0)

    def test_perda_isolada_aparece_no_segmento_certo(self) -> None:
        """A propriedade central do módulo.

        A volta analisada é idêntica à referência, exceto por meio segundo
        perdido depois dos 2700 m. Como o tempo atribuído a cada segmento é a
        **variação** do delta e não o delta acumulado, só o segmento que contém
        2700 m pode acusar perda — os seguintes devem ficar em zero, mesmo tendo
        delta acumulado de meio segundo.
        """
        reference = build_lap()
        penalty_ms = 500
        analysed = [
            replace(point, elapsed_ms=point.elapsed_ms + penalty_ms)
            if point.distance_m > 2700.0
            else replace(point)
            for point in reference
        ]

        report = analyse_time_loss(reference, analysed)

        guilty = [
            s
            for s in report.segments
            if s.start_distance_m <= 2700.0 <= s.end_distance_m
        ]
        assert len(guilty) == 1
        assert guilty[0].time_delta_ms == pytest.approx(penalty_ms, abs=30.0)

        later = [s for s in report.segments if s.start_distance_m > 2700.0]
        assert later, "o caso perde o sentido se a penalidade cair no último segmento"
        for segment in later:
            assert segment.time_delta_ms == pytest.approx(0.0, abs=30.0)
            assert not segment.is_loss

    def test_volta_identica_nao_acusa_perda(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        report = analyse_time_loss(reference_lap, reference_lap)
        assert report.losses == []
        assert report.recoverable_ms == 0.0
        assert report.total_delta_ms == pytest.approx(0.0, abs=1e-6)

    def test_ganho_e_relatado_e_nao_conta_como_recuperavel(self) -> None:
        reference = build_lap()
        analysed = [
            replace(point, elapsed_ms=point.elapsed_ms - 400)
            if point.distance_m > 2700.0
            else replace(point)
            for point in reference
        ]
        report = analyse_time_loss(reference, analysed)
        assert report.gains, "o trecho mais rápido deve aparecer como ganho"
        assert report.recoverable_ms == 0.0

    def test_piores_trechos_vem_ordenados(self) -> None:
        report = analyse_time_loss(
            build_lap(lap_time_ms=102_000), build_lap(lap_time_ms=105_000)
        )
        worst = report.worst(3)
        assert len(worst) <= 3
        assert worst == sorted(worst, key=lambda s: s.time_delta_ms, reverse=True)

    def test_volta_sem_curvas_vira_um_segmento_unico(self) -> None:
        flat = constant_speed_lap()
        slower = [replace(p, elapsed_ms=int(p.elapsed_ms * 1.02)) for p in flat]
        report = analyse_time_loss(flat, slower)
        assert [s.label for s in report.segments] == ["Volta"]

    def test_voltas_vazias_devolvem_relatorio_vazio(self) -> None:
        report = analyse_time_loss([], [])
        assert report.segments == []
        assert report.total_delta_ms == 0.0
        assert "sem trechos comparáveis" in report.summary()

    def test_resumo_menciona_as_curvas_perdidas(self) -> None:
        report = analyse_time_loss(
            build_lap(lap_time_ms=102_000), build_lap(lap_time_ms=104_000)
        )
        summary = report.summary()
        assert "Diferença total" in summary
        assert "Curva" in summary


class TestDriverProfile:
    def test_none_sem_voltas(self) -> None:
        assert build_profile([]) is None
        assert build_profile([[]]) is None

    def test_perfil_com_poucas_voltas_e_preliminar(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        profile = build_profile([reference_lap])
        assert profile is not None
        assert profile.lap_count == 1
        assert not profile.is_reliable
        assert "preliminar" in profile.summary()

    def test_perfil_confiavel_a_partir_do_minimo(self) -> None:
        laps = [build_lap(lap_time_ms=102_000 + i * 100) for i in range(MIN_LAPS_FOR_PROFILE)]
        profile = build_profile(laps)
        assert profile is not None
        assert profile.is_reliable
        assert "preliminar" not in profile.summary()

    def test_melhor_volta_e_a_mais_rapida(self) -> None:
        laps = [build_lap(lap_time_ms=t) for t in (104_000, 102_000, 103_000)]
        profile = build_profile(laps)
        assert profile is not None
        assert profile.best_lap_ms == min(lap[-1].elapsed_ms for lap in laps)

    def test_tendencia_negativa_quando_o_piloto_melhora(self) -> None:
        laps = [build_lap(lap_time_ms=t) for t in (105_000, 104_000, 103_000, 102_000)]
        profile = build_profile(laps)
        assert profile is not None
        assert profile.pace_trend_ms_per_lap < 0
        assert any("evoluindo" in note for note in profile.strengths())

    def test_voltas_iguais_sao_consistentes(self) -> None:
        laps = [build_lap() for _ in range(5)]
        profile = build_profile(laps)
        assert profile is not None
        assert profile.lap_time_stddev_ms == pytest.approx(0.0, abs=1e-6)
        assert profile.consistency_label == "consistente"
        assert profile.braking_point_stddev_m == pytest.approx(0.0, abs=1e-6)

    def test_voltas_dispersas_sao_irregulares(self) -> None:
        laps = [build_lap(lap_time_ms=t) for t in (100_000, 106_000, 101_000, 107_000)]
        profile = build_profile(laps)
        assert profile is not None
        assert profile.consistency_label == "irregular"
        assert any("nconsistência" in note for note in profile.weaknesses())

    def test_incidente_nas_duas_rodas_conta_uma_vez(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        """O perfil conta ocorrências; a detecção continua sendo por roda.

        Travar as duas dianteiras na mesma frenagem é **um** travamento para o
        piloto, e dois eventos para o detector — que precisa dessa granularidade
        porque travar só uma roda é outro diagnóstico. Somar eventos aqui
        dobrava o número: uma volta com quatro frenagens virava "8 travamentos".

        Errar por um fator de dois já era ruim na tela; a partir da Fase 7 o
        número vai no prompt do engenheiro, que é instruído a não inventar
        grandeza e repetiria a inflação com toda a confiança.
        """
        events = detect_tyre_events(reference_lap)
        por_roda = sum(1 for e in events if e.kind == "travamento")
        assert por_roda > 0, "a volta de referência precisa ter travamentos"

        profile = build_profile([reference_lap])
        assert profile is not None
        assert profile.lockups_per_lap < por_roda, "contou por roda, não por incidente"

        # Cada incidente é um intervalo de distância distinto.
        spans = {
            (e.start_distance_m, e.end_distance_m)
            for e in events
            if e.kind == "travamento"
        }
        assert profile.lockups_per_lap == len(spans)

    def test_agrupamento_de_incidentes_separa_o_que_e_separado(self) -> None:
        """Agrupar por sobreposição não pode fundir frenagens distintas.

        Casos montados à mão porque a volta sintética é simétrica demais para
        distinguir "as duas rodas na mesma frenagem" de "duas frenagens".
        """
        from gt7core.analytics.driver import _incident_count

        def event(wheel: str, start: float, end: float, kind: str = "travamento") -> TyreEvent:
            return TyreEvent(
                kind=kind,
                wheel=wheel,
                start_distance_m=start,
                end_distance_m=end,
                start_time_ms=0,
                end_time_ms=100,
                peak_ratio=0.6,
            )

        # Mesmo eixo, mesma frenagem: um incidente.
        assert _incident_count(
            [event("fl", 700, 880), event("fr", 700, 880)], "travamento"
        ) == 1
        # Uma roda só, ainda é um incidente — não pode sumir.
        assert _incident_count([event("fl", 700, 880)], "travamento") == 1
        # Duas frenagens distintas, em pontos distintos da pista: dois.
        assert _incident_count(
            [event("fl", 700, 880), event("fl", 1600, 1750)], "travamento"
        ) == 2
        # Sobreposição parcial (uma roda trava um pouco depois): ainda um.
        assert _incident_count(
            [event("fl", 700, 880), event("fr", 820, 910)], "travamento"
        ) == 1
        # Espécies diferentes não se misturam.
        assert _incident_count(
            [event("fl", 700, 880), event("rl", 700, 880, "patinagem")], "patinagem"
        ) == 1
        assert _incident_count([], "travamento") == 0

    def test_estilo_de_frenagem_reflete_o_trail_braking(
        self, reference_lap: list[TelemetryPoint]
    ) -> None:
        profile = build_profile([reference_lap])
        assert profile is not None
        assert profile.braking_style in {
            "trail braking acentuado",
            "trail braking moderado",
            "frenagem em linha reta",
        }
