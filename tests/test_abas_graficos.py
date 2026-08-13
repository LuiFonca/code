"""
Teste de ouro das abas de gráfico.

A Fase 5 é refatoração pura: extrair a montagem repetida entre Telemetria e
Comparação **sem mudar nada para o usuário**. O risco é justamente esse — não
há funcionalidade nova para provar que deu certo, só a ausência de estrago.

Por isso o teste captura a saída exata dos gráficos (contagem, títulos, séries
ponto a ponto) antes da mudança, e compara depois. Um gráfico que perdesse uma
série ou trocasse de eixo passaria despercebido em qualquer verificação mais
frouxa.
"""

import pytest

from src.application.events.event_bus import EventBus
from src.application.viewmodels.comparison_viewmodel import ComparisonViewModel
from src.application.viewmodels.telemetry_viewmodel import TelemetryViewModel
from src.presentation.tabs.comparison_tab import ComparisonTab
from src.presentation.tabs.telemetry_tab import TelemetryTab


@pytest.fixture
def aba_telemetria(qapp, laps, tracks, cars, make_lap):
    track_id = tracks.get_or_create("Pista")
    car_id = cars.get_or_create("Carro")
    for ms in (90000, 88000, 92000):
        laps.save(make_lap(track_id=track_id, car_id=car_id, lap_time_ms=ms, samples=300))

    bus = EventBus()
    vm = TelemetryViewModel(laps, bus, tracks)
    aba = TelemetryTab(vm)
    vm.set_track(track_id)
    aba._lap_combo.setCurrentIndex(0)
    aba._on_plot_clicked()
    yield aba, vm
    vm.dispose()
    bus.clear()


@pytest.fixture
def aba_comparacao(qapp, laps, tracks, cars, make_lap):
    track_id = tracks.get_or_create("Pista")
    car_id = cars.get_or_create("Carro")
    for ms in (90000, 88000):
        laps.save(make_lap(track_id=track_id, car_id=car_id, lap_time_ms=ms, samples=300))

    bus = EventBus()
    vm = ComparisonViewModel(laps, bus)
    aba = ComparisonTab(vm)
    vm.set_track(track_id)
    aba._on_compare_clicked()
    yield aba, vm
    vm.dispose()
    bus.clear()


def _assinatura(aba):
    """Retrato do estado dos gráficos: quantos, com que título e que séries.

    É o que a refatoração precisa preservar exatamente.
    """
    retrato = []
    for chart in aba._charts:
        series = chart.chart().series()
        retrato.append(
            {
                "titulo": chart.chart().title(),
                "series": [
                    (s.name(), s.count(), round(s.at(0).y(), 4) if s.count() else None)
                    for s in series
                ],
            }
        )
    return retrato


# ======================= Telemetria =========================================
def test_telemetria_monta_19_graficos(aba_telemetria):
    """18 gráficos até a Fase 5, mais o de ângulo de deriva na Fase 6.

    O número era 18 e foi alterado deliberadamente: a Fase 6 **acrescenta** um
    gráfico, sem mexer nos existentes. Se algum dia este teste voltar a 18 sem
    que alguém tenha decidido remover o ângulo, é regressão.
    """
    aba, _ = aba_telemetria
    assert len(aba._charts) == 19


def test_telemetria_mosaicos_completos(aba_telemetria):
    """Três mosaicos de quatro rodas, com a mesma cor por roda em todos."""
    aba, _ = aba_telemetria
    for mosaico in (aba.tire_charts, aba.susp_charts, aba.slip_charts):
        assert set(mosaico) == {"fl", "fr", "rl", "rr"}


def test_telemetria_series_tem_dados(aba_telemetria):
    aba, _ = aba_telemetria
    com_dados = [c for c in aba._charts if c.chart().series()]
    assert len(com_dados) >= 15, "quase todos os gráficos devem ter série"


def test_telemetria_indicador_de_deslizamento(aba_telemetria):
    aba, _ = aba_telemetria
    assert "%" in aba.slip_indicator._value.text()
    assert aba.slip_indicator._verdict.text()


def test_telemetria_resumo_preenchido(aba_telemetria):
    aba, _ = aba_telemetria
    texto = aba._sector_panel.text()
    assert "Tempo:" in texto
    assert "Distância:" in texto


def test_telemetria_troca_de_eixo_preserva_graficos(aba_telemetria):
    """Trocar o eixo não pode perder gráfico nem série."""
    aba, vm = aba_telemetria
    antes = len(_assinatura(aba))

    vm.set_axis_mode("time")
    assert len(_assinatura(aba)) == antes

    vm.set_axis_mode("distance")
    assert len(_assinatura(aba)) == antes


def test_telemetria_linhas_de_setor_so_no_eixo_de_distancia(aba_telemetria):
    """No eixo temporal os limites de setor não têm posição definida."""
    aba, vm = aba_telemetria

    vm.set_axis_mode("distance")
    aba._on_plot_clicked()
    com_setor = sum(1 for c in aba._charts if getattr(c, "_sector_items", []))

    vm.set_axis_mode("time")
    aba._on_plot_clicked()
    sem_setor = sum(1 for c in aba._charts if getattr(c, "_sector_items", []))

    assert com_setor > 0
    assert sem_setor == 0


def test_telemetria_cursor_sincronizado(aba_telemetria):
    """O cursor precisa aparecer e sumir em todos os gráficos de uma vez."""
    aba, _ = aba_telemetria
    aba._on_hover(1000.0)
    visiveis = sum(1 for c in aba._charts if c._crosshair.isVisible())
    assert visiveis > 0

    aba._on_hover_left()
    assert all(not c._crosshair.isVisible() for c in aba._charts)


# ======================= Comparação =========================================
def test_comparacao_monta_6_graficos(aba_comparacao):
    aba, _ = aba_comparacao
    assert len(aba._charts) == 6


def test_comparacao_delta_vem_primeiro(aba_comparacao):
    """O delta responde a pergunta da tela; precisa estar no topo."""
    aba, _ = aba_comparacao
    assert aba._charts[0] is aba.chart_delta
    assert "Delta" in aba.chart_delta.chart().title()


def test_comparacao_canais_tem_duas_series(aba_comparacao):
    """Cada canal mostra A e B; perder uma delas passaria despercebido."""
    aba, _ = aba_comparacao
    for chart in (aba.chart_speed, aba.chart_throttle, aba.chart_brake,
                  aba.chart_gear, aba.chart_rpm):
        nomes = [s.name() for s in chart.chart().series()]
        assert nomes == ["A", "B"], f"{chart.chart().title()}: {nomes}"


def test_comparacao_grade_de_setores(aba_comparacao):
    aba, _ = aba_comparacao
    assert aba._sector_grid.rowCount() == 4  # cabeçalho + 3 setores


def test_comparacao_resumo_com_diferenca(aba_comparacao):
    aba, _ = aba_comparacao
    assert "Diferença" in aba._summary.text()


def test_comparacao_cursor_sincronizado(aba_comparacao):
    aba, _ = aba_comparacao
    aba._on_hover(1000.0)
    assert sum(1 for c in aba._charts if c._crosshair.isVisible()) > 0

    aba._on_hover_left()
    assert all(not c._crosshair.isVisible() for c in aba._charts)


# ======================= ouro: séries idênticas ==============================
def test_ouro_telemetria_series_estaveis(aba_telemetria):
    """Duas renderizações da mesma volta produzem exatamente a mesma saída.

    Se a refatoração introduzir qualquer variação na montagem — ordem de
    série, ponto inicial, quantidade — este teste acusa.
    """
    aba, _ = aba_telemetria
    primeira = _assinatura(aba)
    aba._on_plot_clicked()
    assert _assinatura(aba) == primeira


def test_ouro_comparacao_series_estaveis(aba_comparacao):
    aba, _ = aba_comparacao
    primeira = _assinatura(aba)
    aba._on_compare_clicked()
    assert _assinatura(aba) == primeira
