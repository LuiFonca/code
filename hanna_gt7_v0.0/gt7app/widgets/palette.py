"""
Paleta de comandos — a sobreposição que aparece com ⌘K / Ctrl+K.

Só apresentação: a busca mora em `gt7app/commands.py`, que é Python puro e
testável. Este arquivo cuida de foco, teclado e de fechar na hora certa.

Detalhe que costuma faltar em paletas caseiras: a lista é navegável **sem tirar
a mão do teclado** e sem o foco sair do campo de texto. Setas e Enter são
interceptados no `eventFilter` do campo e redirecionados para a lista; se
fossem tratados pela lista, digitar exigiria clicar de volta no campo.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..commands import Command, CommandRegistry
from ..design.theme import OBJ_PALETTE, OBJ_PALETTE_INPUT, OBJ_PALETTE_LIST
from ..design.tokens import Space

PALETTE_WIDTH = 560
PALETTE_MAX_HEIGHT = 420


class CommandPalette(QFrame):
    """Sobreposição de busca de comandos.

    É filha da janela (não uma janela própria) para que apareça centralizada
    sobre o conteúdo e siga o redimensionamento sem gerenciamento extra.
    """

    def __init__(self, parent: QWidget, registry: CommandRegistry) -> None:
        super().__init__(parent)
        self.setObjectName(OBJ_PALETTE)
        self._registry = registry
        self._results: list[Command] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, Space.SM.px)
        layout.setSpacing(0)

        self._input = QLineEdit()
        self._input.setObjectName(OBJ_PALETTE_INPUT)
        self._input.setPlaceholderText("Buscar comando…")
        self._input.textChanged.connect(self._refresh)
        self._input.installEventFilter(self)

        self._list = QListWidget()
        self._list.setObjectName(OBJ_PALETTE_LIST)
        self._list.itemActivated.connect(self._run_item)
        self._list.itemClicked.connect(self._run_item)

        layout.addWidget(self._input)
        layout.addWidget(self._list)

        self.setVisible(False)
        self.setFixedWidth(PALETTE_WIDTH)

    # ---------- ciclo de vida ----------

    def open(self) -> None:
        self._input.clear()
        self._refresh("")
        self._reposition()
        self.setVisible(True)
        self.raise_()
        self._input.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def close_palette(self) -> None:
        self.setVisible(False)
        parent = self.parentWidget()
        if parent is not None:
            parent.setFocus()

    def toggle(self) -> None:
        if self.isVisible():
            self.close_palette()
        else:
            self.open()

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        height = min(PALETTE_MAX_HEIGHT, max(160, self._list.count() * 40 + 70))
        self.setFixedHeight(height)
        # Um pouco acima do centro: é onde o olho já está, e deixa o resultado
        # visível sem cobrir o conteúdo que motivou a busca.
        self.move(
            (parent.width() - self.width()) // 2,
            max(40, parent.height() // 5),
        )

    # ---------- busca ----------

    def _refresh(self, query: str) -> None:
        self._results = self._registry.search(query)
        self._list.clear()
        for command in self._results:
            label = command.title
            if command.shortcut:
                label = f"{label}\t{command.shortcut}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, command.id)
            self._list.addItem(item)

        if self._list.count():
            self._list.setCurrentRow(0)
        self._reposition()

    def _run_item(self, item: QListWidgetItem) -> None:
        command_id = str(item.data(Qt.ItemDataRole.UserRole))
        command = self._registry.get(command_id)
        self.close_palette()
        if command is not None:
            command.run()

    def _run_current(self) -> None:
        item = self._list.currentItem()
        if item is not None:
            self._run_item(item)

    # ---------- teclado ----------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """Setas e Enter no campo de texto pilotam a lista.

        Sem isto, navegar exigiria mover o foco para a lista — e então digitar
        exigiria movê-lo de volta, o que anula a paleta.
        """
        if watched is self._input and event.type() == QEvent.Type.KeyPress:
            assert isinstance(event, QKeyEvent)
            key = event.key()

            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                delta = 1 if key == Qt.Key.Key_Down else -1
                count = self._list.count()
                if count:
                    # Circular: descer no último volta ao primeiro.
                    self._list.setCurrentRow((self._list.currentRow() + delta) % count)
                return True

            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._run_current()
                return True

            if key == Qt.Key.Key_Escape:
                self.close_palette()
                return True

        return super().eventFilter(watched, event)
