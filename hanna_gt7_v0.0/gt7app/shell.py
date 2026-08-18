"""
Casca da aplicação — navegação lateral, pilha de páginas e paleta de comandos.

Substitui a `LiveWindow` da Fase 3 e as abas da aplicação anterior. A troca de
abas por páginas não é estética: com abas, as quatro telas ficam vivas o tempo
todo e as quatro se atualizam enquanto se olha para uma. Aqui só a página
visível trabalha — `on_enter` / `on_leave` no contrato de `Page`.

Ordem de desmonte
-----------------
`closeEvent` desmonta na ordem inversa da montagem: páginas, ViewModel,
adaptador Qt, núcleo. Sem isso o barramento seguiria emitindo para objetos Qt já
destruídos, que é acesso a ponteiro morto — não uma exceção Python que se veja
no log.
"""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QCloseEvent, QKeySequence, QResizeEvent, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gt7core.observability.logging import get_logger
from gt7core.session.manager import LapSaved

from .adapters.qt_bus import QtEventBusAdapter
from .application import CoreApplication
from .commands import CommandRegistry
from .design.theme import (
    OBJ_NAV_BUTTON,
    OBJ_SECTION_TITLE,
    OBJ_SIDEBAR,
    OBJ_STATUS_BAR,
    build_stylesheet,
)
from .design.tokens import Space, Theme, get_theme
from .pages.analysis import AnalysisPage
from .pages.base import Page
from .pages.compare import ComparePage
from .pages.driver import DriverPage
from .pages.history import HistoryPage
from .pages.live import LivePage
from .pages.settings import SettingsPage
from .viewmodels.live import LiveViewModel
from .widgets.palette import CommandPalette

_log = get_logger(__name__)

SIDEBAR_WIDTH = 200


class AppShell(QMainWindow):
    """Janela principal: barra lateral + páginas + paleta de comandos."""

    def __init__(
        self,
        core: CoreApplication,
        view_model: LiveViewModel,
        adapter: QtEventBusAdapter,
        *,
        theme: Theme | None = None,
    ) -> None:
        super().__init__()
        self._core = core
        self._vm = view_model
        self._adapter = adapter
        self._theme = theme or get_theme(core.settings.ui.theme)
        self._commands = CommandRegistry()
        self._pages: list[Page] = []

        self.setWindowTitle("HANNA GT7")
        self.resize(1360, 860)
        self.setStyleSheet(build_stylesheet(self._theme))

        self._build()
        self._register_commands()
        self._install_shortcuts()

        # Gravar uma volta invalida as páginas que leem do banco. Elas se
        # recarregam agora se estiverem visíveis, ou na próxima entrada.
        self._vm.lap_saved.connect(self._on_lap_saved)

        self._activate(0)

    # ---------- construção ----------

    def _build(self) -> None:
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_sidebar())

        self._stack = QStackedWidget()
        for page in (
            LivePage(self._core, self._theme, self._vm),
            AnalysisPage(self._core, self._theme),
            ComparePage(self._core, self._theme),
            HistoryPage(self._core, self._theme),
            DriverPage(self._core, self._theme),
            SettingsPage(self._core, self._theme),
        ):
            self._add_page(page)
        layout.addWidget(self._stack, stretch=1)

        self.setCentralWidget(root)

        self._palette = CommandPalette(self, self._commands)

    def _add_page(self, page: Page) -> None:
        # Cada página rola por conta própria: as de análise têm mais conteúdo
        # que a altura da janela, e uma rolagem global moveria a barra lateral
        # junto.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._stack.addWidget(scroll)
        self._pages.append(page)

        button = QPushButton(page.nav_title)
        button.setObjectName(OBJ_NAV_BUTTON)
        button.setCheckable(True)
        index = len(self._pages) - 1
        button.clicked.connect(partial(self._activate, index))
        self._nav_group.addButton(button, index)
        self._nav_layout.addWidget(button)

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName(OBJ_SIDEBAR)
        sidebar.setFixedWidth(SIDEBAR_WIDTH)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(Space.MD.px, Space.XL.px, Space.MD.px, Space.LG.px)
        layout.setSpacing(Space.XS.px)

        brand = QLabel("HANNA GT7")
        brand.setObjectName(OBJ_SECTION_TITLE)
        layout.addWidget(brand)
        layout.addSpacing(Space.MD.px)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_layout = QVBoxLayout()
        self._nav_layout.setSpacing(Space.XS.px)
        layout.addLayout(self._nav_layout)
        layout.addStretch(1)

        self._hint = QLabel("⌘K  comandos")
        self._hint.setObjectName(OBJ_STATUS_BAR)
        layout.addWidget(self._hint)
        return sidebar

    # ---------- comandos e atalhos ----------

    def _register_commands(self) -> None:
        for index, page in enumerate(self._pages):
            self._commands.add(
                f"go.{page.page_id}",
                f"Ir para {page.nav_title}",
                partial(self._activate, index),
                category="Navegação",
                shortcut=f"Ctrl+{index + 1}",
                keywords=(page.page_id, page.title.lower()),
            )

        self._commands.add(
            "capture.start",
            "Conectar e começar a capturar",
            self._core.start,
            category="Captura",
            keywords=("iniciar", "start", "conectar"),
        )
        self._commands.add(
            "capture.stop",
            "Parar captura",
            self._core.stop,
            category="Captura",
            keywords=("parar", "stop", "desconectar"),
        )
        service = getattr(self._core, "engineer_service", None)
        if service is not None and service.is_available:
            self._commands.add(
                "engineer.debrief",
                "Pedir debrief ao engenheiro",
                self._ask_debrief,
                keywords=("ia", "conselho", "análise", "engenheiro"),
            )

        self._commands.add(
            "view.refresh",
            "Recarregar a página atual",
            self._refresh_current,
            category="Visualização",
            shortcut="F5",
            keywords=("atualizar", "reload"),
        )

    def _install_shortcuts(self) -> None:
        # ⌘K no macOS, Ctrl+K no resto: `QKeySequence.StandardKey` não cobre
        # "abrir paleta", então os dois são registrados explicitamente.
        for sequence in ("Ctrl+K", "Meta+K"):
            QShortcut(QKeySequence(sequence), self, self._palette.toggle)

        QShortcut(QKeySequence("F5"), self, self._refresh_current)
        for index in range(len(self._pages)):
            QShortcut(
                QKeySequence(f"Ctrl+{index + 1}"),
                self,
                partial(self._activate, index),
            )

    # ---------- navegação ----------

    def _activate(self, index: int) -> None:
        if not (0 <= index < len(self._pages)):
            return

        current = self._stack.currentIndex()
        if current != index and 0 <= current < len(self._pages):
            self._pages[current].on_leave()

        self._stack.setCurrentIndex(index)
        button = self._nav_group.button(index)
        if button is not None:
            button.setChecked(True)
        self._pages[index].on_enter()

    def _ask_debrief(self) -> None:
        """Leva à comparação e recalcula — é lá que o debrief é montado.

        O comando não chama o engenheiro direto de propósito: o debrief precisa
        de uma volta e de uma referência escolhidas, e quem sabe quais são é a
        página. Pedir daqui, sem contexto, produziria um conselho sobre a volta
        errada — ou sobre nenhuma.
        """
        for index, page in enumerate(self._pages):
            if page.page_id == "compare":
                self._activate(index)
                page.refresh()
                return

    def on_track_candidates(self, names: list[str]) -> None:
        """A detecção automática avisa a página ao vivo, que mostra o nome.

        Passa pela janela porque quem detecta é o `build_gui` e quem exibe é uma
        página — e nenhum dos dois deveria procurar o outro na hierarquia de
        widgets.
        """
        for page in self._pages:
            handler = getattr(page, "_on_track_candidates", None)
            if handler is not None:
                handler(names)

    def _refresh_current(self) -> None:
        index = self._stack.currentIndex()
        if 0 <= index < len(self._pages):
            self._pages[index].refresh()

    def _on_lap_saved(self, _event: LapSaved) -> None:
        for page in self._pages:
            if page.page_id != "live":
                page.invalidate()

    # ---------- eventos da janela ----------

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802  (API do Qt)
        super().resizeEvent(event)
        if self._palette.isVisible():
            self._palette._reposition()  # noqa: SLF001  (reposiciona a própria filha)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802  (API do Qt)
        # Perder o foco da janela fecha a paleta — é o que se espera de uma
        # sobreposição modal leve.
        if (
            event.type() == QEvent.Type.WindowDeactivate
            and self._palette.isVisible()
        ):
            self._palette.close_palette()
        super().changeEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802  (API do Qt)
        """Desmonta na ordem inversa da montagem.

        O engenheiro sai **primeiro**: uma inferência em voo segura uma thread
        que toca objetos Qt na volta, e fechar a janela antes de ela terminar é
        o modo de falha que o adaptador de barramento existe para evitar — só
        que no sentido contrário.
        """
        service = getattr(self._core, "engineer_service", None)
        if service is not None:
            service.shutdown()
        for page in self._pages:
            page.close_page()
        self._vm.close()
        self._adapter.close()
        self._core.close()
        _log.info("janela encerrada")
        super().closeEvent(event)


def main() -> int:
    """Entrada da interface: `python3 -m gt7app`."""
    import sys

    from PySide6.QtWidgets import QApplication

    from .application import build_core, build_gui

    app = QApplication(sys.argv)
    app.setApplicationName("HANNA GT7")
    app.setStyle("Fusion")

    core = build_core()
    window = build_gui(core)
    window.show()

    return int(app.exec())
