"""
O círculo de atrito.

Substitui o gráfico de força G por distância, que respondia "quanto de G houve
no metro 1.200" — pergunta que ninguém faz. A de engenharia é como o piloto
reparte um orçamento único de aderência entre frear, acelerar e curvar, e ela é
bidimensional.

O que se verifica aqui é geometria, porque é onde uma leitura errada nasce sem
parecer erro: um quadro não-quadrado transforma um envelope circular em elipse e
passa a sugerir assimetria de aderência que não existe; e um sinal invertido põe
a frenagem em cima, onde a intuição de quem pilota espera aceleração.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="o widget é Qt")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gt7app.design.tokens import get_theme  # noqa: E402
from gt7app.widgets.gforce import MIN_SCALE_G, GForceDiagram  # noqa: E402


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def diagrama(app: QApplication) -> GForceDiagram:  # noqa: ARG001
    widget = GForceDiagram(get_theme("dark"))
    widget.resize(400, 320)
    return widget


class TestEscala:
    def test_volta_mansa_nao_finge_estar_no_limite(self, diagrama: GForceDiagram) -> None:
        """Sem piso, ruído de ±0,05 g encheria o quadro e pareceria o limite."""
        diagrama.set_points([(0.05, -0.03), (-0.04, 0.02)])
        assert diagrama.scale_g == MIN_SCALE_G

    def test_a_escala_acompanha_o_pico(self, diagrama: GForceDiagram) -> None:
        diagrama.set_points([(1.8, 0.0), (0.0, -1.2)])
        assert diagrama.scale_g > 1.8, "o ponto de pico ficaria fora do quadro"

    def test_pico_e_o_g_combinado_e_nao_o_maior_de_um_eixo(
        self, diagrama: GForceDiagram
    ) -> None:
        """1,0 lateral com 1,0 longitudinal é 1,41 g de aderência usada.

        Olhar um eixo de cada vez subestimaria — e é exatamente o que o gráfico
        antigo, com duas linhas separadas por distância, fazia.
        """
        diagrama.set_points([(1.0, 1.0)])
        assert diagrama.peak_g == pytest.approx(2**0.5, rel=1e-6)

    def test_sem_pontos_nao_estoura(self, diagrama: GForceDiagram) -> None:
        assert diagrama.peak_g == 0.0
        assert diagrama.scale_g == MIN_SCALE_G


class TestGeometria:
    def test_o_quadro_e_quadrado(self, diagrama: GForceDiagram) -> None:
        """Num retângulo, 1 g lateral ocuparia mais pixels que 1 g longitudinal.

        O envelope circular viraria elipse e o gráfico passaria a afirmar uma
        assimetria de aderência inexistente — distorção que não é estética.
        """
        diagrama.resize(600, 300)
        rect = diagrama._plot_rect()  # noqa: SLF001
        assert rect.width() == pytest.approx(rect.height())

        diagrama.resize(300, 600)
        rect = diagrama._plot_rect()  # noqa: SLF001
        assert rect.width() == pytest.approx(rect.height())

    def test_acelerar_sobe_e_frear_desce(self, diagrama: GForceDiagram) -> None:
        """A convenção que a intuição de quem pilota espera.

        O Y do Qt cresce para baixo, então sem a inversão de sinal a frenagem
        apareceria no topo — e o gráfico ficaria de cabeça para baixo sem nada
        na tela denunciando.
        """
        diagrama.set_points([(0.0, 1.0), (0.0, -1.0)])
        rect = diagrama._plot_rect()  # noqa: SLF001

        acelerando = diagrama._to_pixel(0.0, 1.0, rect)  # noqa: SLF001
        freando = diagrama._to_pixel(0.0, -1.0, rect)  # noqa: SLF001

        assert acelerando.y() < freando.y()
        assert acelerando.y() < rect.center().y()
        assert freando.y() > rect.center().y()

    def test_direita_vai_para_a_direita(self, diagrama: GForceDiagram) -> None:
        rect = diagrama._plot_rect()  # noqa: SLF001
        assert (
            diagrama._to_pixel(1.0, 0.0, rect).x()  # noqa: SLF001
            > diagrama._to_pixel(-1.0, 0.0, rect).x()  # noqa: SLF001
        )

    def test_a_origem_cai_no_centro(self, diagrama: GForceDiagram) -> None:
        rect = diagrama._plot_rect()  # noqa: SLF001
        origem = diagrama._to_pixel(0.0, 0.0, rect)  # noqa: SLF001
        assert origem.x() == pytest.approx(rect.center().x())
        assert origem.y() == pytest.approx(rect.center().y())

    def test_sobra_espaco_para_a_leitura_numerica(
        self, diagrama: GForceDiagram
    ) -> None:
        """A linha de valores é desenhada abaixo do quadro.

        Sem reservar a faixa, ela saía fora do widget — foi o que a primeira
        renderização mostrou, junto com o texto cortado nas duas pontas.
        """
        diagrama.resize(400, 320)
        rect = diagrama._plot_rect()  # noqa: SLF001
        assert rect.bottom() + 16 <= diagrama.height()


class TestPintura:
    """Pintar de verdade: um `QPainter` mal fechado só aparece assim."""

    def _pintar(self, diagrama: GForceDiagram) -> None:
        from PySide6.QtGui import QPixmap

        alvo = QPixmap(diagrama.size())
        diagrama.render(alvo)

    def test_pinta_vazio(self, diagrama: GForceDiagram) -> None:
        self._pintar(diagrama)

    def test_pinta_com_nuvem_e_bola(self, diagrama: GForceDiagram) -> None:
        diagrama.set_points([(x / 100, (x % 37) / 50 - 0.4) for x in range(-90, 90)])
        diagrama.set_current((0.6, -0.4))
        self._pintar(diagrama)

    def test_pinta_so_com_a_bola(self, diagrama: GForceDiagram) -> None:
        """Ao vivo, antes da primeira volta fechar, não há nuvem — só o carro."""
        diagrama.set_current((0.2, 0.1))
        self._pintar(diagrama)

    def test_limpar_volta_ao_estado_inicial(self, diagrama: GForceDiagram) -> None:
        diagrama.set_points([(1.0, 1.0)])
        diagrama.set_current((1.0, 1.0))
        diagrama.clear()

        assert diagrama.peak_g == 0.0
        assert diagrama.scale_g == MIN_SCALE_G
        self._pintar(diagrama)


class TestLigacaoComAPagina:
    def test_a_analise_alimenta_a_nuvem_e_o_cursor(self, app: QApplication, tmp_path) -> None:  # noqa: ANN001, ARG002
        """A volta inteira vira nuvem, e o cursor move a bola."""
        from gt7app.application import build_core, build_gui
        from gt7core.config.settings import Settings
        from gt7core.telemetry.sources.mock import synthetic_lap

        settings = Settings()
        settings.storage.database_path = tmp_path / "t.db"
        settings.storage.telemetry_path = tmp_path / "tel"
        settings.env_path = tmp_path / ".env"

        core = build_core(settings)
        try:
            window = build_gui(core)
            for frame in synthetic_lap(lap_time_ms=20_000):
                core.engine.on_frame(frame)

            page = window._pages[1]  # noqa: SLF001
            pontos = list(core.engine._buffer)  # noqa: SLF001
            page._points = pontos  # noqa: SLF001
            page._corners = []  # noqa: SLF001
            page._populate(20_000)  # noqa: SLF001

            assert len(page._gforce._points) == len(pontos)  # noqa: SLF001
            assert page._gforce._current is None  # noqa: SLF001

            page._on_hover(pontos[len(pontos) // 2].distance_m)  # noqa: SLF001
            assert page._gforce._current is not None  # noqa: SLF001

            page._on_hover_left()  # noqa: SLF001
            assert page._gforce._current is None  # noqa: SLF001
            window.close()
        finally:
            core.close()
