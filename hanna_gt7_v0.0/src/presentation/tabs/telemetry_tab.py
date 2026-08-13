"""
Aba "Telemetria" — detalhe de uma volta, canal a canal.

View pura: pede as séries ao `TelemetryViewModel`, que já resolve o eixo ativo
(distância ou tempo). Na versão antiga, cada gráfico repetia a decisão de eixo
e consultava o banco por conta própria.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...application.viewmodels.telemetry_viewmodel import (
    AXIS_DISTANCE,
    AXIS_TIME,
    LapDetail,
    TelemetryViewModel,
)
from ..widgets.widgets import format_ms
from ..widgets.widgets_chart import SyncedMiniChart, TrackMapWidget

COLOR_SPEED = "#4f7cff"
COLOR_RPM = "#e06cff"
COLOR_THROTTLE = "#3ddc84"
COLOR_BRAKE = "#ff5c5c"
COLOR_GEAR = "#f2c94c"
COLOR_FUEL = "#f2994a"
COLOR_G_LAT = "#ff9f4f"
COLOR_G_LONG = "#3ddc84"

# Uma cor por roda, reaproveitada nos três mosaicos (pneu, suspensão, slip) —
# assim a mesma roda tem sempre a mesma cor, em qualquer painel.
WHEEL_COLORS = {
    "fl": "#3ddc84",
    "fr": "#4f7cff",
    "rl": "#f2c94c",
    "rr": "#ff5c5c",
}
WHEEL_LABELS = {"fl": "Diant. Esq.", "fr": "Diant. Dir.", "rl": "Tras. Esq.", "rr": "Tras. Dir."}


class _SlipIndicator(QFrame):
    """Indicador central do mosaico de deriva: nível médio, com cor por faixa."""

    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setContentsMargins(10, 10, 10, 10)

        self._value = QLabel("--")
        self._value.setAlignment(Qt.AlignCenter)
        self._value.setStyleSheet("color: #ffffff; font-size: 30px; font-weight: 800;")

        self._label = QLabel("DERIVA MÉDIA")
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setObjectName("sectionHeader")

        self._verdict = QLabel("")
        self._verdict.setAlignment(Qt.AlignCenter)
        self._verdict.setStyleSheet("color: #c8cad0; font-size: 11px;")

        layout.addWidget(self._value)
        layout.addWidget(self._label)
        layout.addWidget(self._verdict)

    def set_pct(self, pct: float):
        self._value.setText(f"{pct:.1f}%")
        if pct < 30:
            color, verdict = "#3ddc84", "Aderência estável"
        elif pct < 60:
            color, verdict = "#f2c94c", "Deslizamento moderado"
        else:
            color, verdict = "#ff5c5c", "Perda de aderência"
        self._value.setStyleSheet(f"color: {color}; font-size: 30px; font-weight: 800;")
        self._verdict.setText(verdict)

    def clear(self):
        self._value.setText("--")
        self._verdict.setText("")


class TelemetryTab(QWidget):
    def __init__(self, view_model: TelemetryViewModel):
        super().__init__()
        self._vm = view_model
        self._charts: list[SyncedMiniChart] = []
        self._build_ui()

        self._vm.laps_available.connect(self._on_laps_available)
        self._vm.detail_ready.connect(self._render)
        self._vm.error.connect(self._on_error)

    # ---------- construção ----------

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        outer.addLayout(self._build_controls())

        self._message = QLabel("Selecione uma volta para ver a telemetria.")
        self._message.setAlignment(Qt.AlignCenter)
        self._message.setStyleSheet("color: #6b6f7a; font-size: 14px;")
        outer.addWidget(self._message)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self._root = QVBoxLayout(content)
        self._root.setContentsMargins(4, 4, 4, 4)
        self._root.setSpacing(8)

        self.chart_speed = self._add_chart("Velocidade (km/h)")
        self.chart_rpm = self._add_chart("RPM")
        self.chart_pedals = self._add_chart("Acelerador / Freio (%)")
        self.chart_gear = self._add_chart("Marcha")
        self.chart_g = self._add_chart("Força G (lateral / longitudinal)")
        self.chart_fuel = self._add_chart("Combustível")

        self._root.addWidget(self._section_header("TEMPERATURA DOS PNEUS"))
        self.tire_charts = self._add_wheel_mosaic("°C")

        self._root.addWidget(self._section_header("SUSPENSÃO"))
        self.susp_charts = self._add_wheel_mosaic("")

        self._root.addWidget(self._section_header("ÂNGULO DE DERIVA (SLIP ANGLE)"))
        self.slip_charts, self.slip_indicator = self._add_slip_mosaic()

        self._root.addWidget(self._section_header("TRAÇADO"))
        self.track_map = TrackMapWidget("Traçado da volta", height=260)
        self._root.addWidget(self.track_map)

        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)

        self._sector_panel = QLabel("")
        self._sector_panel.setObjectName("sectionHeader")
        outer.addWidget(self._sector_panel)

    def _build_controls(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(4, 4, 4, 0)
        row.setSpacing(10)

        lap_label = QLabel("Volta:")
        lap_label.setObjectName("sectionHeader")
        self._lap_combo = QComboBox()
        self._lap_combo.setMinimumWidth(240)

        self._plot_button = QPushButton("Exibir")
        self._plot_button.clicked.connect(self._on_plot_clicked)

        axis_label = QLabel("Eixo:")
        axis_label.setObjectName("sectionHeader")
        self._radio_distance = QRadioButton("Distância")
        self._radio_time = QRadioButton("Tempo")
        self._radio_distance.setChecked(True)

        self._axis_group = QButtonGroup(self)
        self._axis_group.addButton(self._radio_distance, 0)
        self._axis_group.addButton(self._radio_time, 1)
        self._axis_group.idClicked.connect(
            lambda i: self._vm.set_axis_mode(AXIS_TIME if i == 1 else AXIS_DISTANCE)
        )

        row.addWidget(lap_label)
        row.addWidget(self._lap_combo)
        row.addWidget(self._plot_button)
        row.addSpacing(20)
        row.addWidget(axis_label)
        row.addWidget(self._radio_distance)
        row.addWidget(self._radio_time)
        row.addStretch()
        return row

    def _section_header(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionHeader")
        return label

    def _add_chart(self, title: str, height: int = 130) -> SyncedMiniChart:
        chart = SyncedMiniChart(title, height=height)
        chart.hovered_at_distance.connect(self._on_hover)
        chart.hover_left.connect(self._on_hover_left)
        self._charts.append(chart)
        self._root.addWidget(chart)
        return chart

    def _add_wheel_mosaic(self, unit: str) -> dict[str, SyncedMiniChart]:
        """Grade 2×2 com um gráfico por roda.

        Quatro linhas no mesmo gráfico se sobrepõem quando os valores são
        próximos — que é o caso normal. Separadas, dá para ver a assimetria
        entre lados, que é o que interessa no acerto do carro.
        """
        frame = QFrame()
        grid = QGridLayout(frame)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)

        charts = {}
        for i, wheel in enumerate(("fl", "fr", "rl", "rr")):
            title = f"{WHEEL_LABELS[wheel]}" + (f" ({unit})" if unit else "")
            chart = SyncedMiniChart(title, height=110)
            chart.hovered_at_distance.connect(self._on_hover)
            chart.hover_left.connect(self._on_hover_left)
            self._charts.append(chart)
            charts[wheel] = chart
            grid.addWidget(chart, i // 2, i % 2)

        self._root.addWidget(frame)
        return charts

    def _add_slip_mosaic(self):
        """Mosaico de deriva: 2×2 por roda + indicador central."""
        frame = QFrame()
        grid = QGridLayout(frame)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)

        charts = {}
        positions = {"fl": (0, 0), "fr": (0, 2), "rl": (1, 0), "rr": (1, 2)}
        for wheel, (r, c) in positions.items():
            chart = SyncedMiniChart(f"{WHEEL_LABELS[wheel]} (graus)", height=110)
            chart.hovered_at_distance.connect(self._on_hover)
            chart.hover_left.connect(self._on_hover_left)
            self._charts.append(chart)
            charts[wheel] = chart
            grid.addWidget(chart, r, c)

        indicator = _SlipIndicator()
        grid.addWidget(indicator, 0, 1, 2, 1)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 3)

        self._root.addWidget(frame)
        return charts, indicator

    # ---------- reação ao ViewModel ----------

    def _on_laps_available(self, laps: list):
        self._lap_combo.clear()
        for lap in laps:
            self._lap_combo.addItem(
                f"Volta {lap.id} — {format_ms(lap.lap_time_ms)}", lap.id
            )
        self._plot_button.setEnabled(bool(laps))
        if not laps:
            self._message.setText("Nenhuma volta gravada nesta pista.")
            self._message.setVisible(True)

    def _on_plot_clicked(self):
        lap_id = self._lap_combo.currentData()
        if lap_id is not None:
            self._vm.load_lap(lap_id)

    def _on_error(self, message: str):
        self._message.setText(message)
        self._message.setVisible(True)

    def _render(self, detail: LapDetail):
        if not detail.is_valid:
            return
        self._message.setVisible(False)

        by_time = detail.axis_mode == AXIS_TIME
        # As linhas de setor marcam distância; no eixo temporal não têm posição
        # definida, então saem em vez de aparecerem no lugar errado.
        # O widget espera pares (posição, rótulo).
        sectors = (
            []
            if by_time
            else [(d, f"S{i + 1}") for i, d in enumerate(detail.sector_boundaries)]
        )

        self.chart_speed.set_series(
            [("Velocidade", COLOR_SPEED, self._vm.points_for("speed_kmh"))]
        )
        self.chart_rpm.set_series([("RPM", COLOR_RPM, self._vm.points_for("rpm"))])
        self.chart_pedals.set_series(
            [
                ("Acelerador", COLOR_THROTTLE, self._vm.points_for("throttle")),
                ("Freio", COLOR_BRAKE, self._vm.points_for("brake")),
            ],
            y_range=(0, 105),
        )
        self.chart_gear.set_series([("Marcha", COLOR_GEAR, self._vm.points_for("gear"))])
        self.chart_g.set_series(
            [
                ("G lateral", COLOR_G_LAT, self._vm.points_for("g_lateral")),
                ("G longitudinal", COLOR_G_LONG, self._vm.points_for("g_longitudinal")),
            ]
        )
        self.chart_fuel.set_series(
            [("Combustível", COLOR_FUEL, self._vm.points_for("fuel_level"))]
        )

        for wheel, chart in self.tire_charts.items():
            chart.set_series(
                [(WHEEL_LABELS[wheel], WHEEL_COLORS[wheel],
                  self._vm.points_for(f"tire_temp_{wheel}"))]
            )
        for wheel, chart in self.susp_charts.items():
            chart.set_series(
                [(WHEEL_LABELS[wheel], WHEEL_COLORS[wheel],
                  self._vm.points_for(f"suspension_{wheel}"))]
            )
        for wheel, chart in self.slip_charts.items():
            chart.set_series(
                [(WHEEL_LABELS[wheel], WHEEL_COLORS[wheel],
                  self._vm.slip_angle_points(f"tire_slip_{wheel}"))]
            )
        self.slip_indicator.set_pct(self._vm.average_slip_pct())

        for chart in self._charts:
            chart.set_sector_lines(sectors)

        self.track_map.clear()
        trail = detail.series.position_points()
        if trail:
            self.track_map.set_paths([("volta", COLOR_SPEED, trail)])

        self._render_sectors(detail)

    def _render_sectors(self, detail: LapDetail):
        lap = detail.lap
        parts = [f"Volta {lap.id}", f"Tempo: {format_ms(lap.lap_time_ms)}"]
        if lap.has_points:
            parts.append(f"Distância: {lap.distance_m:.0f} m")
            parts.append(f"Vel. média: {lap.avg_speed:.1f} km/h")
            parts.append(f"Vel. máxima: {lap.max_speed:.1f} km/h")
            fuel = lap.fuel_used
            if fuel is not None:
                parts.append(f"Combustível: {fuel:.2f}")
        self._sector_panel.setText("   |   ".join(parts))

    # ---------- cursor sincronizado ----------

    def _on_hover(self, x_value: float):
        for chart in self._charts:
            chart.show_crosshair(x_value)

    def _on_hover_left(self):
        for chart in self._charts:
            chart.hide_crosshair()
