"""
O rádio: a nota do engenheiro durante a pilotagem.

Diferente do `AdviceCard`, e a diferença é toda sobre o momento de leitura. O
debrief é lido com o carro parado, com tempo para conferir a tabela ao lado. Isto
aqui é lido **de relance**, a 200 km/h, entre uma curva e outra.

Do que decorre tudo:

- **Uma linha, fonte grande.** Sem detalhe, sem lista de ações. O que não couber
  numa olhada não será lido, e o `Advice` do nível 1 já vem com uma frase só.
- **A nota permanece.** Some depois de um tempo, mas não instantaneamente: o
  piloto pode estar no meio de uma freada quando ela chega.
- **A hora aparece.** Sem ela, uma nota antiga na tela é indistinguível de uma
  nova, e o piloto tenta aplicar uma correção sobre a curva errada.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..design.theme import OBJ_BADGE, OBJ_SECTION_TITLE
from ..design.tokens import Radius, Space, Theme

# Quanto tempo a nota fica na tela. Generoso de propósito: numa volta rápida o
# piloto pode levar dez segundos até ter os olhos livres para lê-la.
HOLD_MS = 25_000

IDLE_TEXT = "Rádio em silêncio."
UNAVAILABLE_TEXT = "Rádio indisponível — o engenheiro não está instalado."
THINKING_TEXT = "…"


class RadioCard(QWidget):
    """Uma linha grande com a última nota do engenheiro."""

    def __init__(self, theme: Theme) -> None:
        super().__init__()
        self._theme = theme
        palette = theme.palette

        self.setObjectName("radioCard")
        # Sem isto o Qt **não pinta** o fundo declarado na folha de estilo: um
        # QWidget nu ignora `background-color` a menos que se peça fundo
        # estilizado explicitamente. É o oposto exato do ajuste feito no cartão
        # do engenheiro, onde widgets de layout precisavam parar de pintar — e
        # confundir os dois deixa ou uma tarja escura sobrando ou, como aqui, um
        # cartão invisível.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"#radioCard {{"
            f" background-color: {palette.surface_raised};"
            f" border: 1px solid {palette.border};"
            f" border-radius: {Radius.MD.px}px;"
            f"}}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            Space.LG.px, Space.MD.px, Space.LG.px, Space.MD.px
        )
        layout.setSpacing(Space.MD.px)

        self._label = QLabel("RÁDIO")
        self._label.setObjectName(OBJ_SECTION_TITLE)
        self._label.setStyleSheet("background: transparent;")

        self._text = QLabel(IDLE_TEXT)
        self._text.setWordWrap(True)
        self._text.setStyleSheet(
            f"background: transparent; color: {palette.text_muted}; font-size: 16px;"
        )

        self._stamp = QLabel("")
        self._stamp.setObjectName(OBJ_BADGE)
        self._stamp.setVisible(False)

        layout.addWidget(self._label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._text, 1, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._stamp, 0, Qt.AlignmentFlag.AlignVCenter)

        # `singleShot` reiniciável: nota nova estende o prazo em vez de herdar o
        # que sobrou do anterior, que faria a segunda sumir cedo demais.
        # Estado lógico próprio, e não `self._stamp.isVisible()`. `isVisible`
        # é falso enquanto o widget não estiver **exibido** — inclusive quando o
        # piloto está noutra página —, então usá-lo como estado faria a nota
        # "sumir" da lógica só por trocar de aba, e o próximo pedido apagaria
        # uma nota que continua na tela ao voltar.
        self._has_note = False

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.show_idle)

    # ------------------------------------------------------------------

    @property
    def has_note(self) -> bool:
        """Se há uma nota de verdade na tela — não ocioso nem pensando."""
        return self._has_note

    def show_idle(self) -> None:
        self._timer.stop()
        self._has_note = False
        self._set(IDLE_TEXT, self._theme.palette.text_muted)
        self._stamp.setVisible(False)

    def show_unavailable(self) -> None:
        self._timer.stop()
        self._has_note = False
        self._set(UNAVAILABLE_TEXT, self._theme.palette.text_muted)
        self._stamp.setVisible(False)

    def show_thinking(self) -> None:
        """Indica que uma nota está sendo preparada — **sem** apagar a atual.

        A guarda não é detalhe. Um evento dispara o pedido, dez outros chegam
        atrás e viram um pedido pendente; quando o pendente roda, a cadência do
        `Budget` costuma recusá-lo. Sem a guarda, esse pedido natimorto trocava
        a nota recém-entregue por "…" e depois por silêncio — o piloto via o
        conselho aparecer e sumir sozinho, sem ter feito nada.

        Uma nota na tela vale mais que um indicador de progresso.
        """
        if self._has_note:
            return
        self._timer.stop()
        self._set(THINKING_TEXT, self._theme.palette.text_secondary)
        self._stamp.setVisible(False)

    def show_advice(self, advice: object) -> None:
        """Mostra a fala do nível 1. Ignora detalhe e ações de propósito."""
        speech = getattr(advice, "speech", None)
        text = speech() if callable(speech) else str(getattr(advice, "headline", ""))
        if not text.strip():
            self.show_idle()
            return

        palette = self._theme.palette
        self._set(text, palette.text_primary)

        is_local = bool(getattr(advice, "is_local", False))
        self._stamp.setText("local" if is_local else "IA")
        self._stamp.setStyleSheet(
            f"color: {palette.text_muted if is_local else palette.accent};"
        )
        self._stamp.setVisible(True)
        self._has_note = True
        self._timer.start(HOLD_MS)

    # ------------------------------------------------------------------

    def _set(self, text: str, color: str) -> None:
        self._text.setText(text)
        self._text.setStyleSheet(
            f"background: transparent; color: {color}; font-size: 16px;"
        )
