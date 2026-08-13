"""
Reconexão automática.

Os testes de pré-modificação abaixo travam o ciclo de vida da thread de
captura. É a área de maior risco do roteiro: reconectar sem encerrar a thread
anterior deixa duas disputando a porta 33740, e o sintoma — pacotes divididos
entre dois sockets — é intermitente e difícil de rastrear.

Por isso a contagem de threads é verificada explicitamente depois de vários
ciclos de queda e retorno, não só o resultado funcional.
"""

import errno
import socket
import threading
import time

import pytest

from src.application.events.events import ConnectionStateChanged
from src.domain.config import AppConfig


class _SocketFalso:
    """Socket que nunca recebe nada e pode falhar no envio sob comando."""

    falhar_envio = False

    def setsockopt(self, *a): pass
    def bind(self, *a): pass
    def settimeout(self, *a): pass
    def close(self): pass

    def sendto(self, data, addr):
        if _SocketFalso.falhar_envio:
            raise OSError(errno.EHOSTUNREACH, "No route to host")
        return len(data)

    def recvfrom(self, n):
        time.sleep(0.02)
        raise socket.timeout()


@pytest.fixture
def socket_falso(monkeypatch):
    import src.infrastructure.telemetry.listener_thread as lt

    _SocketFalso.falhar_envio = False
    monkeypatch.setattr(lt.socket, "socket", lambda *a, **k: _SocketFalso())
    monkeypatch.setattr(lt, "HEARTBEAT_INTERVAL_INITIAL", 0.05)
    monkeypatch.setattr(lt, "SOCKET_TIMEOUT", 1)
    yield _SocketFalso
    _SocketFalso.falhar_envio = False


def _threads_de_captura() -> int:
    return sum(1 for t in threading.enumerate() if t.is_alive() and "Qt" not in t.name)


# ==================== pré-modificação: ciclo de vida =========================
def test_pre_start_e_idempotente(qapp, socket_falso):
    """Chamar start duas vezes não pode criar duas threads na mesma porta."""
    from src.infrastructure.telemetry.listener_thread import Gt7TelemetrySource

    fonte = Gt7TelemetrySource("127.0.0.1")
    fonte.start()
    primeira = fonte._thread
    fonte.start()

    assert fonte._thread is primeira, "start repetido não pode trocar a thread"
    fonte.stop()


def test_pre_stop_encerra_a_thread(qapp, socket_falso):
    from src.infrastructure.telemetry.listener_thread import Gt7TelemetrySource

    fonte = Gt7TelemetrySource("127.0.0.1")
    fonte.start()
    thread = fonte._thread
    assert fonte.is_running

    fonte.stop()
    assert not fonte.is_running
    assert fonte._thread is None
    assert thread.isFinished() or not thread.isRunning()


def test_pre_stop_sem_start_e_seguro(qapp, socket_falso):
    from src.infrastructure.telemetry.listener_thread import Gt7TelemetrySource

    Gt7TelemetrySource("127.0.0.1").stop()


def test_pre_watchdog_detecta_silencio_e_recupera(qapp, bus, flush):
    """Comportamento do watchdog antes da Fase 4, que deve continuar valendo."""
    from src.application.viewmodels.live_viewmodel import LiveViewModel
    from src.application.events.events import TelemetryReceived
    from tests.conftest import make_point

    vm = LiveViewModel(bus, AppConfig(stale_timeout_s=0.15))
    eventos = []
    vm.stale_entered.connect(lambda: eventos.append("stale"))
    vm.stale_exited.connect(lambda: eventos.append("ok"))

    bus.publish(ConnectionStateChanged(state="recebendo"))
    ponto = make_point(0, 1, 1000, 10.0)
    bus.publish(TelemetryReceived(point=ponto, frame=None))
    flush(0.05)
    assert not vm.is_stale

    flush(0.45)
    assert vm.is_stale
    assert "stale" in eventos

    bus.publish(TelemetryReceived(point=ponto, frame=None))
    flush(0.05)
    assert not vm.is_stale
    assert "ok" in eventos
    vm.dispose()


# ==================== pós-modificação: reconexão =============================
def test_reconecta_apos_queda(qapp, source, laps, session, bus, flush):
    """Fonte que cai e volta deve religar sozinha."""
    from src.application.services.telemetry_service import TelemetryService

    config = AppConfig(
        auto_reconnect=True, reconnect_initial_delay_s=0.5, stale_timeout_s=0.1
    )
    svc = TelemetryService(source, laps, session, bus, config=config)
    svc.start()
    assert svc.is_running

    # A fonte "cai": para sozinha, sem ninguém ter pedido.
    source.stop()
    assert not svc.is_running

    svc._on_source_status("sem_sinal")
    flush(1.2)

    assert svc.is_running, "o serviço deveria ter religado a fonte"
    svc.stop()


def test_desconexao_manual_nao_reconecta(qapp, source, laps, session, bus, flush):
    """Parar a pedido do usuário não pode disparar reconexão."""
    from src.application.services.telemetry_service import TelemetryService

    config = AppConfig(auto_reconnect=True, reconnect_initial_delay_s=0.3)
    svc = TelemetryService(source, laps, session, bus, config=config)
    svc.start()
    svc.stop()

    flush(1.0)
    assert not svc.is_running, "desconexão manual não pode religar"


def test_reconexao_desligada_na_config(qapp, source, laps, session, bus, flush):
    from src.application.services.telemetry_service import TelemetryService

    config = AppConfig(auto_reconnect=False, reconnect_initial_delay_s=0.2)
    svc = TelemetryService(source, laps, session, bus, config=config)
    svc.start()
    source.stop()
    svc._on_source_status("sem_sinal")

    flush(0.8)
    assert not svc.is_running
    svc.stop()


def test_recuo_cresce_a_cada_tentativa(qapp, source, laps, session, bus):
    """Console desligado não pode inundar a rede nem o log."""
    from src.application.services.telemetry_service import TelemetryService

    config = AppConfig(
        auto_reconnect=True,
        reconnect_initial_delay_s=1.0,
        reconnect_max_delay_s=8.0,
    )
    svc = TelemetryService(source, laps, session, bus, config=config)

    atrasos = [svc._next_reconnect_delay() for _ in range(6)]
    assert atrasos == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0], (
        f"recuo exponencial com teto, veio {atrasos}"
    )

    svc._reset_reconnect_backoff()
    assert svc._next_reconnect_delay() == 1.0, "reconexão bem-sucedida zera o recuo"


def test_ciclos_de_queda_nao_vazam_thread(qapp, socket_falso, flush):
    """O sintoma mais perigoso: duas threads na mesma porta.

    Verifica a contagem de threads vivas, não só o resultado funcional — uma
    thread órfã continuaria consumindo pacotes sem aparecer em nenhum teste
    de comportamento.
    """
    from src.infrastructure.telemetry.listener_thread import Gt7TelemetrySource

    antes = _threads_de_captura()
    fonte = Gt7TelemetrySource("127.0.0.1")

    for _ in range(5):
        fonte.start()
        flush(0.08)
        fonte.stop()
        flush(0.08)

    assert not fonte.is_running
    depois = _threads_de_captura()
    assert depois <= antes, f"threads vazaram: {antes} -> {depois}"


def test_estado_de_reconexao_chega_na_interface(
    qapp, source, laps, session, bus, collect, flush
):
    """O usuário precisa ver que o app está tentando, e qual tentativa."""
    from src.application.services.telemetry_service import TelemetryService

    eventos = collect(ConnectionStateChanged)
    config = AppConfig(auto_reconnect=True, reconnect_initial_delay_s=0.3)
    svc = TelemetryService(source, laps, session, bus, config=config)
    svc.start()
    source.stop()
    svc._on_source_status("sem_sinal")
    flush(0.9)

    estados = [e.state for e in eventos]
    assert "reconectando" in estados
    mensagens = [e.message for e in eventos if e.state == "reconectando"]
    assert any("tentativa" in m.lower() for m in mensagens)
    svc.stop()


def test_cancelar_reconexao(qapp, source, laps, session, bus, flush):
    """Deve haver como desistir sem fechar o app."""
    from src.application.services.telemetry_service import TelemetryService

    config = AppConfig(auto_reconnect=True, reconnect_initial_delay_s=5.0)
    svc = TelemetryService(source, laps, session, bus, config=config)
    svc.start()
    source.stop()
    svc._on_source_status("sem_sinal")
    flush(0.1)
    assert svc.is_reconnecting

    svc.cancel_reconnect()
    assert not svc.is_reconnecting
    flush(0.3)
    assert not svc.is_running
    svc.stop()


# ==================== interface durante a reconexão ==========================
def test_interface_mostra_reconectando_e_oferece_cancelar(qapp, tmp_path, flush):
    """O usuário precisa ver que o app tenta, e ter como desistir."""
    import src.main as M
    from src.infrastructure.repositories.sqlite_database import SqliteDatabase

    caminho = tmp_path / "app.db"
    original = M.SqliteDatabase
    M.SqliteDatabase = lambda *a, **k: SqliteDatabase(caminho)
    try:
        w = M.build_application()
    finally:
        M.SqliteDatabase = original

    w._on_connection_changed(
        ConnectionStateChanged(state="reconectando", message="tentativa 1")
    )
    assert w.status_pill.text() == "↻ Reconectando"
    assert w.stop_button.text() == "Cancelar"
    assert not w.connect_button.isEnabled()

    w._on_connection_changed(ConnectionStateChanged(state="recebendo"))
    assert w.stop_button.text() == "Desconectar"
    w._service.stop()
