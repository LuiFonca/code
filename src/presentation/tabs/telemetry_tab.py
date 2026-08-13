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
    SLIP_MODERATE_PCT,
    SLIP_STABLE_PCT,
    LapDetail,
    TelemetryViewModel,
    slip_level_label,
)
from ..widgets.widgets import format_ms
from ..widgets.widgets_chart import SyncedMiniChart, TrackMapWidget
from .chart_tab_base import ChartTabBase

COLOR_SPEED = "#4f7cff"
COLOR_RPM = "#e06cff"
COLOR_THROTTLE = "#3ddc84"
COLOR_BRAKE = "#ff5c5c"
COLOR_GEAR = "#f2c94c"
COLOR_FUEL = "#f2994a"
COLOR_G_LAT = "#ff9f4f"
COLOR_G_LONG = "#3ddc84"
COLOR_SLIP_ANGLE = "#00d5c8"

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
    """Indicador central do mosaico: índice médio de deslizamento, com faixa."""

    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setContentsMargins(10, 10, 10, 10)

        self._value = QLabel("--")
        self._value.setAlignment(Qt.AlignCenter)
        self._value.setStyleSheet("color: #ffffff; font-size: 30px; font-weight: 800;")

        self._label = QLabel("ÍNDICE MÉDIO")
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setObjectName("sectionHeader")

        self._verdict = QLabel("")
        self._verdict.setAlignment(Qt.AlignCenter)
        self._verdict.setStyleSheet("color: #c8cad0; font-size: 11px;")
        self._verdict.setWordWrap(True)

        layout.addWidget(self._value)
        layout.addWidget(self._label)
        layout.addWidget(self._verdict)

    def set_pct(self, pct: float):
        self._value.setText(f"{pct:.1f}%")
        if pct < SLIP_STABLE_PCT:
            color = "#3ddc84"
        elif pct < SLIP_MODERATE_PCT:
            color = "#f2c94c"
        else:
            color = "#ff5c5c"
        self._value.setStyleSheet(f"color: {color}; font-size: 30px; font-weight: 800;")
        self._verdict.setText(slip_level_label(pct))

    def clear(self):
        self._value.setText("--")
        self._verdict.setText("")


class TelemetryTab(ChartTabBase):
    def __init__(self, view_model: TelemetryViewModel):
        super().__init__()
        self._vm = view_model
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

        self.chart_speed = self.add_chart("Velocidade (km/h)")
        self.chart_rpm = self.add_chart("RPM")
        self.chart_pedals = self.add_chart("Acelerador / Freio (%)")
        self.chart_gear = self.add_chart("Marcha")
        self.chart_g = self.add_chart("Força G (lateral / longitudinal)")
        self.chart_fuel = self.add_chart("Combustível")

        self._root.addWidget(self.section_header("TEMPERATURA DOS PNEUS"))
        frame_pneus, self.tire_charts = self.add_wheel_mosaic(
            WHEEL_LABELS, WHEEL_COLORS, unit="°C"
        )
        self._root.addWidget(frame_pneus)

        self._root.addWidget(self.section_header("SUSPENSÃO"))
        frame_susp, self.susp_charts = self.add_wheel_mosaic(WHEEL_LABELS, WHEEL_COLORS)
        self._root.addWidget(frame_susp)

        self._root.addWidget(
            self.section_header("ÍNDICE DE DESLIZAMENTO DOS PNEUS  (0–100 %)")
        )
        slip_note = QLabel(
            "Razão entre a velocidade da roda e a do solo, normalizada — quanto "
            "cada pneu escorrega. Mede coisa diferente do ângulo de deriva "
            "abaixo, que é a atitude do carro inteiro."
        )
        slip_note.setWordWrap(True)
        slip_note.setStyleSheet("color: #8b93a7; font-size: 11px;")
        self._root.addWidget(slip_note)
        self.slip_indicator = _SlipIndicator()
        frame_slip, self.slip_charts = self.add_wheel_mosaic(
            WHEEL_LABELS, WHEEL_COLORS, unit="%", central_widget=self.slip_indicator
        )
        self._root.addWidget(frame_slip)

        self._build_slip_angle_section()

        self._root.addWidget(self.section_header("TRAÇADO"))
        self.track_map = TrackMapWidget("Traçado da volta", height=260)
        self._root.addWidget(self.track_map)

        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)

        self._sector_panel = QLabel("")
        self._sector_panel.setObjectName("sectionHeader")
        outer.addWidget(self._sector_panel)

    def _build_slip_angle_section(self):
        """Ângulo de deriva em graus — ao lado do índice, não no lugar dele.

        São medidas independentes e ambas úteis: dá para ter índice alto com
        ângulo baixo (rodas patinando em aceleração, carro reto) e ângulo alto
        com índice moderado (traseira saindo de forma controlada). Substituir
        uma pela outra perderia informação.

        A seção inteira some quando a volta não tem o dado — voltas gravadas
        antes do schema v8. Um gráfico vazio com título faria parecer que a
        medida existe e deu zero, que é uma afirmação diferente de "não medido".
        """
        self._slip_angle_header = self.section_header("ÂNGULO DE DERIVA  (graus)")
        self._root.addWidget(self._slip_angle_header)

        self._slip_angle_note = QLabel(
            "Ângulo entre para onde o carro aponta e para onde ele de fato se "
            "move, obtido do quaternion de orientação. Positivo e negativo "
            "distinguem o lado; perto de zero, o carro está alinhado com a "
            "trajetória."
        )
        self._slip_angle_note.setWordWrap(True)
        self._slip_angle_note.setStyleSheet("color: #8b93a7; font-size: 11px;")
        self._root.addWidget(self._slip_angle_note)

        self.chart_slip_angle = self.add_chart("Ângulo de deriva (°)")
        self._root.addWidget(self.chart_slip_angle)

        self._slip_angle_summary = QLabel("")
        self._slip_angle_summary.setStyleSheet("color: #c8cad0; font-size: 12px;")
        self._root.addWidget(self._slip_angle_summary)

        self._slip_angle_absent = QLabel(
            "Esta volta foi gravada antes da medida de ângulo existir — só o "
            "índice de deslizamento acima está disponível."
        )
        self._slip_angle_absent.setWordWrap(True)
        self._slip_angle_absent.setStyleSheet("color: #6b6f7a; font-size: 11px;")
        self._root.addWidget(self._slip_angle_absent)

    def _render_slip_angle(self):
        """Preenche (ou esconde) a seção de ângulo conforme o dado disponível."""
        tem_dado = self._vm.has_slip_angle()

        self._slip_angle_header.setVisible(tem_dado)
        self._slip_angle_note.setVisible(tem_dado)
        self.chart_slip_angle.setVisible(tem_dado)
        self._slip_angle_summary.setVisible(tem_dado)
        self._slip_angle_absent.setVisible(not tem_dado)

        if not tem_dado:
            self.chart_slip_angle.set_series([])
            self._slip_angle_summary.setText("")
            return

        self.chart_slip_angle.set_series(
            [("Deriva", COLOR_SLIP_ANGLE, self._vm.slip_angle_points())]
        )
        pico = self._vm.peak_slip_angle_deg()
        media = self._vm.average_slip_angle_deg()
        if pico is None or media is None:
            self._slip_angle_summary.setText("")
            return
        lado = "direita" if pico > 0 else "esquerda"
        self._slip_angle_summary.setText(
            f"Pico: {pico:+.1f}° (para a {lado})    |    "
            f"Média em módulo: {media:.1f}°"
        )

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
        sectors = [] if by_time else detail.sector_boundaries

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
                  self._vm.slip_points(f"tire_slip_{wheel}"))],
                y_range=(0, 100),
            )
        self.slip_indicator.set_pct(self._vm.average_slip_pct())
        self._render_slip_angle()

        self.apply_sector_lines(sectors)

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
            # Sempre com unidade explícita: percentual do tanque quando a
            # capacidade é conhecida, valor bruto rotulado quando não é.
            fuel_pct = self._vm.fuel_used_pct()
            if fuel_pct is not None:
                parts.append(f"Combustível: {fuel_pct:.1f} % do tanque")
            elif lap.fuel_used is not None:
                parts.append(f"Combustível: {lap.fuel_used:.2f} (unidade do jogo)")
        if not lap.is_complete:
            parts.append("⚠ volta parcial — não conta como recorde")
        self._sector_panel.setText("   |   ".join(parts))

