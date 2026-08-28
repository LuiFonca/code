"""
Testes da fábrica de fontes e do adaptador Qt.

A fábrica é o único ponto do sistema que sabe qual fonte está em uso — é ela
que transforma "trocar ao vivo por replay" numa mudança de configuração.

O adaptador Qt só é exercitado se PySide6 estiver instalado. Que os testes
passem sem ele é a própria demonstração de que o núcleo é headless: 130 dos
testes deste projeto rodam num ambiente sem interface gráfica nenhuma.
"""

from __future__ import annotations

import importlib.util
import socket
import threading
import time
from pathlib import Path

import pytest

from gt7core.config.settings import Settings, TelemetryConfig
from gt7core.telemetry.recording import ReplayTelemetrySource, SessionRecorder
from gt7core.telemetry.sources.base import TelemetrySource
from gt7core.telemetry.sources.factory import (
    TelemetrySourceError,
    create_telemetry_source,
)
from gt7core.telemetry.sources.mock import MockTelemetrySource, synthetic_lap
from gt7core.telemetry.sources.udp import Gt7UdpTelemetrySource


def _porta_livre() -> int:
    """Uma porta alta que o sistema garante livre agora.

    Não a 33740 de propósito: rodar a suíte com o HANNA GT7 aberto na mesma
    máquina faria estes testes reprovarem por disputa de porta, o que é uma
    reprovação sobre o ambiente e não sobre o código.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def settings_with(**telemetry: object) -> Settings:
    return Settings(telemetry=TelemetryConfig(**telemetry))  # type: ignore[arg-type]


class TestFabricaDeFontes:
    def test_mock_e_o_padrao(self) -> None:
        source = create_telemetry_source(settings_with())

        assert isinstance(source, MockTelemetrySource)

    def test_udp_com_ip(self) -> None:
        source = create_telemetry_source(
            settings_with(source="udp", ps_ip="192.168.1.50", receive_port=40000)
        )

        assert isinstance(source, Gt7UdpTelemetrySource)
        assert source.ps_ip == "192.168.1.50"

    def test_udp_sem_ip_falha_com_instrucao(self) -> None:
        """Não existe IP padrão razoável: um inventado tentaria falar com a
        máquina de outra pessoa na rede."""
        with pytest.raises(TelemetrySourceError, match="GT7_PS_IP"):
            create_telemetry_source(settings_with(source="udp"))

    def test_replay_com_arquivo(self, tmp_path: Path) -> None:
        path = tmp_path / "s.gt7rec"
        with SessionRecorder(path) as recorder:
            for frame in synthetic_lap(lap_time_ms=500):
                recorder.record(frame)

        source = create_telemetry_source(
            settings_with(source="replay"), replay_path=path
        )

        assert isinstance(source, ReplayTelemetrySource)

    def test_replay_sem_caminho_falha(self) -> None:
        with pytest.raises(TelemetrySourceError, match="caminho"):
            create_telemetry_source(settings_with(source="replay"))

    def test_replay_com_arquivo_inexistente_falha(self, tmp_path: Path) -> None:
        with pytest.raises(TelemetrySourceError, match="não encontrada"):
            create_telemetry_source(
                settings_with(source="replay"), replay_path=tmp_path / "nada.gt7rec"
            )

    def test_fonte_desconhecida_lista_as_opcoes(self) -> None:
        """Falhar na hora, com as opções — em vez de cair num padrão silencioso
        e deixar o usuário sem entender por que não chega telemetria."""
        with pytest.raises(TelemetrySourceError, match="'mock', 'udp' ou 'replay'"):
            create_telemetry_source(settings_with(source="iracing"))

    def test_nome_da_fonte_ignora_caixa_e_espaco(self) -> None:
        source = create_telemetry_source(settings_with(source="  MOCK  "))

        assert isinstance(source, MockTelemetrySource)

    @pytest.mark.parametrize("kind", ["mock", "udp", "replay"])
    def test_toda_fonte_satisfaz_o_mesmo_contrato(
        self, kind: str, tmp_path: Path
    ) -> None:
        """A propriedade que sustenta §40 e §42: quem consome não distingue."""
        path = tmp_path / "s.gt7rec"
        with SessionRecorder(path) as recorder:
            recorder.record(next(iter(synthetic_lap())))

        source = create_telemetry_source(
            settings_with(source=kind, ps_ip="127.0.0.1", receive_port=45999),
            replay_path=path,
        )

        assert isinstance(source, TelemetrySource)
        assert hasattr(source, "start")
        assert hasattr(source, "stop")
        assert source.is_running is False


HAS_QT = importlib.util.find_spec("PySide6") is not None


@pytest.mark.skipif(not HAS_QT, reason="PySide6 não instalado — o núcleo é headless")
class TestAdaptadorQt:
    """Só roda com PySide6 instalado.

    Que a suíte inteira passe sem ele é a demonstração viva do que a Fase 1
    entregou: o núcleo não precisa de interface gráfica para existir.
    """

    def test_adaptador_entrega_na_thread_da_interface(self) -> None:
        from PySide6.QtCore import QCoreApplication

        from gt7app.adapters.qt_bus import QtEventBusAdapter
        from gt7core.events.bus import EventBus
        from gt7core.telemetry.engine import TelemetryReceived

        app = QCoreApplication.instance() or QCoreApplication([])
        bus = EventBus()
        adapter = QtEventBusAdapter(bus)

        received: list[object] = []
        adapter.subscribe(TelemetryReceived, received.append)

        frame = next(iter(synthetic_lap()))
        from gt7core.telemetry.engine import TelemetryEngine

        engine = TelemetryEngine(bus)
        engine.on_frame(frame)
        app.processEvents()

        assert len(received) == 1
        adapter.close()

    def test_close_desliga_do_barramento(self) -> None:
        from gt7app.adapters.qt_bus import QtEventBusAdapter
        from gt7core.events.bus import EventBus
        from gt7core.telemetry.engine import TelemetryReceived

        bus = EventBus()
        adapter = QtEventBusAdapter(bus)
        adapter.subscribe(TelemetryReceived, lambda _: None)
        assert bus.handler_count(TelemetryReceived) == 1

        adapter.close()

        # Sem isto, o barramento seguiria emitindo para um QObject destruído —
        # que em Qt é acesso a ponteiro morto, não exceção Python.
        assert bus.handler_count(TelemetryReceived) == 0


class TestAPortaFicaLivreDepoisDeParar:
    """`stop()` tem de **garantir** a porta liberada, não pedir educadamente.

    O contrato antigo era sinalizar e esperar: a thread só percebia a parada ao
    sair do `recvfrom`, e se não saísse dentro do prazo do `join`, `stop()`
    voltava com a thread viva e a porta ocupada. A captura seguinte bindava um
    segundo socket na mesma porta — o `SO_REUSEADDR` permite — e o sistema
    passava a entregar cada pacote a **um** dos dois.

    O sintoma disso é telemetria que chega na máquina e não chega na tela, com o
    teste de conexão dizendo que está tudo certo — porque o teste abre o socket
    dele e recebe normalmente. Trocar o IP em Configurações passa por este
    caminho, e cada troca podia deixar mais um ouvinte fantasma.
    """

    @staticmethod
    def _porta_esta_livre(porta: int) -> bool:
        """Binda **sem** `SO_REUSEADDR`: é o que denuncia ocupação de verdade.

        Com a opção ligada o bind passa mesmo com outro socket na porta, que é
        justamente como o defeito escapava — o teste diria "livre" sobre uma
        porta disputada.
        """
        sonda = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sonda.bind(("0.0.0.0", porta))
        except OSError:
            return False
        else:
            return True
        finally:
            sonda.close()

    def test_parar_devolve_a_porta(self) -> None:
        porta = _porta_livre()
        fonte = Gt7UdpTelemetrySource("127.0.0.1", receive_port=porta)

        fonte.start()
        for _ in range(200):
            if fonte._socket is not None:  # noqa: SLF001
                break
            time.sleep(0.01)
        assert fonte._socket is not None, "a captura não chegou a abrir a porta"  # noqa: SLF001

        fonte.stop()

        assert not fonte.is_running, "a thread continuou viva"
        assert self._porta_esta_livre(porta), "a porta ficou presa"

    def test_parar_e_comecar_de_novo_funciona(self) -> None:
        """É o que a troca de IP faz: parar e subir de novo, várias vezes.

        Cada ciclo que deixasse uma thread para trás somaria um ouvinte
        disputando os mesmos pacotes.
        """
        porta = _porta_livre()
        fonte = Gt7UdpTelemetrySource("127.0.0.1", receive_port=porta)

        for _ in range(3):
            fonte.start()
            for _ in range(200):
                if fonte._socket is not None:  # noqa: SLF001
                    break
                time.sleep(0.01)
            fonte.stop()
            assert not fonte.is_running

        vivas = [
            t for t in threading.enumerate()
            if t.name == "Gt7UdpTelemetrySource" and t.is_alive()
        ]
        assert vivas == [], f"{len(vivas)} threads de captura ficaram vivas"
        assert self._porta_esta_livre(porta)

    def test_parar_e_rapido(self) -> None:
        """Fechar o socket desbloqueia o `recvfrom` na hora.

        Sinalizar e esperar levava até 3 s — tempo em que a janela fica parada
        com o botão ainda verde, e o clique parece não ter pego.
        """
        porta = _porta_livre()
        fonte = Gt7UdpTelemetrySource("127.0.0.1", receive_port=porta)
        fonte.start()
        for _ in range(200):
            if fonte._socket is not None:  # noqa: SLF001
                break
            time.sleep(0.01)

        inicio = time.monotonic()
        fonte.stop()

        assert time.monotonic() - inicio < 1.0, "parar demorou como antes"
