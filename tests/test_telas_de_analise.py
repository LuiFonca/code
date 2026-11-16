"""
Curvas, guinada, voltas extras e mapa de ganho e perda — nas telas.

Os serviços de domínio já são testados com resposta conhecida em
`test_analise_de_curvas.py`. Aqui o que se verifica é a ligação: que a tela
mostra o que o serviço calculou, na volta certa e com o sinal certo.
"""

import pytest

from src.application.events.event_bus import EventBus
from src.application.viewmodels.comparison_viewmodel import ComparisonViewModel
from src.application.viewmodels.telemetry_viewmodel import TelemetryViewModel
from src.domain.models.lap import Lap
from src.presentation.tabs.comparison_tab import ComparisonTab, gain_color
from src.presentation.tabs.telemetry_tab import TelemetryTab
from tests.test_analise_de_curvas import PISTA, _volta


def _tempo(pontos):
    return pontos[-1].elapsed_ms


@pytest.fixture
def pista_com_curvas(laps, tracks):
    """Pista com três voltas: referência, uma lenta na curva 1 e uma igual."""
    tid = tracks.get_or_create("Pista")
    lenta = list(PISTA)
    lenta[2] = (100.0, 75.0, 1.2, 0.0)
    ids = []
    for trechos in (PISTA, lenta, PISTA):
        pontos = _volta(trechos)
        ids.append(
            laps.save(Lap(track_id=tid, lap_time_ms=_tempo(pontos),
                          is_complete=True, points=pontos))
        )
    return tid, ids


# ------------------------------------------------------- Telemetria
@pytest.fixture
def aba_telemetria(qapp, laps, tracks, pista_com_curvas):
    tid, ids = pista_com_curvas
    bus = EventBus()
    vm = TelemetryViewModel(laps, bus, tracks)
    aba = TelemetryTab(vm)
    vm.set_track(tid)
    vm.load_lap(ids[0])
    yield aba, vm
    vm.dispose()
    bus.clear()


def test_tabela_de_curvas_lista_as_curvas_da_volta(aba_telemetria):
    aba, vm = aba_telemetria
    assert len(vm.corners()) == 2
    assert aba.corners_table.rowCount() == 2
    assert aba.corners_table.item(0, 1).text() == "direita"
    assert aba.corners_table.item(0, 4).text() == "90"
    assert aba.corners_table.item(0, 8).text() == "neutro"


def test_grafico_de_guinada_em_graus_por_segundo(aba_telemetria):
    aba, vm = aba_telemetria
    assert aba.chart_yaw.chart().series()
    # Curva 1: 1,2 g a 90 km/h pede 0,47 rad/s, que são ~27 °/s.
    pico = max(abs(y) for _, y in vm.yaw_points())
    assert pico == pytest.approx(27.0, abs=1.5)


def test_volta_antiga_sem_guinada_esconde_o_grafico(qapp, laps, tracks, make_lap):
    """Gráfico vazio com título afirmaria 'guinada zero'."""
    tid = tracks.get_or_create("Antiga")
    lap_id = laps.save(make_lap(track_id=tid, samples=200))
    bus = EventBus()
    vm = TelemetryViewModel(laps, bus, tracks)
    aba = TelemetryTab(vm)
    try:
        vm.set_track(tid)
        vm.load_lap(lap_id)
        assert aba.chart_yaw.isHidden()
    finally:
        vm.dispose()
        bus.clear()


def test_trocar_de_volta_refaz_as_curvas(qapp, laps, tracks, pista_com_curvas, make_lap):
    tid, ids = pista_com_curvas
    sem_curvas = laps.save(make_lap(track_id=tid, samples=200, g_lateral=0.0))
    bus = EventBus()
    vm = TelemetryViewModel(laps, bus, tracks)
    try:
        vm.set_track(tid)
        vm.load_lap(ids[0])
        assert len(vm.corners()) == 2
        vm.load_lap(sem_curvas)
        assert vm.corners() == [], "as curvas da volta anterior ficaram em cache"
    finally:
        vm.dispose()
        bus.clear()


# ------------------------------------------------------- Comparação
@pytest.fixture
def aba_comparacao(qapp, laps, tracks, pista_com_curvas):
    tid, ids = pista_com_curvas
    bus = EventBus()
    vm = ComparisonViewModel(laps, bus, tracks)
    aba = ComparisonTab(vm)
    vm.set_track(tid)
    yield aba, vm, ids
    vm.dispose()
    bus.clear()


def test_tabela_de_curvas_mostra_a_perda_na_curva_certa(aba_comparacao):
    aba, vm, (ref, lenta, _igual) = aba_comparacao
    vm.compare(ref, lenta)

    assert aba.corners_table.rowCount() == 2
    assert aba.corners_table.item(0, 2).text().startswith("+0.8")
    assert aba.corners_table.item(0, 3).text() == "-15.0 km/h"
    assert aba.corners_table.item(1, 2).text() in ("+0.000 s", "-0.000 s")


def test_mapa_de_ganho_e_perda_e_desenhado(aba_comparacao):
    aba, vm, (ref, lenta, _igual) = aba_comparacao
    vm.compare(ref, lenta)
    assert vm.result.delta_map
    assert len(aba.gain_map._paths) == len(vm.result.delta_map)


def test_voltas_extras_entram_nos_graficos(aba_comparacao):
    aba, vm, (ref, lenta, igual) = aba_comparacao
    vm.compare(ref, lenta, [igual])

    assert len(vm.result.extra_laps) == 1
    nomes_delta = [s.name() for s in aba.chart_delta.chart().series()]
    assert nomes_delta == ["B − A", "C − A"]
    nomes_vel = [s.name() for s in aba.chart_speed.chart().series()]
    assert nomes_vel == ["A", "B", "C"]


def test_extra_repetida_e_ignorada(aba_comparacao):
    """Escolher a mesma volta num seletor opcional não é erro."""
    _aba, vm, (ref, lenta, igual) = aba_comparacao
    vm.compare(ref, lenta, [ref, lenta, igual, igual])
    assert [lap.id for lap in vm.result.extra_laps] == [igual]


def test_seletores_extras_comecam_vazios(aba_comparacao):
    aba, _vm, _ids = aba_comparacao
    for combo in aba._extra_combos.values():
        assert combo.currentData() is None
        assert combo.count() == 4      # travessão + três voltas


def test_comparar_pela_tela_leva_as_extras(aba_comparacao):
    aba, vm, (ref, lenta, igual) = aba_comparacao
    aba._combo_a.setCurrentIndex(aba._combo_a.findData(ref))
    aba._combo_b.setCurrentIndex(aba._combo_b.findData(lenta))
    aba._extra_combos["C"].setCurrentIndex(aba._extra_combos["C"].findData(igual))
    aba._on_compare_clicked()
    assert [lap.id for lap in vm.result.extra_laps] == [igual]


# ------------------------------------------------------- cor do mapa
def test_cor_do_mapa_separa_ganho_de_perda():
    perda, ganho, igual = gain_color(100, 100), gain_color(-100, 100), gain_color(0, 100)
    assert perda == "#ff5c5c"
    assert ganho == "#3ddc84"
    assert igual == "#6b6f7a"


def test_cor_do_mapa_e_proporcional_a_maior_diferenca():
    """Escala relativa: a mesma diferença pesa mais numa volta apertada."""
    assert gain_color(50, 50) == gain_color(500, 500)
    assert gain_color(50, 500) != gain_color(50, 50)


def test_cor_do_mapa_sem_diferenca_nenhuma_nao_divide_por_zero():
    assert gain_color(0, 0) == "#6b6f7a"
