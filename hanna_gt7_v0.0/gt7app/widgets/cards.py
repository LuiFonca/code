"""
Blocos de construção das páginas.

Nenhum destes widgets sabe de telemetria — recebem texto e cor já decididos.
É o que permite reusá-los na página ao vivo, na de análise e na de comparação
sem carregar regra de negócio junto.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..design.theme import (
    OBJ_BADGE,
    OBJ_CARD,
    OBJ_CARD_TITLE,
    OBJ_CARD_UNIT,
    OBJ_CARD_VALUE,
    OBJ_MONO,
    OBJ_PAGE_SUBTITLE,
    OBJ_PAGE_TITLE,
    OBJ_SECTION_TITLE,
)
from ..design.tokens import Space, Theme

PLACEHOLDER = "—"


class Card(QFrame):
    """Superfície elevada com título opcional. A moldura de tudo."""

    def __init__(self, title: str = "") -> None:
        super().__init__()
        self.setObjectName(OBJ_CARD)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(
            Space.LG.px, Space.MD.px, Space.LG.px, Space.MD.px
        )
        self._layout.setSpacing(Space.SM.px)
        self._has_title = bool(title)

        if title:
            label = QLabel(title)
            label.setObjectName(OBJ_SECTION_TITLE)
            label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            self._layout.addWidget(label)

        # Esticador permanente no fim. Sem ele, o espaço que sobra num cartão
        # mais alto que seu conteúdo é distribuído entre os widgets que aceitam
        # crescer — e o primeiro deles costuma ser o título, que então aparece
        # como uma tarja alta e vazia no topo do cartão.
        self._layout.addStretch(1)

    def body(self) -> QVBoxLayout:
        return self._layout

    def add(self, widget: QWidget) -> None:
        """Acrescenta conteúdo **antes** do esticador final."""
        self._layout.insertWidget(self._layout.count() - 1, widget)

    def clear_content(self) -> None:
        """Remove o conteúdo, preservando título e esticador.

        Mora aqui, e não em quem chama, porque só o `Card` conhece sua própria
        estrutura interna. Uma limpeza feita de fora removia o esticador junto,
        e o `add()` seguinte inseria antes do título — que ia parar no rodapé
        do cartão.
        """
        first = 1 if self._has_title else 0
        while self._layout.count() - 1 > first:
            item = self._layout.takeAt(first)
            widget = item.widget() if item is not None else None
            if widget is not None:
                # `setParent(None)` **antes** do `deleteLater()`, e é a linha
                # que conserta um defeito visível: `takeAt` tira o widget do
                # layout mas não do cartão, e `deleteLater` só agenda a
                # destruição. Entre uma coisa e outra o rótulo continua filho e
                # continua pintando na última geometria que teve — de modo que
                # cada reconstrução da tela empilhava mais uma camada de texto
                # por cima da anterior. Com oito voltas na Análise de stint o
                # cartão "A trabalhar" virava um borrão ilegível.
                widget.setParent(None)
                widget.deleteLater()


class MetricCard(QFrame):
    """Um número grande com rótulo e unidade.

    O valor usa fonte monoespaçada (via `objectName`) porque a largura dos
    dígitos precisa ser constante: sem isso o cartão treme a 60 Hz enquanto a
    velocidade oscila entre 99 e 100.
    """

    def __init__(self, title: str, unit: str = "") -> None:
        super().__init__()
        self.setObjectName(OBJ_CARD)
        self._default_color: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.LG.px, Space.MD.px, Space.LG.px, Space.MD.px)
        layout.setSpacing(2)

        self._title = QLabel(title)
        self._title.setObjectName(OBJ_CARD_TITLE)

        value_row = QHBoxLayout()
        value_row.setSpacing(Space.XS.px)
        value_row.setContentsMargins(0, 0, 0, 0)

        self._value = QLabel(PLACEHOLDER)
        self._value.setObjectName(OBJ_CARD_VALUE)
        value_row.addWidget(self._value)

        if unit:
            self._unit: QLabel | None = QLabel(unit)
            self._unit.setObjectName(OBJ_CARD_UNIT)
            self._unit.setAlignment(
                Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft
            )
            value_row.addWidget(self._unit)
        else:
            self._unit = None
        value_row.addStretch(1)

        layout.addWidget(self._title)
        layout.addLayout(value_row)

    def set_value(self, text: str, color: str | None = None) -> None:
        self._value.setText(text)
        if color is not None:
            self._value.setStyleSheet(f"color: {color}; background: transparent;")
        elif self._default_color is not None:
            self._value.setStyleSheet(
                f"color: {self._default_color}; background: transparent;"
            )

    def set_default_color(self, color: str) -> None:
        self._default_color = color

    def clear(self, muted_color: str) -> None:
        self.set_value(PLACEHOLDER, muted_color)


class StatRow(QWidget):
    """Linha rótulo → valor, para listas densas de números.

    Onde o `MetricCard` grita um número, este sussurra vários. Usado nos painéis
    de detalhe, em que quinze valores precisam caber sem virar uma parede.
    """

    def __init__(self, label: str, value: str = PLACEHOLDER) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(Space.MD.px)

        self._label = QLabel(label)
        self._label.setObjectName(OBJ_CARD_TITLE)
        self._value = QLabel(value)
        self._value.setObjectName(OBJ_MONO)
        self._value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        layout.addWidget(self._label)
        layout.addStretch(1)
        layout.addWidget(self._value)

    def set_value(self, text: str, color: str | None = None) -> None:
        self._value.setText(text)
        if color:
            self._value.setStyleSheet(f"color: {color}; background: transparent;")


class Badge(QLabel):
    """Rótulo em pílula — severidade de curva, estado de conexão, marcador."""

    def __init__(self, text: str = "", color: str | None = None) -> None:
        super().__init__(text)
        self.setObjectName(OBJ_BADGE)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        if color:
            self.set_color(color)

    def set_color(self, color: str) -> None:
        self.setStyleSheet(f"color: {color}; border-color: {color};")


class PageHeader(QWidget):
    """Título e subtítulo de uma página, com espaço para ações à direita."""

    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.LG.px)

        text = QVBoxLayout()
        text.setSpacing(0)
        # Alinha ao topo: sem isto, um cabeçalho com ações altas (dois
        # seletores empilhados, por exemplo) estica esta coluna e o subtítulo
        # descola do título, como se fossem dois elementos sem relação.
        text.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._title = QLabel(title)
        self._title.setObjectName(OBJ_PAGE_TITLE)
        text.addWidget(self._title)

        self._subtitle = QLabel(subtitle)
        self._subtitle.setObjectName(OBJ_PAGE_SUBTITLE)
        self._subtitle.setVisible(bool(subtitle))
        text.addWidget(self._subtitle)
        text.addStretch(1)

        layout.addLayout(text)
        layout.addStretch(1)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._actions = QHBoxLayout()
        self._actions.setSpacing(Space.SM.px)
        layout.addLayout(self._actions)

    def set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))

    def add_action(self, widget: QWidget) -> None:
        self._actions.addWidget(widget)


class MetricGrid(QWidget):
    """Grade de `MetricCard` que reflui conforme o número de colunas."""

    def __init__(self, columns: int = 6) -> None:
        super().__init__()
        self._columns = columns
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(Space.MD.px)
        self._cards: dict[str, MetricCard] = {}

    def add_card(self, key: str, card: MetricCard) -> MetricCard:
        index = len(self._cards)
        self._grid.addWidget(card, index // self._columns, index % self._columns)
        self._cards[key] = card
        return card

    def card(self, key: str) -> MetricCard:
        return self._cards[key]

    @property
    def cards(self) -> dict[str, MetricCard]:
        return self._cards

    def clear_values(self, theme: Theme) -> None:
        for card in self._cards.values():
            card.clear(theme.palette.text_muted)
