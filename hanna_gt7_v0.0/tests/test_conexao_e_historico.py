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
from gt7core.domain.models import Lap  # noqa: E402
from gt7core.telemetry.sources.base import ConnectionState  # noqa: E402
from gt7core.telemetry.sources.mock import synthetic_lap  # noqa: E402
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
