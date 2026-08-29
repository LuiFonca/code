"""
Conexão automática, indicador de estado e a coluna de carro no histórico.

O tema comum é **não afirmar o que não foi verificado**. O botão só fica verde
com pacote do console na mão, porque UDP não tem aperto de mão e abrir o socket
"funciona" mesmo com o PS5 desligado; e a conexão automática só age quando há
IP configurado e a fonte é a rede, senão o programa encheria a tela de dados
sintéticos antes de alguém pedir.
"""

from __future__ import annotations

from datetime import datetime

import pytest

pytest.importorskip("PySide6", reason="são páginas Qt")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gt7app.application import build_core, build_gui  # noqa: E402
from gt7app.pages.history import CAR_COLUMN, HISTORY_COLUMNS  # noqa: E402
from gt7core.config.settings import Settings  # noqa: E402
from gt7core.domain.models import Lap, Track  # noqa: E402
from gt7core.telemetry.engine import LapBoundaryDetected  # noqa: E402
from gt7core.telemetry.sources.base import ConnectionState  # noqa: E402
from gt7core.telemetry.sources.mock import (  # noqa: E402
    synthetic_lap,
    synthetic_session,
)
from tests.conftest import dispose_window  # noqa: E402


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _settings(tmp_path, **telemetry):  # noqa: ANN001, ANN003
    s = Settings()
    s.storage.database_path = tmp_path / "t.db"
    s.storage.telemetry_path = tmp_path / "tel"
    s.env_path = tmp_path / ".env"
    for chave, valor in telemetry.items():
        setattr(s.telemetry, chave, valor)
    return s


class TestIndicadorDeConexao:
    """Verde é uma afirmação: "os pacotes estão chegando"."""

    @pytest.mark.parametrize(
        ("estado", "verde"),
        [
            (ConnectionState.RECEIVING.value, True),
            (ConnectionState.CONNECTING.value, False),
            (ConnectionState.NO_SIGNAL.value, False),
            (ConnectionState.ERROR.value, False),
            (ConnectionState.DISCONNECTED.value, False),
        ],
    )
    def test_so_fica_verde_recebendo(
        self, app: QApplication, tmp_path, estado: str, verde: bool  # noqa: ANN001, ARG002
    ) -> None:
        core = build_core(_settings(tmp_path))
        try:
            window = build_gui(core)
            live = window._pages[0]  # noqa: SLF001

            live._paint_connection(estado)  # noqa: SLF001
            estilo = live._start_button.styleSheet()  # noqa: SLF001

            assert (live.theme.palette.green in estilo) is verde
            dispose_window(window)
        finally:
            core.close()

    def test_o_rotulo_diz_o_estado(self, app: QApplication, tmp_path) -> None:  # noqa: ANN001, ARG002
        core = build_core(_settings(tmp_path))
        try:
            window = build_gui(core)
            live = window._pages[0]  # noqa: SLF001

            live._paint_connection(ConnectionState.RECEIVING.value)  # noqa: SLF001
            assert live._start_button.text() == "CONECTADO"  # noqa: SLF001

            live._paint_connection(ConnectionState.DISCONNECTED.value)  # noqa: SLF001
            assert live._start_button.text() == "Conectar"  # noqa: SLF001
            assert live._start_button.styleSheet() == ""  # noqa: SLF001
            dispose_window(window)
        finally:
            core.close()


class TestConexaoAutomatica:
    """A **decisão** é verificada sem abrir socket.

    `try_autoconnect` abre uma porta UDP; um teste que precise abrir porta para
    verificar uma regra de negócio testa a rede, não a regra. Por isso a decisão
    mora em `should_autoconnect`, e é ela que estes testes exercitam.
    """

    @pytest.mark.parametrize(
        ("source", "ps_ip", "esperado"),
        [
            ("udp", "192.168.15.156", True),
            ("udp", "   ", False),
            ("udp", "", False),
            # Iniciar o gerador sozinho encheria a tela de dados inventados
            # antes de alguém pedir — o mal-entendido que o selo existe para
            # desfazer.
            ("mock", "192.168.15.156", False),
            ("replay", "192.168.15.156", False),
        ],
    )
    def test_quando_conectar_sozinho(
        self, app: QApplication, tmp_path, source: str, ps_ip: str, esperado: bool  # noqa: ANN001, ARG002
    ) -> None:
        core = build_core(_settings(tmp_path))
        try:
            window = build_gui(core)
            live = window._pages[0]  # noqa: SLF001

            core.settings.telemetry.source = source
            core.settings.telemetry.ps_ip = ps_ip

            assert live.should_autoconnect() is esperado
            dispose_window(window)
        finally:
            core.close()

    def test_sem_configuracao_nao_toca_na_fonte(
        self, app: QApplication, tmp_path  # noqa: ANN001, ARG002
    ) -> None:
        core = build_core(_settings(tmp_path))
        try:
            window = build_gui(core)
            live = window._pages[0]  # noqa: SLF001

            assert live.try_autoconnect() is False
            assert not core.source.is_running
            dispose_window(window)
        finally:
            core.close()


class TestCarroNoHistorico:
    def test_a_tabela_tem_coluna_de_carro(self, app: QApplication, tmp_path) -> None:  # noqa: ANN001, ARG002
        core = build_core(_settings(tmp_path))
        try:
            track_id = core.tracks.get_or_create("Interlagos")
            car_id = core.cars.get_or_create("Porsche 911 GT3")
            for frame in synthetic_lap(lap_time_ms=92_000):
                core.engine.on_frame(frame)
            core.laps.save(
                Lap(
                    track_id=track_id,
                    car_id=car_id,
                    lap_time_ms=92_000,
                    start_time=datetime.now(),
                    points=list(core.engine._buffer),  # noqa: SLF001
                )
            )

            window = build_gui(core)
            pagina = window._pages[3]  # noqa: SLF001
            pagina.refresh()

            assert HISTORY_COLUMNS[CAR_COLUMN] == "Carro"
            item = pagina._table.item(0, CAR_COLUMN)  # noqa: SLF001
            assert item is not None
            assert item.text() == "Porsche 911 GT3"
            dispose_window(window)
        finally:
            core.close()

    def test_volta_sem_carro_nao_estoura(self, app: QApplication, tmp_path) -> None:  # noqa: ANN001, ARG002
        """Voltas antigas foram gravadas antes de o carro ser identificado."""
        core = build_core(_settings(tmp_path))
        try:
            window = build_gui(core)
            pagina = window._pages[3]  # noqa: SLF001

            assert pagina.car_name(None) == "—"
            assert pagina.car_name(9999) == "—"
            dispose_window(window)
        finally:
            core.close()


class TestEstadoDoABS:
    """O ABS é dito em toda volta, e não só quando há bit novo.

    Silêncio aqui se lê como "o ABS não atuou" — uma afirmação que ninguém
    mediu, porque o bit dele não está identificado no pacote.
    """

    def _pagina_com_volta(self, core, window, *, flags: int):  # noqa: ANN001, ANN202
        from gt7core.domain.models import Lap as _Lap

        track_id = core.tracks.get_or_create("Interlagos")
        core.engine.reset()
        for frame in synthetic_lap(lap_time_ms=92_000):
            core.engine.on_frame(frame)
        pontos = []
        for p in core.engine._buffer:  # noqa: SLF001
            campos = {f: getattr(p, f) for f in p.__slots__}
            campos["flags"] = flags
            pontos.append(type(p)(**campos))

        lap_id = core.laps.save(
            _Lap(
                track_id=track_id,
                lap_time_ms=92_000,
                start_time=datetime.now(),
                points=pontos,
            )
        )
        pagina = window._pages[1]  # noqa: SLF001
        pagina.refresh()
        pagina._on_lap_selected(lap_id)  # noqa: SLF001
        return pagina

    def test_sem_bit_desconhecido_explica_a_ausencia(
        self, app: QApplication, tmp_path  # noqa: ANN001, ARG002
    ) -> None:
        from gt7core.telemetry.protocol import FLAG_CAR_ON_TRACK

        core = build_core(_settings(tmp_path))
        try:
            window = build_gui(core)
            pagina = self._pagina_com_volta(core, window, flags=FLAG_CAR_ON_TRACK)

            texto = pagina._flags_hint.text()  # noqa: SLF001
            assert "ABS" in texto
            assert "não há indicador" in texto
            dispose_window(window)
        finally:
            core.close()

    def test_bit_desconhecido_vira_candidato(
        self, app: QApplication, tmp_path  # noqa: ANN001, ARG002
    ) -> None:
        from gt7core.telemetry.protocol import FLAG_CAR_ON_TRACK

        core = build_core(_settings(tmp_path))
        try:
            window = build_gui(core)
            pagina = self._pagina_com_volta(
                core, window, flags=FLAG_CAR_ON_TRACK | (1 << 13)
            )

            texto = pagina._flags_hint.text()  # noqa: SLF001
            assert "candidatos" in texto
            assert "13" in texto
            dispose_window(window)
        finally:
            core.close()

    def test_travar_o_cursor_nao_apaga_a_linha_do_abs(
        self, app: QApplication, tmp_path  # noqa: ANN001, ARG002
    ) -> None:
        """As duas mensagens dividiam o mesmo rótulo, e o clique apagava o ABS."""
        from gt7core.telemetry.protocol import FLAG_CAR_ON_TRACK

        core = build_core(_settings(tmp_path))
        try:
            window = build_gui(core)
            pagina = self._pagina_com_volta(core, window, flags=FLAG_CAR_ON_TRACK)
            antes = pagina._flags_hint.text()  # noqa: SLF001

            pagina._on_click(1200.0)  # noqa: SLF001

            assert pagina._flags_hint.text() == antes  # noqa: SLF001
            assert "travado" in pagina._cursor_hint.text()  # noqa: SLF001
            dispose_window(window)
        finally:
            core.close()


class TestOTesteNaoDisputaAPortaComACaptura:
    """O "FUNCIONANDO" que aparecia com a tela vazia.

    A sonda de conexão abre a **mesma porta 33740** que a captura. Com a
    captura de pé, os dois sockets ficam ligados na porta — o `SO_REUSEADDR`
    permite — e o sistema entrega cada pacote a um deles. A sonda recebe,
    anuncia sucesso, e a aba Ao vivo fica vazia porque os pacotes que ela
    precisava foram para a sonda.

    O veredito verde é a pior parte: ele manda procurar o defeito em qualquer
    lugar menos onde ele está.
    """

    def test_com_captura_rodando_nao_abre_segundo_socket(
        self, app: QApplication, tmp_path, monkeypatch  # noqa: ANN001, ARG002
    ) -> None:
        core = build_core(_settings(tmp_path))
        try:
            window = build_gui(core)
            pagina = window._pages[5]  # noqa: SLF001

            sondou = False

            def nunca(*args: object, **kwargs: object) -> None:
                nonlocal sondou
                sondou = True

            monkeypatch.setattr(pagina._pool, "start", nunca)  # noqa: SLF001
            monkeypatch.setattr(
                type(core.source), "is_running", property(lambda self: True)
            )

            pagina._ps_ip.setText("192.168.15.156")  # noqa: SLF001
            pagina._on_test()  # noqa: SLF001

            assert not sondou, "abriu um segundo socket na porta da captura"
            assert "captura" in pagina._test_result.text().lower()  # noqa: SLF001

            dispose_window(window)
        finally:
            core.close()

    def test_captura_parada_sonda_normalmente(
        self, app: QApplication, tmp_path, monkeypatch  # noqa: ANN001, ARG002
    ) -> None:
        """Sem captura de pé não há disputa — a sonda é a única resposta."""
        core = build_core(_settings(tmp_path))
        try:
            window = build_gui(core)
            pagina = window._pages[5]  # noqa: SLF001

            sondou = False

            def marca(*args: object, **kwargs: object) -> None:
                nonlocal sondou
                sondou = True

            monkeypatch.setattr(pagina._pool, "start", marca)  # noqa: SLF001
            monkeypatch.setattr(
                type(core.source), "is_running", property(lambda self: False)
            )

            pagina._ps_ip.setText("192.168.15.156")  # noqa: SLF001
            pagina._on_test()  # noqa: SLF001

            assert sondou, "a sonda devia ter rodado"

            dispose_window(window)
        finally:
            core.close()


class TestGravacaoDeVoltas:
    """As voltas param de ser gravadas sem que nada na tela diga por quê.

    O portão é `SessionManager.can_persist`, que exige pista definida. Com o
    autoconectar disparando na abertura — quando o campo de pista nasce vazio
    de propósito — ninguém chamava `set_track`, e toda volta virava
    `LapDiscarded("nenhuma pista definida")`. Em silêncio: o motivo já vinha
    pronto no evento e nenhuma tela o exibia.
    """

    def test_escolher_a_pista_no_campo_define_a_sessao(
        self, app: QApplication, tmp_path  # noqa: ANN001, ARG002
    ) -> None:
        """A pista era lida **só** no instante do clique em Conectar.

        Escolher no dropdown depois disso não fazia nada — o combo não tinha
        ligação nenhuma, e nada além de `_on_start` lia aquele campo.
        """
        core = build_core(_settings(tmp_path))
        try:
            window = build_gui(core)
            live = window._pages[0]  # noqa: SLF001

            assert core.session_manager.track is None

            live._track_input.setCurrentText("Interlagos")  # noqa: SLF001
            live._on_track_chosen()  # noqa: SLF001

            assert core.session_manager.track is not None
            assert core.session_manager.track.name == "Interlagos"
            assert core.session_manager.can_persist

            dispose_window(window)
        finally:
            core.close()

    def test_a_primeira_volta_com_pista_escolhida_e_gravada(
        self, app: QApplication, tmp_path  # noqa: ANN001, ARG002
    ) -> None:
        """Com pista definida, a volta 1 grava — não só a partir da segunda."""
        core = build_core(_settings(tmp_path))
        try:
            track_id = core.tracks.get_or_create("Interlagos")
            core.session_manager.set_track(Track(id=track_id, name="Interlagos"))
            core.start()

            for frame in synthetic_session(lap_count=2):
                core.engine.on_frame(frame)

            gravadas = core.laps.get_by_track(track_id)
            core.stop()

            assert gravadas, "nenhuma volta foi gravada"
        finally:
            core.close()

    def test_a_deteccao_de_pista_roda_antes_do_gravador(
        self, app: QApplication, tmp_path  # noqa: ANN001, ARG002
    ) -> None:
        """Ordem de inscrição é a garantia — o barramento despacha nela.

        A detecção vinha pelo adaptador Qt, que entrega um turno de evento
        adiante: o gravador já tinha decidido descartar quando a pista era
        aplicada, e a primeira volta de toda sessão se perdia.
        """
        core = build_core(_settings(tmp_path))
        try:
            handlers = core.bus._handlers[LapBoundaryDetected]  # noqa: SLF001
            nomes = [getattr(h, "__name__", type(h).__name__) for h in handlers]

            assert "nomear_pista_pelo_comprimento" in nomes
            detector = nomes.index("nomear_pista_pelo_comprimento")
            gravador = next(
                i for i, n in enumerate(nomes) if "lap_boundary" in n
            )
            assert detector < gravador, f"ordem errada: {nomes}"
        finally:
            core.close()

    def test_o_aviso_aparece_quando_a_gravacao_esta_bloqueada(
        self, app: QApplication, tmp_path  # noqa: ANN001, ARG002
    ) -> None:
        """Sem isto o programa sabia o motivo e guardava para si."""
        core = build_core(_settings(tmp_path))
        try:
            window = build_gui(core)
            live = window._pages[0]  # noqa: SLF001

            core.start()
            live._refresh_recording_hint()  # noqa: SLF001

            assert live._recording_badge.isVisibleTo(live)  # noqa: SLF001
            assert "NÃO ESTÃO SENDO GRAVADAS" in live._recording_badge.text()  # noqa: SLF001

            live._track_input.setCurrentText("Interlagos")  # noqa: SLF001
            live._on_track_chosen()  # noqa: SLF001

            assert not live._recording_badge.isVisibleTo(live)  # noqa: SLF001

            core.stop()
            dispose_window(window)
        finally:
            core.close()


class TestDuploCliqueAbreAAnalise:
    """Do Histórico direto para a volta, sem reencontrá-la na outra aba.

    O caminho antes era: anotar o número da volta, trocar de aba, reencontrar a
    pista no combo e procurar a volta na lista — quatro passos para uma
    intenção só.
    """

    def test_duplo_clique_leva_a_analise_com_a_volta(
        self, app: QApplication, tmp_path  # noqa: ANN001, ARG002
    ) -> None:
        core = build_core(_settings(tmp_path))
        try:
            track_id = core.tracks.get_or_create("Interlagos")
            for tempo in (92_000, 93_500):
                core.engine.reset()
                for frame in synthetic_lap(lap_time_ms=tempo):
                    core.engine.on_frame(frame)
                core.laps.save(
                    Lap(
                        track_id=track_id,
                        lap_time_ms=tempo,
                        start_time=datetime.now(),
                        points=list(core.engine._buffer),  # noqa: SLF001
                    )
                )

            window = build_gui(core)
            window.show()
            historico = window._pages[3]  # noqa: SLF001
            historico.refresh()

            alvo = historico._laps[1]  # noqa: SLF001
            historico._on_lap_double_clicked(  # noqa: SLF001
                historico._table.item(1, 0)  # noqa: SLF001
            )

            analise = window._pages[1]  # noqa: SLF001
            assert window._stack.currentIndex() == 1, "não navegou para a Análise"  # noqa: SLF001
            assert analise._selector.current_lap_id() == alvo.id  # noqa: SLF001

            dispose_window(window)
        finally:
            core.close()

    def test_linha_invalida_nao_estoura(
        self, app: QApplication, tmp_path  # noqa: ANN001, ARG002
    ) -> None:
        """Tabela vazia, ou linha fora da lista, não pode derrubar a página."""
        core = build_core(_settings(tmp_path))
        try:
            window = build_gui(core)
            historico = window._pages[3]  # noqa: SLF001

            assert historico._lap_at_row(0) is None  # noqa: SLF001
            assert historico._lap_at_row(-1) is None  # noqa: SLF001

            dispose_window(window)
        finally:
            core.close()
