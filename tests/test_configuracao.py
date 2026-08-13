"""
Configuração centralizada.

Os testes de padrão existem para uma razão específica: ao mover 8 constantes
espalhadas por 6 arquivos para um lugar só, é fácil trocar um valor sem
perceber. Cada `assert` abaixo fixa o comportamento que o app tinha antes da
centralização.
"""

import json

import pytest

from src.domain.config import AppConfig, load_config, save_config


# ---------------------------------------------------------- padrões travados
def test_padroes_batem_com_os_valores_originais():
    """Centralizar não pode mudar nenhum comportamento.

    Estes eram os valores das constantes espalhadas antes da Fase 1.
    """
    c = AppConfig()
    assert c.ps_ip == "192.168.15.156"
    assert c.heartbeat_interval_s == 10
    assert c.keep_best_per_track == 5
    assert c.keep_recent_per_track == 50
    assert c.max_g == 5.0
    assert c.stale_timeout_s == 1.0
    assert c.slip_saturation == 1.0
    assert c.max_plot_points == 2000
    assert c.num_sectors == 3


def test_config_e_imutavel():
    """Config trocada em runtime por engano vira bug difícil de rastrear."""
    c = AppConfig()
    with pytest.raises(Exception):
        c.ps_ip = "10.0.0.1"


# ---------------------------------------------------------------- carga
def test_sem_arquivo_usa_padroes(tmp_path):
    c = load_config(tmp_path / "nao-existe.json")
    assert c == AppConfig()


def test_arquivo_parcial_completa_com_padroes(tmp_path):
    """Config antiga com campos novos ausentes precisa continuar carregando."""
    caminho = tmp_path / "config.json"
    caminho.write_text(json.dumps({"ps_ip": "10.0.0.5"}))

    c = load_config(caminho)
    assert c.ps_ip == "10.0.0.5"
    assert c.keep_recent_per_track == 50, "campo ausente cai no padrão"


def test_arquivo_corrompido_cai_no_padrao_sem_derrubar(tmp_path):
    """Config quebrada não pode impedir o app de abrir."""
    caminho = tmp_path / "config.json"
    caminho.write_text("{isto não é json")

    c = load_config(caminho)
    assert c == AppConfig()


def test_valor_de_tipo_errado_cai_no_padrao(tmp_path):
    caminho = tmp_path / "config.json"
    caminho.write_text(json.dumps({"keep_recent_per_track": "cinquenta"}))

    c = load_config(caminho)
    assert c.keep_recent_per_track == 50


def test_valor_fora_de_faixa_e_recusado(tmp_path):
    """Retenção zero apagaria todo o histórico a cada gravação."""
    caminho = tmp_path / "config.json"
    caminho.write_text(
        json.dumps({"keep_recent_per_track": 0, "max_g": -3, "num_sectors": 99})
    )

    c = load_config(caminho)
    assert c.keep_recent_per_track == 50
    assert c.max_g == 5.0
    assert c.num_sectors == 3


def test_ida_e_volta_preserva_valores(tmp_path):
    caminho = tmp_path / "config.json"
    original = AppConfig(ps_ip="10.0.0.9", keep_recent_per_track=120, max_g=3.5)

    save_config(original, caminho)
    assert load_config(caminho) == original


# ------------------------------------------------- efeito real no comportamento
def test_limite_de_retencao_muda_a_poda(database, tracks, make_lap):
    """Config não pode ser decorativa: mudar o limite tem que mudar a poda."""
    from src.infrastructure.repositories.sqlite_lap_repository import (
        SqliteLapRepository,
    )

    track_id = tracks.get_or_create("T")

    apertado = SqliteLapRepository(database, keep_best=1, keep_recent=2)
    for i in range(6):
        apertado.save(
            make_lap(track_id=track_id, lap_time_ms=95000 - i * 100, samples=20)
        )
    com_limite_baixo = len(apertado.get_by_track(track_id))

    assert com_limite_baixo <= 3, f"esperado no máximo 3, veio {com_limite_baixo}"


def test_teto_de_g_configuravel_afeta_a_saturacao(source, laps, session, bus, qapp):
    """O teto de força G precisa vir da config, não de constante fixa."""
    from src.application.services.telemetry_service import TelemetryService
    from src.domain.models.track import Track
    from tests.conftest import FakeFrame

    config = AppConfig(max_g=1.0)
    svc = TelemetryService(
        telemetry_source=source,
        lap_repository=laps,
        session_manager=session,
        event_bus=bus,
        config=config,
    )
    session.set_track(Track(id=1, name="T"))
    svc.start()
    source.feed(FakeFrame(lap_count=1, current_lap_ms=0, velocity_x=10.0))
    source.feed(FakeFrame(lap_count=1, current_lap_ms=16, velocity_x=300.0))
    qapp.processEvents()

    assert all(abs(p.g_longitudinal) <= 1.0 for p in svc._buffer)
    svc.stop()


def test_saturacao_de_slip_configuravel(laps, tracks, make_lap, bus, qapp):
    from src.application.viewmodels.telemetry_viewmodel import TelemetryViewModel

    track_id = tracks.get_or_create("T")
    lap_id = laps.save(make_lap(track_id=track_id, samples=50, tire_slip_fl=0.25))

    vm = TelemetryViewModel(laps, bus, tracks, config=AppConfig(slip_saturation=0.5))
    vm.set_track(track_id)
    vm.load_lap(lap_id)

    # Com saturação em 0,5 um slip de 0,25 vale 50 %; com 1,0 valeria 25 %.
    pontos = vm.slip_points("tire_slip_fl")
    assert pontos[0][1] == pytest.approx(50.0)


def test_teto_de_pontos_configuravel(laps, tracks, make_lap, bus, qapp):
    from src.application.viewmodels.telemetry_viewmodel import TelemetryViewModel

    track_id = tracks.get_or_create("T")
    lap_id = laps.save(make_lap(track_id=track_id, samples=3000))

    vm = TelemetryViewModel(laps, bus, tracks, config=AppConfig(max_plot_points=200))
    vm.set_track(track_id)
    vm.load_lap(lap_id)

    assert len(vm.points_for("speed_kmh")) <= 201


# ------------------------------------------------------- tela de preferências
def test_dialogo_carrega_e_devolve_valores(qapp):
    from src.presentation.preferences_dialog import PreferencesDialog

    original = AppConfig(ps_ip="10.0.0.7", keep_recent_per_track=120, max_g=3.0)
    dialog = PreferencesDialog(original)

    assert dialog.result_config() == original, "abrir e fechar não pode alterar nada"

    dialog._keep_recent.setValue(200)
    dialog._max_g.setValue(2.5)
    nova = dialog.result_config()
    assert nova.keep_recent_per_track == 200
    assert nova.max_g == 2.5
    assert nova.ps_ip == "10.0.0.7", "campos não tocados permanecem"


def test_dialogo_restaura_padroes(qapp):
    from src.presentation.preferences_dialog import PreferencesDialog

    dialog = PreferencesDialog(AppConfig(ps_ip="10.0.0.7", max_g=1.0))
    dialog._restore_defaults()
    assert dialog.result_config() == AppConfig()


def test_dialogo_recusa_ip_vazio(qapp):
    """IP em branco produziria tentativa de conexão sem destino."""
    from src.presentation.preferences_dialog import PreferencesDialog

    dialog = PreferencesDialog(AppConfig(ps_ip="10.0.0.7"))
    dialog._ip.setText("   ")
    assert dialog.result_config().ps_ip == "10.0.0.7"
