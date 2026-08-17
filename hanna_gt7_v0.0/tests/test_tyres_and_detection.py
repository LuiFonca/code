"""
Temperatura de pneus, detecção de pista e faixa de voltas.

Três pedidos que compartilham um princípio: **sugerir sem decidir errado**.

A cor do pneu responde "estou na janela?" de relance, que é a única forma de
essa informação ser útil a 200 km/h. A detecção de pista sugere quando o
comprimento é ambíguo, em vez de escolher — batizar a sessão com a pista errada
mistura voltas de circuitos diferentes sob um rótulo e nada na tela denuncia. E
a faixa de voltas existe porque um perfil sobre a sessão inteira descreve um
piloto que não existe, misturando reconhecimento com ritmo.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="os widgets são Qt")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gt7app.design.tokens import get_theme  # noqa: E402
from gt7app.widgets.tyres import (  # noqa: E402
    COLD_BELOW_C,
    IDEAL_BELOW_C,
    WARM_BELOW_C,
    TyreTemperatures,
    temperature_color,
    temperature_label,
)


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


class TestFaixasDeTemperatura:
    @pytest.mark.parametrize(
        ("celsius", "esperado"),
        [
            (40.0, "frio"),
            (COLD_BELOW_C - 0.1, "frio"),
            (COLD_BELOW_C, "ideal"),
            (IDEAL_BELOW_C - 0.1, "ideal"),
            (IDEAL_BELOW_C, "quente"),
            (WARM_BELOW_C - 0.1, "quente"),
            (WARM_BELOW_C, "superaquecido"),
            (140.0, "superaquecido"),
        ],
    )
    def test_os_limites_nao_tem_buraco_nem_sobreposicao(
        self, celsius: float, esperado: str
    ) -> None:
        """Cada grau cai em exatamente uma faixa, e a troca é no limite exato."""
        assert temperature_label(celsius) == esperado

    def test_cada_faixa_tem_cor_propria(self, app: QApplication) -> None:  # noqa: ARG002
        palette = get_theme("dark").palette
        cores = {
            temperature_color(c, palette) for c in (40.0, 80.0, 100.0, 130.0)
        }
        assert len(cores) == 4, "duas faixas compartilhando cor seriam indistinguíveis"


class TestWidgetDePneus:
    def test_as_quatro_rodas_ficam_na_posicao_fisica(self, app: QApplication) -> None:  # noqa: ARG002
        """DE em cima à esquerda, TD embaixo à direita.

        Trocar a ordem faria o gráfico apontar o pneu de trás quando o quente é
        o da frente — erro que ninguém desconfia olhando.
        """
        widget = TyreTemperatures(get_theme("dark"))
        widget.resize(300, 200)
        de, dd, te, td = widget._wheel_rects()  # noqa: SLF001

        assert de.left() < dd.left(), "DE precisa estar à esquerda de DD"
        assert te.left() < td.left()
        assert de.top() < te.top(), "dianteiros acima dos traseiros"
        assert dd.top() < td.top()

    def test_pinta_com_e_sem_dados(self, app: QApplication) -> None:  # noqa: ARG002
        from PySide6.QtGui import QPixmap

        widget = TyreTemperatures(get_theme("dark"))
        widget.resize(300, 200)

        widget.render(QPixmap(widget.size()))
        widget.set_temperatures(65.0, 82.0, 101.0, 125.0)
        assert widget.temperatures == (65.0, 82.0, 101.0, 125.0)
        widget.render(QPixmap(widget.size()))

        widget.clear()
        assert widget.temperatures is None
        widget.render(QPixmap(widget.size()))
