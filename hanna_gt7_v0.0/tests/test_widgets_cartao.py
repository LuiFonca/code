"""
Cartão: limpar o conteúdo tem que limpar de verdade.

O defeito que este arquivo tranca era invisível em teste e gritante na tela.
`clear_content` tirava os rótulos do layout com `takeAt` e agendava a
destruição com `deleteLater`. Entre as duas coisas o widget continua **filho do
cartão** e continua pintando na última geometria que teve — e como `deleteLater`
só roda quando o laço de eventos chega na hora dele, cada reconstrução da tela
depositava mais uma camada de texto sobre a anterior.

Ninguém percebeu enquanto os textos eram curtos e iguais: duas camadas do mesmo
"evoluindo 0.068 s por volta" desenham em cima uma da outra e parecem uma. Só
quando "A trabalhar" passou a escrever frases diferentes por curva é que o
cartão virou um borrão.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="é um widget Qt")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from gt7app.widgets.cards import Card  # noqa: E402


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _textos(card: Card) -> list[str]:
    """Todo QLabel que ainda é filho do cartão — inclusive os que sumiram do
    layout mas continuam pintando."""
    return [w.text() for w in card.findChildren(QLabel)]


class TestLimpezaDeConteudo:
    def test_o_conteudo_antigo_some_do_cartao_e_nao_so_do_layout(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        card = Card("Título")
        card.add(QLabel("primeira leva"))
        card.clear_content()
        card.add(QLabel("segunda leva"))

        assert "primeira leva" not in _textos(card)
        assert "segunda leva" in _textos(card)

    def test_reconstrucoes_seguidas_nao_empilham(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        """O caso real: a página se reconstrói a cada mexida num seletor."""
        card = Card("A trabalhar")
        for rodada in range(5):
            card.clear_content()
            for i in range(3):
                card.add(QLabel(f"rodada {rodada} item {i}"))
            app.processEvents()

        restantes = [t for t in _textos(card) if t.startswith("rodada")]
        assert restantes == [
            "rodada 4 item 0",
            "rodada 4 item 1",
            "rodada 4 item 2",
        ]

    def test_o_titulo_sobrevive_a_limpeza(self, app: QApplication) -> None:  # noqa: ARG002
        card = Card("Título")
        card.add(QLabel("conteúdo"))
        card.clear_content()
        assert "Título" in _textos(card)

    def test_o_conteudo_novo_entra_depois_do_titulo(
        self, app: QApplication  # noqa: ARG002
    ) -> None:
        """O esticador precisa continuar por último, senão o `add` seguinte
        insere antes do título e ele vai parar no rodapé do cartão."""
        card = Card("Título")
        card.add(QLabel("primeiro"))
        card.clear_content()
        card.add(QLabel("novo"))

        layout = card._layout  # noqa: SLF001
        rotulos = [
            layout.itemAt(i).widget().text()
            for i in range(layout.count())
            if layout.itemAt(i).widget() is not None
        ]
        assert rotulos == ["Título", "novo"]
