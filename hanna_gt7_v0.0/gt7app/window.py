"""
Janela ao vivo sobre o núcleo novo.

Fatia vertical completa: fonte → motor → barramento → adaptador → ViewModel →
widgets. Serve de duas coisas ao mesmo tempo — é usável, e é o molde para
migrar as abas restantes (histórico, telemetria, comparação), que hoje ainda
rodam sobre a arquitetura antiga em `src/`.

A regra que a View segue: **nenhuma regra de negócio aqui**. Ela conecta sinais
do ViewModel a widgets e nada mais. Sem timer próprio, sem cálculo, sem SQL —
tudo isso mora abaixo.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gt7core.domain.models import Track
from gt7core.observability.logging import get_logger
from gt7core.session.manager import LapSaved
from gt7core.telemetry.engine import TelemetryReceived

from .adapters.qt_bus import QtEventBusAdapter
from .application import CoreApplication
from .viewmodels.live import LiveViewModel

_log = get_logger(__name__)

# Paleta herdada de `src/presentation/styles.py` — superfícies escuras
# estratificadas, sem preto absoluto, cores de texto sempre explícitas (depender
# do padrão do sistema produzia texto escuro sobre fundo escuro em temas claros).
BG_APP = "#12141a"
BG_CARD = "#1a1d25"
BORDER = "#2a2e3a"
TEXT_PRIMARY = "#e8e8ec"
TEXT_MUTED = "#6b6f7a"
ACCENT = "#4f7cff"
SUCCESS = "#3ddc84"
DANGER = "#ff5c5c"

STYLE = f"""
QMainWindow, QWidget {{ background-color: {BG_APP}; color: {TEXT_PRIMARY};
    font-family: 'Segoe UI', Arial, sans-serif; }}
QLabel#cardValue {{ font-size: 34px; font-weight: 600; color: {TEXT_PRIMARY}; }}
QLabel#cardTitle {{ font-size: 11px; color: {TEXT_MUTED};
    letter-spacing: 1px; text-transform: uppercase; }}
QWidget#card {{ background-color: {BG_CARD}; border: 1px solid {BORDER};
    border-radius: 10px; }}
QPushButton {{ background-color: {ACCENT}; border: none; border-radius: 6px;
    padding: 9px 20px; font-size: 14px; font-weight: 600; color: white; }}
QPushButton:disabled {{ background-color: {BORDER}; color: {TEXT_MUTED}; }}
QComboBox {{ background-color: {BG_CARD}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 6px 10px; color: {TEXT_PRIMARY}; min-width: 180px; }}
QComboBox QAbstractItemView {{ background-color: {BG_CARD}; color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT}; }}
"""


class MetricCard(QWidget):
    """Um valor grande com rótulo. O bloco de construção do painel."""

    def __init__(self, title: str, unit: str = "") -> None:
        super().__init__()
        self.setObjectName("card")
        self._unit = unit

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(2)

        self._title = QLabel(title)
        self._title.setObjectName("cardTitle")
        self._value = QLabel("—")
        self._value.setObjectName("cardValue")

        layout.addWidget(self._title)
        layout.addWidget(self._value)

    def set_value(self, text: str, color: str | None = None) -> None:
        self._value.setText(f"{text}{self._unit}")
        if color:
            self._value.setStyleSheet(f"color: {color};")


class LiveWindow(QMainWindow):
    """Painel ao vivo alimentado pelo núcleo."""

    def __init__(
        self,
        core: CoreApplication,
        view_model: LiveViewModel,
        adapter: QtEventBusAdapter,
    ) -> None:
        super().__init__()
        self._core = core
        self._vm = view_model
        self._adapter = adapter

        self.setWindowTitle("HANNA GT7 — Ao Vivo")
        self.resize(980, 560)
        self.setStyleSheet(STYLE)

        self._build_ui()
        self._connect()

    # ---------- construção ----------

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(16)

        layout.addLayout(self._build_toolbar())
        layout.addLayout(self._build_cards())
        layout.addStretch(1)

        self._status = QLabel("Parado")
        self._status.setObjectName("cardTitle")
        layout.addWidget(self._status)

        self.setCentralWidget(root)

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(10)

        self._track_input = QComboBox()
        self._track_input.setEditable(True)
        self._track_input.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._reload_tracks()

        self._start_button = QPushButton("Conectar")
        self._stop_button = QPushButton("Parar")
        self._stop_button.setEnabled(False)

        bar.addWidget(QLabel("Pista:"))
        bar.addWidget(self._track_input)
        bar.addStretch(1)
        bar.addWidget(self._start_button)
        bar.addWidget(self._stop_button)
        return bar

    def _build_cards(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setSpacing(12)

        self._cards = {
            "speed": MetricCard("Velocidade", " km/h"),
            "gear": MetricCard("Marcha"),
            "rpm": MetricCard("RPM"),
            "delta": MetricCard("Delta", " s"),
            "lap": MetricCard("Volta"),
            "distance": MetricCard("Distância", " m"),
        }
        for column, card in enumerate(self._cards.values()):
            grid.addWidget(card, 0, column)
        return grid

    def _connect(self) -> None:
        self._start_button.clicked.connect(self._on_start)
        self._stop_button.clicked.connect(self._on_stop)

        self._vm.frame_updated.connect(self._on_frame)
        self._vm.delta_updated.connect(self._on_delta)
        self._vm.connection_changed.connect(self._on_connection)
        self._vm.stale_entered.connect(self._on_stale)
        self._vm.lap_saved.connect(self._on_lap_saved)

        # Estatísticas de captura (§35) numa cadência própria: são diagnóstico,
        # não telemetria, e não precisam da taxa de repintura do painel.
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start()

    # ---------- ações ----------

    def _reload_tracks(self) -> None:
        self._track_input.clear()
        for track in self._core.tracks.get_all():
            self._track_input.addItem(track.name, track.id)

    def _resolve_track_name(self) -> str:
        """Lê o texto digitado, não `currentData()`.

        Num QComboBox editável com NoInsert, `setCurrentText()` não move o
        `currentIndex` — `currentData()` devolveria sempre o item 0. Esse foi um
        bug real: com o catálogo carregado, qualquer pista digitada era gravada
        como a primeira em ordem alfabética.
        """
        return self._track_input.currentText().strip()

    def _on_start(self) -> None:
        name = self._resolve_track_name()
        if name:
            track_id = self._core.tracks.get_or_create(name)
            self._core.session_manager.set_track(Track(id=track_id, name=name))
            self._reload_tracks()
            self._track_input.setCurrentText(name)

        self._core.start()
        self._start_button.setEnabled(False)
        self._stop_button.setEnabled(True)

    def _on_stop(self) -> None:
        self._core.stop()
        self._start_button.setEnabled(True)
        self._stop_button.setEnabled(False)
        self._status.setText("Parado")

    # ---------- reação aos sinais ----------

    def _on_frame(self, event: TelemetryReceived) -> None:
        point = event.point
        self._cards["speed"].set_value(f"{point.speed_kmh:.0f}")
        self._cards["gear"].set_value(str(point.gear) if point.gear > 0 else "N")
        self._cards["rpm"].set_value(f"{point.rpm:.0f}")
        self._cards["distance"].set_value(f"{point.distance_m:.0f}")
        self._cards["lap"].set_value(str(event.frame.lap_count))

    def _on_delta(self, best: float | None, _previous: float | None) -> None:
        if best is None:
            self._cards["delta"].set_value("—", TEXT_MUTED)
            return
        color = SUCCESS if best <= 0 else DANGER
        self._cards["delta"].set_value(f"{best:+.3f}", color)

    def _on_connection(self, state: str, message: str) -> None:
        self._status.setText(message or f"Conexão: {state}")

    def _on_stale(self) -> None:
        """Distingue 'carro parado' de 'transmissão perdida'."""
        for card in self._cards.values():
            card.set_value("—", TEXT_MUTED)
        self._status.setText("Sem telemetria")

    def _on_lap_saved(self, event: LapSaved) -> None:
        minutes, remainder = divmod(event.lap.lap_time_ms, 60_000)
        seconds, millis = divmod(remainder, 1000)
        marker = " ★ melhor" if event.is_best else ""
        self._status.setText(
            f"Volta gravada: {minutes}:{seconds:02d}.{millis:03d}{marker}"
        )

    def _refresh_stats(self) -> None:
        if not self._core.source.is_running:
            return
        stats = self._core.metrics.snapshot()
        if stats.packets_received:
            self._status.setText(stats.format_summary())

    # ---------- desmonte ----------

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802  (API do Qt)
        """Desmonta na ordem inversa da montagem.

        Sem isto, o barramento seguiria emitindo para objetos Qt já destruídos —
        que é acesso a ponteiro morto, não uma exceção Python.
        """
        self._stats_timer.stop()
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

    # Qt.ApplicationAttribute não é necessário: o closeEvent já desmonta.
    return int(app.exec())
