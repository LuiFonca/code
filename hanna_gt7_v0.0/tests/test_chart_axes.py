"""
Os eixos dos gráficos de linha.

Um eixo é uma afirmação sobre os dados, e um eixo mal escolhido mente sem
parecer errado — motivo pelo qual quase tudo aqui nasceu de um relato de uso, e
não de leitura de código:

- **Eixo X ancorado em zero** desenhava metade do gráfico vazia ao vivo, porque
  o rastro guarda só os últimos 800 m. Relatado como "o gráfico fica assim na
  metade da volta".
- **Eixo Y colado nos dados** transformava 20 km/h de variação em serra que
  ocupava a altura toda. O olho lê inclinação, não números: a mesma volta
  parecia dramática ou mansa conforme a escala.
- **Escala de velocidade contínua** impedia comparar duas voltas de olho.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="os gráficos são Qt")

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gt7app.design.tokens import get_theme  # noqa: E402
from gt7app.widgets.charts import (  # noqa: E402
    SPEED_STEP_KMH,
    SPEED_TOP_MIN_KMH,
    DistanceChart,
    Series,
)


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def chart(app: QApplication, **kwargs: object) -> DistanceChart:  # noqa: ARG001
    widget = DistanceChart(get_theme("dark"), "t", **kwargs)  # type: ignore[arg-type]
    widget.resize(400, 200)
    return widget


def serie(pontos: list[tuple[float, float]]) -> list[Series]:
    return [Series("s", "#fff", pontos)]


class TestEixoYComecaNoZero:
    def test_dados_altos_e_estreitos_nao_viram_serra(self, app: QApplication) -> None:
        """180–200 km/h precisa ocupar a faixa de cima, não a altura inteira."""
        c = chart(app)
        c.set_series(serie([(0.0, 180.0), (100.0, 200.0), (200.0, 190.0)]))

        low, high = c._bounds  # noqa: SLF001
        assert low == 0.0, "o eixo precisa incluir o zero"
        assert high >= 200.0

    def test_valores_negativos_mantem_o_zero_no_eixo(self, app: QApplication) -> None:
        """O delta é negativo quando se está ganhando — zerar por baixo o
        cortaria. "Começar no zero" aqui significa **conter** o zero."""
        c = chart(app)
        c.set_series(serie([(0.0, -0.4), (100.0, 0.3)]))

        low, high = c._bounds  # noqa: SLF001
        assert low <= -0.4
        assert high >= 0.3
        assert low < 0.0 < high

    def test_faixa_forcada_continua_mandando(self, app: QApplication) -> None:
        """Pedais são 0–100 por definição, não por medição."""
        c = chart(app, y_range=(0.0, 100.0))
        c.set_series(serie([(0.0, 12.0), (100.0, 44.0)]))
        assert c._bounds == (0.0, 100.0)  # noqa: SLF001


class TestEscalaDeVelocidadeEmDegraus:
    def test_volta_normal_desenha_ate_300(self, app: QApplication) -> None:
        c = chart(app, y_step=SPEED_STEP_KMH, y_top_min=SPEED_TOP_MIN_KMH)
        c.set_series(serie([(0.0, 90.0), (100.0, 197.0)]))
        assert c._bounds == (0.0, 300.0)  # noqa: SLF001

    def test_passar_de_300_sobe_para_400(self, app: QApplication) -> None:
        c = chart(app, y_step=SPEED_STEP_KMH, y_top_min=SPEED_TOP_MIN_KMH)
        c.set_series(serie([(0.0, 90.0), (100.0, 317.0)]))
        assert c._bounds == (0.0, 400.0)  # noqa: SLF001

    def test_o_degrau_continua_subindo(self, app: QApplication) -> None:
        c = chart(app, y_step=SPEED_STEP_KMH, y_top_min=SPEED_TOP_MIN_KMH)
        c.set_series(serie([(0.0, 90.0), (100.0, 412.0)]))
        assert c._bounds == (0.0, 500.0)  # noqa: SLF001

    def test_duas_voltas_diferentes_compartilham_a_escala(
        self, app: QApplication
    ) -> None:
        """O ponto do degrau fixo.

        Com escala colada no pico, uma volta de 190 e outra de 250 km/h
        desenhariam curvas de altura parecida e a diferença sumiria. Com degrau,
        as duas usam 0–300 e a mais rápida *parece* mais rápida.
        """
        lenta = chart(app, y_step=SPEED_STEP_KMH, y_top_min=SPEED_TOP_MIN_KMH)
        rapida = chart(app, y_step=SPEED_STEP_KMH, y_top_min=SPEED_TOP_MIN_KMH)
        lenta.set_series(serie([(0.0, 80.0), (100.0, 190.0)]))
        rapida.set_series(serie([(0.0, 80.0), (100.0, 250.0)]))

        assert lenta._bounds == rapida._bounds  # noqa: SLF001


class TestEixoXAncoradoNosDados:
    def test_o_traco_ocupa_a_largura_toda(self, app: QApplication) -> None:
        """O defeito relatado: ao vivo, o rastro começa em 700 m e o gráfico
        desenhava de 0, deixando a metade esquerda vazia."""
        c = chart(app)
        c.set_series(serie([(700.0, 100.0), (1100.0, 120.0), (1500.0, 140.0)]))
        rect = QRectF(0.0, 0.0, 400.0, 200.0)

        primeiro = c._x_pixel(700.0, rect)  # noqa: SLF001
        ultimo = c._x_pixel(1500.0, rect)  # noqa: SLF001

        assert primeiro == pytest.approx(rect.left())
        assert ultimo == pytest.approx(rect.right())

    def test_o_cursor_volta_no_intervalo_certo(self, app: QApplication) -> None:
        """`_to_distance` é o inverso de `_x_pixel`; se divergirem, o cursor
        aponta para um lugar e o traço mostra outro."""
        c = chart(app)
        c.set_series(serie([(700.0, 1.0), (1500.0, 2.0)]))
        rect = QRectF(0.0, 0.0, 400.0, 200.0)

        for alvo in (700.0, 900.0, 1500.0):
            pixel = c._x_pixel(alvo, rect)  # noqa: SLF001
            assert c._to_distance(pixel, rect) == pytest.approx(alvo)  # noqa: SLF001

    def test_limpar_devolve_o_eixo_ao_zero(self, app: QApplication) -> None:
        c = chart(app)
        c.set_series(serie([(700.0, 1.0), (1500.0, 2.0)]))
        c.clear()
        assert c._min_x == 0.0  # noqa: SLF001


class TestUnidadeDoEixoX:
    def test_troca_de_unidade_repinta(self, app: QApplication) -> None:
        c = chart(app)
        assert c._x_unit == "m"  # noqa: SLF001
        c.set_x_unit("s")
        assert c._x_unit == "s"  # noqa: SLF001

    def test_segundos_ganham_uma_casa_decimal(self) -> None:
        """Com `.0f`, uma janela de 8 s virava "0 s, 2 s, 4 s" e perdia
        justamente a resolução que faz o eixo de tempo valer a pena."""
        from gt7app.widgets.charts import _format_x

        assert _format_x(3.25, "s") == "3.2 s"
        assert _format_x(1234.0, "m") == "1234 m"


class TestEixoEspelhado:
    """Canal com sinal precisa do mesmo espaço para os dois lados.

    O degrau sozinho produz eixo assimétrico — o teto mínimo só se aplica em
    cima —, e aí uma curva à direita de 30° desenha o dobro da altura de uma
    curva à esquerda de 30°. O gráfico passaria a afirmar uma assimetria de
    pilotagem que não existe, e nada denunciaria: os números do eixo estão
    certos; é a leitura de relance que mente. Apareceu no gráfico de volante,
    onde o eixo saiu de −90 a 180 com o traço quase todo em torno do zero.
    """

    def test_eixo_com_sinal_fica_simetrico(self, app: QApplication) -> None:
        c = chart(app, y_step=90.0, y_top_min=180.0, y_symmetric=True)
        c.set_series(serie([(0.0, 5.0), (10.0, -3.0)]))

        low, high = c._bounds  # noqa: SLF001
        assert low == -high

    def test_o_lado_maior_manda_nos_dois(self, app: QApplication) -> None:
        """Espelhar é pelo extremo, não pelo positivo.

        Uma volta anti-horária vive quase toda no negativo; ancorar no maior
        positivo achataria a volta inteira contra a borda de baixo.
        """
        c = chart(app, y_step=30.0, y_top_min=60.0, y_symmetric=True)
        c.set_series(serie([(0.0, 4.0), (10.0, -95.0)]))

        assert c._bounds == (-120.0, 120.0)  # noqa: SLF001

    def test_canal_que_so_sobe_nao_espelha(self, app: QApplication) -> None:
        """Velocidade espelhada desperdiçaria metade da altura desenhando um
        território negativo onde nenhuma amostra pode cair."""
        c = chart(app, y_step=100.0)
        c.set_series(serie([(0.0, 0.0), (10.0, 250.0)]))

        assert c._bounds[0] == 0.0  # noqa: SLF001

    def test_serie_toda_zero_nao_colapsa_o_eixo(self, app: QApplication) -> None:
        """Altura zero faria a divisão da projeção estourar."""
        c = chart(app, y_symmetric=True)
        c.set_series(serie([(0.0, 0.0), (10.0, 0.0)]))

        low, high = c._bounds  # noqa: SLF001
        assert high > low
