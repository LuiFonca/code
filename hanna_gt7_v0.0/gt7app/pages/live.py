"""
Página ao vivo — o painel durante a pilotagem.

Portada da `window.py` da Fase 3, agora sobre o design system e com as tiras de
telemetria em tempo real que faltavam.

Uma decisão de ritmo: o painel recebe ~60 quadros por segundo, mas repinta a
15 Hz. O olho não distingue mais que isso num número, e repintar seis cartões
mais três gráficos a 60 Hz consome CPU que a captura precisa. O ViewModel já
entrega o último quadro num timer próprio; aqui só se cuida de não fazer
trabalho extra por quadro.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from gt7core.domain.models import Track
from gt7core.session.manager import LapSaved
from gt7core.telemetry.engine import TelemetryReceived

from ..application import CoreApplication
from ..design.theme import OBJ_GHOST_BUTTON, OBJ_STATUS_BAR
from ..design.tokens import Space, Theme
from ..viewmodels.live import LiveViewModel
from ..widgets.cards import Card, MetricCard, MetricGrid
from ..widgets.charts import DistanceChart, Series
from .base import Page

# Quantos metros de rastro manter nas tiras ao vivo. Uma volta inteira deixaria
# o gráfico ilegível; ~800 m é o horizonte que o piloto consegue relacionar com
# onde está.
TRAIL_WINDOW_M = 800.0

REPAINT_INTERVAL_MS = 66  # ~15 Hz


class LivePage(Page):
    page_id = "live"
    nav_title = "Ao vivo"
    title = "Ao vivo"
    subtitle = "Telemetria em tempo real"

    def __init__(
        self, core: CoreApplication, theme: Theme, view_model: LiveViewModel
    ) -> None:
        self._vm = view_model
        self._trail: list[tuple[float, float, float, float]] = []
        self._pending_repaint = False
        super().__init__(core, theme)
        self._connect()

    # ---------- construção ----------

    def build(self) -> None:
        self.header.add_action(self._build_toolbar())

        self._grid = MetricGrid(columns=6)
        for key, label, unit in (
            ("speed", "Velocidade", "km/h"),
            ("gear", "Marcha", ""),
            ("rpm", "RPM", ""),
            ("delta", "Delta", "s"),
            ("lap", "Volta", ""),
            ("distance", "Distância", "m"),
        ):
            self._grid.add_card(key, MetricCard(label, unit))
        self.content.addWidget(self._grid)

        traces = Card("Últimos 800 metros")
        self._speed_chart = DistanceChart(
            self.theme, "Velocidade", unit="km/h", height=140
        )
        self._pedals_chart = DistanceChart(
            self.theme, "Pedais", unit="%", height=120, y_range=(0.0, 105.0)
        )
        traces.add(self._speed_chart)
        traces.add(self._pedals_chart)
        self.content.addWidget(traces)
        self.content.addStretch(1)

        self._status = QLabel("Parado")
        self._status.setObjectName(OBJ_STATUS_BAR)
        self.content.addWidget(self._status)

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.SM.px)

        self._track_input = QComboBox()
        self._track_input.setEditable(True)
        self._track_input.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._reload_tracks()

        self._start_button = QPushButton("Conectar")
        self._stop_button = QPushButton("Parar")
        self._stop_button.setObjectName(OBJ_GHOST_BUTTON)
        self._stop_button.setEnabled(False)

        layout.addWidget(QLabel("Pista:"))
        layout.addWidget(self._track_input)
        layout.addWidget(self._start_button)
        layout.addWidget(self._stop_button)
        return bar

    def _connect(self) -> None:
        self._start_button.clicked.connect(self._on_start)
        self._stop_button.clicked.connect(self._on_stop)

        self._vm.frame_updated.connect(self._on_frame)
        self._vm.delta_updated.connect(self._on_delta)
        self._vm.connection_changed.connect(self._on_connection)
        self._vm.stale_entered.connect(self._on_stale)
        self._vm.lap_saved.connect(self._on_lap_saved)

        # Repintura desacoplada da chegada de quadros: acumula e desenha a 15 Hz.
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setInterval(REPAINT_INTERVAL_MS)
        self._repaint_timer.timeout.connect(self._repaint_traces)
        self._repaint_timer.start()

        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start()

    # ---------- ações ----------

    def _reload_tracks(self) -> None:
        """As pistas já usadas primeiro, depois o catálogo do jogo.

        Nesta ordem porque quem volta ao programa costuma voltar ao mesmo
        circuito. O catálogo entra embaixo para que digitar continue funcionando
        e, principalmente, para que o nome venha escrito igual toda vez — sem
        ele, "Suzuka" e "suzuka circuit" viram duas pistas distintas no banco e
        o histórico se parte em dois sem ninguém perceber.
        """
        current = self._track_input.currentText() if self._track_input.count() else ""
        self._track_input.clear()

        seen: set[str] = set()
        for track in self.core.tracks.get_all():
            self._track_input.addItem(track.name, track.id)
            seen.add(track.name.lower())

        catalog_names = sorted(
            (t.name for t in self.core.catalog.tracks.values() if t.name),
            key=str.lower,
        )
        for name in catalog_names:
            if name.lower() not in seen:
                self._track_input.addItem(name, None)

        if current:
            self._track_input.setCurrentText(current)

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
            track_id = self.core.tracks.get_or_create(name)
            self.core.session_manager.set_track(Track(id=track_id, name=name))
            self._reload_tracks()
            self._track_input.setCurrentText(name)

        self._trail.clear()
        self.core.start()
        self._start_button.setEnabled(False)
        self._stop_button.setEnabled(True)

    def _on_stop(self) -> None:
        self.core.stop()
        self._start_button.setEnabled(True)
        self._stop_button.setEnabled(False)
        self._status.setText("Parado")

    # ---------- reação ----------

    def _on_frame(self, event: TelemetryReceived) -> None:
        point = event.point
        cards = self._grid.cards
        cards["speed"].set_value(f"{point.speed_kmh:.0f}")
        cards["gear"].set_value(str(point.gear) if point.gear > 0 else "N")
        cards["rpm"].set_value(f"{point.rpm:.0f}")
        cards["distance"].set_value(f"{point.distance_m:.0f}")
        cards["lap"].set_value(str(event.frame.lap_count))

        self._trail.append(
            (point.distance_m, point.speed_kmh, point.throttle, point.brake)
        )
        # Descarta o que saiu da janela de rastro, mantendo a lista curta em vez
        # de crescer a volta inteira.
        cutoff = point.distance_m - TRAIL_WINDOW_M
        if self._trail[0][0] < cutoff:
            self._trail = [row for row in self._trail if row[0] >= cutoff]
        self._pending_repaint = True

    def _repaint_traces(self) -> None:
        if not self._pending_repaint:
            return
        self._pending_repaint = False

        palette = self.theme.palette
        self._speed_chart.set_series(
            [
                Series(
                    "vel",
                    palette.channel_speed,
                    [(d, v) for d, v, _, _ in self._trail],
                )
            ]
        )
        self._pedals_chart.set_series(
            [
                Series(
                    "acel",
                    palette.channel_throttle,
                    [(d, t) for d, _, t, _ in self._trail],
                ),
                Series(
                    "freio",
                    palette.channel_brake,
                    [(d, b) for d, _, _, b in self._trail],
                ),
            ]
        )

    def _on_delta(self, best: float | None, _previous: float | None) -> None:
        card = self._grid.cards["delta"]
        if best is None:
            card.set_value("—", self.theme.palette.text_muted)
            return
        card.set_value(f"{best:+.3f}", self.theme.palette.delta(best))

    def _on_connection(self, state: str, message: str) -> None:
        self._status.setText(message or f"Conexão: {state}")

    def _on_stale(self) -> None:
        """Distingue 'carro parado' de 'transmissão perdida'."""
        self._grid.clear_values(self.theme)
        self._status.setText("Sem telemetria")

    def _on_lap_saved(self, event: LapSaved) -> None:
        minutes, remainder = divmod(event.lap.lap_time_ms, 60_000)
        seconds, millis = divmod(remainder, 1000)
        marker = " ★ melhor" if event.is_best else ""
        self._status.setText(
            f"Volta gravada: {minutes}:{seconds:02d}.{millis:03d}{marker}"
        )
        self._trail.clear()

    def _refresh_stats(self) -> None:
        if not self.core.source.is_running:
            return
        stats = self.core.metrics.snapshot()
        if stats.packets_received:
            self._status.setText(stats.format_summary())

    def close_page(self) -> None:
        self._repaint_timer.stop()
        self._stats_timer.stop()
