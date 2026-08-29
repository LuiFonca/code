"""
Escala vertical: quando ancorar no zero e quando deixar flutuar.

As duas regras existem para o mesmo defeito visto de dois lados, e é por isso
que as duas precisam de teste.

**Ancorada** protege canais em que zero é referência. Sem ela, uma volta cuja
velocidade varia de 180 a 200 km/h desenha uma serra que ocupa a altura toda:
o olho lê inclinação, não números, e 20 km/h viram drama.

**Flutuante** protege canais em que zero não é referência nenhuma. Aderência
vive entre 90% e 105%; num eixo de 0 a 125 essa faixa cabe em poucos pixels, e
um travamento de 8 pontos desenha igual a um de 2. `y_min_span` é o que impede
a flutuante de recriar o problema da primeira: com ele, uma volta limpa desenha
como o que é — quase uma reta.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="é um widget Qt")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gt7app.design.tokens import DARK  # noqa: E402
from gt7app.widgets.charts import DistanceChart, Series  # noqa: E402


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


class _Tema:
    """Tema mínimo: o gráfico só usa a paleta para pintar."""

    palette = DARK


def _grafico(**kwargs) -> DistanceChart:  # noqa: ANN003
    return DistanceChart(_Tema(), "t", **kwargs)


def _com(valores: list[float], **kwargs) -> tuple[float, float]:  # noqa: ANN003
    chart = _grafico(**kwargs)
    chart.set_series(
        [Series("s", "#fff", [(float(i), v) for i, v in enumerate(valores)])]
    )
    return chart._bounds  # noqa: SLF001


class TestAncoradaNoZero:
    def test_o_zero_entra_no_quadro_mesmo_longe_dos_dados(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        baixo, _ = _com([180.0, 190.0, 200.0])
        assert baixo == 0.0

    def test_e_o_padrao(self, app: QApplication) -> None:  # noqa: ARG002
        baixo, _ = _com([95.0, 100.0, 105.0])
        assert baixo == 0.0


class TestFlutuante:
    def test_o_quadro_segue_os_dados_e_ignora_o_zero(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        """O defeito que motivou a opção: aderência achatada contra o zero."""
        baixo, alto = _com([92.0, 100.0, 108.0], y_anchor_zero=False)
        assert baixo > 50.0, f"o quadro não deveria descer até o zero (baixo={baixo})"
        assert baixo < 92.0 <= 108.0 < alto

    def test_a_amplitude_minima_impede_que_ruido_vire_drama(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        """Volta limpa: 2 pontos de variação não podem ocupar o quadro inteiro."""
        baixo, alto = _com([99.0, 100.0, 101.0], y_anchor_zero=False, y_min_span=20.0)
        assert alto - baixo >= 20.0

    def test_dados_mais_largos_que_o_piso_expandem_o_quadro(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        """O piso é piso, não teto: um travamento de verdade abre a escala."""
        baixo, alto = _com([60.0, 100.0, 108.0], y_anchor_zero=False, y_min_span=20.0)
        assert alto - baixo > 48.0
        assert baixo < 60.0 and alto > 108.0

    def test_a_faixa_minima_fica_centrada_nos_dados(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        """Crescer a partir do zero encostaria o traço numa borda."""
        baixo, alto = _com([100.0, 100.0, 100.0], y_anchor_zero=False, y_min_span=20.0)
        centro = (baixo + alto) / 2.0
        assert centro == pytest.approx(100.0, abs=0.5)

    def test_valores_iguais_nao_produzem_quadro_de_altura_zero(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        baixo, alto = _com([100.0, 100.0], y_anchor_zero=False)
        assert alto > baixo

    def test_sem_series_nao_quebra(self, app: QApplication) -> None:  # noqa: ARG002
        chart = _grafico(y_anchor_zero=False, y_min_span=20.0)
        chart.set_series([])
        baixo, alto = chart._bounds  # noqa: SLF001
        assert alto > baixo


class TestNaoRegressao:
    def test_velocidade_continua_ancorada_e_com_degrau(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        """A escala que já existia não pode mudar de comportamento."""
        baixo, alto = _com([180.0, 197.0], y_step=100.0, y_top_min=300.0)
        assert (baixo, alto) == (0.0, 300.0)

    def test_canal_com_sinal_continua_espelhado(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        baixo, alto = _com(
            [-12.0, 40.0], y_step=30.0, y_top_min=60.0, y_symmetric=True
        )
        assert (baixo, alto) == (-60.0, 60.0)
