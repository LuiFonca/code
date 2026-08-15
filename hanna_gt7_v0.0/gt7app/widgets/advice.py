"""
O cartão do engenheiro.

Mostra um `Advice` e, junto dele, **de onde ele veio**. Essa segunda parte não é
enfeite: numa máquina apertada rodando um modelo de 4B, boa parte dos conselhos
vai sair da análise da Fase 4, e o piloto merece saber se está lendo um texto
que um modelo escreveu ou a aritmética que os detectores mediram. Sem a marca,
os dois se parecem — e a confiança que se deposita em cada um deveria ser
diferente.

Também mostra o estado *pensando*. Um cartão que fica idêntico por doze segundos
depois de um clique é indistinguível de um cartão quebrado, e a reação natural é
clicar de novo — que é exatamente o que não ajuda quando há um modelo ocupando a
memória toda.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..design.theme import OBJ_BADGE, OBJ_CARD_UNIT
from ..design.tokens import Space, Theme
from .cards import Card

THINKING_TEXT = "O engenheiro está analisando…"
UNAVAILABLE_TEXT = (
    "O engenheiro não está instalado. A análise numérica continua disponível "
    "nas outras seções desta página."
)


class AdviceCard(Card):
    """Cartão com título, raciocínio e ações do engenheiro."""

    def __init__(self, theme: Theme, title: str = "Engenheiro de corrida") -> None:
        super().__init__(title)
        self._theme = theme
        self._headline = QLabel()
        self._headline.setWordWrap(True)
        self._headline.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        self._detail = QLabel()
        self._detail.setWordWrap(True)
        self._detail.setObjectName(OBJ_CARD_UNIT)
        self._detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        self._source = QLabel()
        self._source.setObjectName(OBJ_BADGE)
        self._source.setVisible(False)

        self._actions = QVBoxLayout()
        self._actions.setSpacing(Space.XS.px)
        self._actions.setContentsMargins(0, 0, 0, 0)

        header_row = QWidget()
        row = QHBoxLayout(header_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(Space.SM.px)
        row.addWidget(self._headline, 1)
        row.addWidget(self._source, 0, Qt.AlignmentFlag.AlignTop)

        holder = QWidget()
        holder.setLayout(self._actions)
        # Mesmo defeito que apareceu nos títulos de seção na Fase 5: um QWidget
        # nu dentro do cartão pinta o fundo da folha de estilo por cima da
        # superfície elevada, e o resultado é uma tarja escura atravessando o
        # cartão. Widget de layout não deve pintar nada.
        holder.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        holder.setStyleSheet("background: transparent;")
        header_row.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        header_row.setStyleSheet("background: transparent;")

        self.add(header_row)
        self.add(self._detail)
        self.add(holder)

        self.show_idle()

    # ------------------------------------------------------------------

    def show_idle(self, message: str = "Selecione uma volta para o debrief.") -> None:
        self._set(message, "", muted=True)

    def show_thinking(self) -> None:
        self._set(THINKING_TEXT, "", muted=True)

    def show_unavailable(self) -> None:
        self._set(UNAVAILABLE_TEXT, "", muted=True)

    def show_error(self, message: str) -> None:
        self._set(f"Falha ao consultar o engenheiro: {message}", "", muted=True)

    def show_advice(self, advice: object) -> None:
        """Preenche a partir de um `Advice` do `gt7ai`.

        O tipo é `object` porque este widget não importa `gt7ai`: a interface
        deve continuar montando com o plugin ausente, e um import no topo do
        módulo faria a página inteira falhar em vez de mostrar "não instalado".
        """
        palette = self._theme.palette
        headline = str(getattr(advice, "headline", "")).strip()
        detail = str(getattr(advice, "detail", "")).strip()

        self._set(headline or "Sem conselho para esta volta.", detail, muted=False)

        is_local = bool(getattr(advice, "is_local", False))
        model = str(getattr(advice, "model", ""))
        if is_local:
            self._source.setText("análise local")
            self._source.setStyleSheet(f"color: {palette.text_muted};")
        else:
            self._source.setText(model or "IA")
            self._source.setStyleSheet(f"color: {palette.accent};")
        self._source.setVisible(True)

        for action in getattr(advice, "actions", []) or []:
            describe = getattr(action, "describe", None)
            text = describe() if callable(describe) else str(action)
            label = QLabel(f"•  {text}")
            label.setWordWrap(True)
            self._actions.addWidget(label)

    # ------------------------------------------------------------------

    def _set(self, headline: str, detail: str, *, muted: bool) -> None:
        palette = self._theme.palette
        self._headline.setText(headline)
        self._headline.setStyleSheet(
            f"color: {palette.text_muted if muted else palette.text_primary};"
        )
        self._detail.setText(detail)
        self._detail.setVisible(bool(detail))
        self._source.setVisible(False)
        self._clear_actions()

    def _clear_actions(self) -> None:
        while self._actions.count():
            item = self._actions.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
