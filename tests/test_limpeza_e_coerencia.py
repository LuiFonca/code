"""
Correções de coerência achadas na revisão do código.

Quatro defeitos do mesmo tipo: a peça existia e funcionava isolada, mas não
estava ligada ao resto — e por isso os testes de unidade passavam enquanto o
app real não fazia o que prometia.
"""

import pytest

from src.application.events.event_bus import EventBus
from src.application.services.track_identifier import TrackIdentifier
from src.application.viewmodels.comparison_viewmodel import ComparisonViewModel
from src.application.viewmodels.history_viewmodel import HistoryViewModel
from src.domain.config import AppConfig
from src.infrastructure.repositories.sqlite_lap_repository import SqliteLapRepository
from tests.test_reconhecimento_pista import _pista_circular
from src.domain.models.lap import Lap


def test_capacidade_do_tanque_chega_a_aba_de_telemetria(qapp, tmp_path):
    """O percentual de combustível dependia de um método que ninguém chamava."""
    import src.main as M
    from src.application.events.events import TelemetryReceived
    from src.infrastructure.repositories.sqlite_database import SqliteDatabase
    from tests.conftest import FakeFrame, make_point

    original = M.SqliteDatabase
    M.SqliteDatabase = lambda *a, **k: SqliteDatabase(tmp_path / "app.db")
    try:
        janela = M.build_application()
    finally:
        M.SqliteDatabase = original
    try:
        aba = next(
            janela.tabs.widget(i) for i in range(janela.tabs.count())
            if janela.tabs.tabText(i) == "Telemetria"
        )
        assert aba._vm._tank_capacity is None
        janela._bus.publish(
            TelemetryReceived(
                point=make_point(1, 10, 90000, 3600.0),
                frame=FakeFrame(fuel_capacity=64.0),
            )
        )
        assert aba._vm._tank_capacity == 64.0
    finally:
        janela._service.stop()


def test_comparacao_respeita_setores_da_pista(laps, tracks, make_lap, qapp):
    """A Comparação dividia sempre em três partes iguais, ignorando o ajuste."""
    tid = tracks.get_or_create("Pista")
    tracks.set_sector_fractions(tid, [0.2, 0.5, 0.8, 1.0])
    a = laps.save(make_lap(track_id=tid, lap_time_ms=90000, samples=200))
    b = laps.save(make_lap(track_id=tid, lap_time_ms=91000, samples=200))

    bus = EventBus()
    vm = ComparisonViewModel(laps, bus, tracks)
    try:
        vm.set_track(tid)
        vm.compare(a, b)
        limites = vm.result.sector_boundaries
        assert len(limites) == 4
        assert limites[0] == pytest.approx(0.2 * limites[-1])
    finally:
        vm.dispose()
        bus.clear()


def test_comparacao_respeita_numero_de_setores_das_preferencias(
    laps, tracks, make_lap, qapp
):
    tid = tracks.get_or_create("Pista")
    a = laps.save(make_lap(track_id=tid, lap_time_ms=90000, samples=200))
    b = laps.save(make_lap(track_id=tid, lap_time_ms=91000, samples=200))

    bus = EventBus()
    vm = ComparisonViewModel(laps, bus, tracks, AppConfig(num_sectors=5))
    try:
        vm.set_track(tid)
        vm.compare(a, b)
        assert len(vm.result.sectors) == 5
    finally:
        vm.dispose()
        bus.clear()


def test_voltas_sem_pista_tambem_sao_podadas(database, make_lap):
    """O grupo sem pista não tinha teto: `track_id = ?` nunca casa com NULL."""
    repo = SqliteLapRepository(database, keep_best=2, keep_recent=2)
    for i in range(10):
        repo.save(make_lap(track_id=None, lap_time_ms=90000 + i * 100, samples=20))
    assert len(repo.get_by_track(None)) <= 4


def test_atribuir_pista_ensina_o_tracado(laps, tracks, qapp):
    """Responder à mão precisa servir para as próximas voltas."""
    pontos = _pista_circular(600.0, n=200)
    lap_id = laps.save(
        Lap(track_id=None, lap_time_ms=90000, is_complete=True, points=pontos)
    )

    bus = EventBus()
    identificador = TrackIdentifier(tracks)
    vm = HistoryViewModel(laps, bus, tracks, identificador)
    try:
        assert vm.assign_track_by_name(lap_id, "Interlagos") is True
        tid = tracks.get_or_create("Interlagos")
        assert tracks.get_fingerprint(tid) is not None, "o traçado não foi aprendido"
        achado = identificador.identify(_pista_circular(600.0, n=200, desvio_linha=5.0))
        assert achado is not None and achado[0] == tid
    finally:
        vm.dispose()
        bus.clear()


def test_fechar_a_janela_solta_as_inscricoes(qapp, tmp_path):
    import src.main as M
    from PySide6.QtGui import QCloseEvent
    from src.application.events.events import TelemetryReceived
    from src.infrastructure.repositories.sqlite_database import SqliteDatabase

    original = M.SqliteDatabase
    M.SqliteDatabase = lambda *a, **k: SqliteDatabase(tmp_path / "app.db")
    try:
        janela = M.build_application()
    finally:
        M.SqliteDatabase = original

    antes = len(janela._bus._handlers.get(TelemetryReceived, []))
    janela.closeEvent(QCloseEvent())
    depois = len(janela._bus._handlers.get(TelemetryReceived, []))
    assert depois < antes
