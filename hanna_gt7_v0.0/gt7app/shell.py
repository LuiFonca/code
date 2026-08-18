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

import contextlib
from functools import partial

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QResizeEvent, QShortcut
from PySide6.QtWidgets import (
    QApplication,
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
from .power import KeepAwake
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

        self._keep_awake = KeepAwake()
        self._wire_keep_awake()

        self._activate(0)
        self._autoconnect()

    def _autoconnect(self) -> None:
        """Tenta a conexão com o que já está configurado, ao abrir.

        Adiada por um `singleShot` de zero: `_on_start` mexe na barra de status
        e no selo da página ao vivo, e chamá-la ainda dentro do construtor da
        janela é agir sobre widgets cuja geometria não foi calculada. O atraso
        zero põe a chamada na primeira volta do laço de eventos, com a janela já
        montada — é a diferença entre "conecta ao abrir" e "conecta ao abrir,
        menos naquela vez em que a tela ficou estranha".
        """
        live = self._pages[0]
        if not hasattr(live, "try_autoconnect"):
            return

        # `QTimer` **com dono**, e não `QTimer.singleShot`. O estático não tem
        # pai: disparado depois de a janela fechar, ele chama um método de uma
        # página cujo núcleo já foi desmontado, e o erro que aparece é
        # "banco já fechado" — num teste completamente diferente, porque o
        # disparo cai no próximo `processEvents()` de quem for. Parentado ao
        # `self`, o temporizador morre junto com a janela.
        self._autoconnect_timer = QTimer(self)
        self._autoconnect_timer.setSingleShot(True)
        self._autoconnect_timer.timeout.connect(live.try_autoconnect)
        self._autoconnect_timer.start(0)

    def _wire_keep_awake(self) -> None:
        """Segura a máquina acordada enquanto o programa está em primeiro plano.

        Pilotando, ninguém toca no teclado do computador — os controles estão no
        console. Para o sistema isso é ociosidade: o protetor de tela entra no
        meio da sessão, ou a máquina suspende e leva a captura UDP junto.

        Ligado ao **estado da aplicação**, e não ao da janela: `QWidget` sabe se
        está ativo, mas alternar entre a janela principal e um diálogo do próprio
        programa a desativaria por um instante — e o inibidor ficaria piscando
        junto. O estado de aplicação é a granularidade certa.
        """
        app = QApplication.instance()
        if app is None:  # pragma: no cover - só sem QApplication
            return
        app.applicationStateChanged.connect(self._on_app_state)  # type: ignore[attr-defined]
        self._on_app_state(app.applicationState())  # type: ignore[attr-defined]

    def _unwire_keep_awake(self) -> None:
        """Desfaz a ligação com o estado da aplicação ao fechar.

        Uma conexão para o `QApplication` **sobrevive à janela**: fechada, ela
        continua recebendo mudanças de estado e chamando métodos de um objeto
        cujo núcleo já foi desmontado. É a mesma classe de defeito que a ordem
        de desmonte deste `closeEvent` existe para evitar, por outra porta — e
        apareceu como uma cascata de `eventFilter` terminando em "banco já
        fechado" quando duas janelas viveram no mesmo processo.
        """
        app = QApplication.instance()
        if app is None:  # pragma: no cover - só sem QApplication
            return
        with contextlib.suppress(RuntimeError, TypeError):
            app.applicationStateChanged.disconnect(self._on_app_state)  # type: ignore[attr-defined]

    def _on_app_state(self, state: Qt.ApplicationState) -> None:
        if state == Qt.ApplicationState.ApplicationActive:
            self._keep_awake.acquire()
        else:
            self._keep_awake.release()

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
        # Para o temporizador antes de tudo: se ele disparar depois do
        # `close()` do núcleo, chama a página com o banco já fechado.
        self._autoconnect_timer.stop()
        self._unwire_keep_awake()
        self._keep_awake.release()
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
