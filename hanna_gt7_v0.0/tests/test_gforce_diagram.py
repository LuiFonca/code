"""
O círculo de atrito.

Substitui o gráfico de força G por distância, que respondia "quanto de G houve
no metro 1.200" — pergunta que ninguém faz. A de engenharia é como o piloto
reparte um orçamento único de aderência entre frear, acelerar e curvar, e ela é
bidimensional.

O que se verifica aqui é geometria, porque é onde uma leitura errada nasce sem
parecer erro: um quadro não-quadrado transforma um envelope circular em elipse e
passa a sugerir assimetria de aderência que não existe; e um sinal trocado gira
a nuvem inteira em 180° sem que nada na tela denuncie — foi exatamente assim que
o diagrama saiu invertido nos dois eixos e só o console revelou.

A convenção verificada aqui é a de **peso**, não a de aceleração do carro: o
ponto marca para onde o peso é jogado, que é como o medidor do próprio GT7 se lê.
O motor entrega o oposto nos dois eixos, e a conversão mora na entrada do widget.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="o widget é Qt")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gt7app.design.tokens import get_theme  # noqa: E402
from gt7app.widgets.gforce import (  # noqa: E402
    MIN_SCALE_G,
    SCALE_STEPS_G,
    GForceDiagram,
)


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def diagrama(app: QApplication) -> GForceDiagram:  # noqa: ARG001
    widget = GForceDiagram(get_theme("dark"))
    widget.resize(400, 320)
    return widget


def _pixel(
    diagrama: GForceDiagram, g_lateral: float, g_longitudinal: float
) -> QPointF:
    """Onde o widget desenha um ponto entregue **na convenção do motor**.

    Passa pela API pública para incluir a conversão de convenção no que está
    sendo verificado; é a peça que o defeito de inversão morava.
    """
    diagrama.set_current((g_lateral, g_longitudinal))
    assert diagrama._current is not None  # noqa: SLF001
    return diagrama._to_pixel(*diagrama._current, diagrama._plot_rect())  # noqa: SLF001


class TestEscala:
    def test_volta_mansa_nao_finge_estar_no_limite(self, diagrama: GForceDiagram) -> None:
        """Sem piso, ruído de ±0,05 g encheria o quadro e pareceria o limite."""
        diagrama.set_points([(0.05, -0.03), (-0.04, 0.02)])
        assert diagrama.scale_g == MIN_SCALE_G

    def test_a_escala_acompanha_o_pico(self, diagrama: GForceDiagram) -> None:
        diagrama.set_points([(1.8, 0.0), (0.0, -1.2)])
        assert diagrama.scale_g > 1.8, "o ponto de pico ficaria fora do quadro"

    @pytest.mark.parametrize(
        ("pico", "esperado"),
        [(0.3, 2.0), (1.9, 2.0), (2.8, 3.0), (3.5, 4.0), (4.2, 5.0), (4.9, 5.0)],
    )
    def test_automatico_escolhe_o_menor_degrau_que_cabe(
        self, diagrama: GForceDiagram, pico: float, esperado: float
    ) -> None:
        """2,8 g desenha num quadro de 3; 4,2 g num de 5.

        Escolher o menor degrau que contém o pico mantém o envelope grande sem
        cortá-lo — e o degrau fixo é o que permite comparar duas voltas de olho.
        """
        diagrama.set_points([(pico, 0.0)])
        assert diagrama.scale_g == esperado

    def test_pico_acima_do_ultimo_degrau_nao_corta_o_dado(
        self, diagrama: GForceDiagram
    ) -> None:
        """6 g é implausível num carro, mas se chegar, ver o ponto importa mais
        que respeitar a lista de degraus."""
        diagrama.set_points([(6.2, 0.0)])
        assert diagrama.scale_g >= 6.2

    @pytest.mark.parametrize("limite", SCALE_STEPS_G)
    def test_limite_fixo_ganha_do_automatico(
        self, diagrama: GForceDiagram, limite: float
    ) -> None:
        diagrama.set_points([(0.4, 0.1)])
        diagrama.set_scale(limite)
        assert diagrama.scale_g == limite

    def test_voltar_ao_automatico(self, diagrama: GForceDiagram) -> None:
        diagrama.set_points([(2.8, 0.0)])
        diagrama.set_scale(5.0)
        assert diagrama.scale_g == 5.0

        diagrama.set_scale(None)
        assert diagrama.scale_g == 3.0

    def test_o_fundo_nunca_vira_hachura(self, diagrama: GForceDiagram) -> None:
        """Num quadro de 5 g, um anel a cada 0,5 g são dez circunferências: a
        nuvem some dentro da grade."""
        for limite in SCALE_STEPS_G:
            diagrama.set_scale(limite)
            assert len(diagrama._rings()) <= 5  # noqa: SLF001
            assert diagrama._rings()[-1] <= limite + 1e-9  # noqa: SLF001

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

    def test_frear_sobe_e_acelerar_desce(self, diagrama: GForceDiagram) -> None:
        """Freando o peso vai à frente, e a bola sobe.

        O motor entrega o oposto — longitudinal negativo na frenagem, porque é a
        aceleração do carro — e é o widget que converte. Este teste passa pela
        API pública de propósito: é lá que a convenção mora, e chamar
        `_to_pixel` direto (como a versão anterior fazia) pula justamente a peça
        que estava errada.
        """
        rect = diagrama._plot_rect()  # noqa: SLF001

        freando = _pixel(diagrama, 0.0, -1.0)
        acelerando = _pixel(diagrama, 0.0, 1.0)

        assert freando.y() < acelerando.y()
        assert freando.y() < rect.center().y()
        assert acelerando.y() > rect.center().y()

    def test_curva_a_direita_joga_a_bola_para_a_esquerda(
        self, diagrama: GForceDiagram
    ) -> None:
        """Numa curva à direita o peso vai para o lado esquerdo do carro.

        Por isso o rótulo daquele lado diz "curva à dir." e não "esquerda": ele
        nomeia a curva, não o lado da tela.
        """
        rect = diagrama._plot_rect()  # noqa: SLF001

        # Convenção do motor: lateral positivo é aceleração para a direita, que
        # é o que acontece numa curva à direita.
        curva_direita = _pixel(diagrama, 1.0, 0.0)
        curva_esquerda = _pixel(diagrama, -1.0, 0.0)

        assert curva_direita.x() < curva_esquerda.x()
        assert curva_direita.x() < rect.center().x()
        assert curva_esquerda.x() > rect.center().x()

    def test_a_leitura_numerica_concorda_com_a_posicao(
        self, diagrama: GForceDiagram
    ) -> None:
        """O número embaixo do gráfico e a posição da bola falam a mesma língua.

        Converter só na hora de pintar deixaria a bola no topo com o texto
        "long -1,20 g" logo abaixo — as duas metades do mesmo widget afirmando
        coisas opostas. Converter na entrada é o que impede isso.
        """
        rect = diagrama._plot_rect()  # noqa: SLF001
        diagrama.set_current((0.0, -1.0))  # motor: frenagem

        lateral, longitudinal = diagrama._current  # type: ignore[misc]  # noqa: SLF001
        assert longitudinal > 0, "frenagem é positiva na convenção do diagrama"
        alvo = diagrama._to_pixel(lateral, longitudinal, rect)  # noqa: SLF001
        assert alvo.y() < rect.center().y()

    def test_a_nuvem_usa_a_mesma_convencao_da_bola(
        self, diagrama: GForceDiagram
    ) -> None:
        """Senão a bola apareceria espelhada dentro da própria nuvem.

        São dois caminhos de entrada distintos — `set_points` para a volta,
        `set_current` para o cursor — e nada além deste teste obriga os dois a
        converterem igual.
        """
        diagrama.set_points([(0.8, -1.0)])
        diagrama.set_current((0.8, -1.0))
        assert diagrama._points[0] == diagrama._current  # noqa: SLF001

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
