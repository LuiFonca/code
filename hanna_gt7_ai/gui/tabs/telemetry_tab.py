"""
Aba "Telemetria" — modo padrão: análise de uma única volta com todos os
canais de telemetria. Modo comparação: sobreposição de múltiplas voltas.
Arquitetura única que suporta ambos os modos sem duplicação de código.

Gráficos sincronizados pelo eixo de distância, com crosshair, tooltips,
zoom/pan via mouse, e painel de valores no hover.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QScrollArea, QGridLayout, QFrame, QCheckBox
)
from PySide6.QtCore import Qt

from analysis import lap_storage
from analysis.telemetry_series import (
    LapSeries, compute_delta_series, sector_boundaries_m,
    sector_times_from_series, best_combined_sectors,
)
from gui.widgets import format_ms
from gui.widgets_chart import SyncedMiniChart, TrackMapWidget

COLOR_A = "#4f7cff"
COLOR_B = "#ff9f4f"
COLOR_DELTA = "#f2c94c"
COLOR_MARKER_A = "#8fb0ff"
COLOR_MARKER_B = "#ffc48f"
COLOR_FL = "#3ddc84"
COLOR_FR = "#4f7cff"
COLOR_RL = "#f2c94c"
COLOR_RR = "#ff5c5c"
COLOR_G_LAT = "#ff9f4f"
COLOR_G_LONG = "#3ddc84"
COLOR_OIL = "#f2c94c"
COLOR_WATER = "#4f7cff"
NUM_SECTORS = 3


class TelemetryTab(QWidget):
    def __init__(self, track_id):
        super().__init__()
        self.track_id = track_id
        self.series_a: LapSeries | None = None
        self.series_b: LapSeries | None = None
        self.lap_id_a = None
        self.lap_id_b = None
        self._comparison_mode = False

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 16, 4, 4)
        root.setSpacing(10)

        root.addLayout(self._build_controls())
        self.summary_label = QLabel("Escolha uma volta para ver a telemetria completa.")
        self.summary_label.setStyleSheet("color: #8a8e99; font-size: 12px;")
        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)

        root.addWidget(self._build_readout_panel())
        root.addWidget(self._build_sector_panel())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        charts_container = QWidget()
        charts_layout = QVBoxLayout(charts_container)
        charts_layout.setSpacing(10)
        charts_layout.setContentsMargins(0, 0, 0, 0)

        self.track_map = TrackMapWidget("Traçado (posição X-Z)")
        charts_layout.addWidget(self.track_map)

        self.chart_speed = SyncedMiniChart("Velocidade (km/h)")
        self.chart_delta = SyncedMiniChart("Delta acumulado (s)")
        self.chart_gear = SyncedMiniChart("Marcha")
        self.chart_throttle = SyncedMiniChart("Acelerador (%)")
        self.chart_brake = SyncedMiniChart("Freio (%)")
        self.chart_tires = SyncedMiniChart("Temperatura média dos pneus (°C)")
        self.chart_fuel = SyncedMiniChart("Combustível (L)")
        self.chart_gforce = SyncedMiniChart("Força G (lateral / longitudinal)")
        self.chart_suspension = SyncedMiniChart("Suspensão (mm)")
        self.chart_tire_slip = SyncedMiniChart("Derrapagem de pneus")
        self.chart_turbo = SyncedMiniChart("Turbo (bar)")
        self.chart_temps = SyncedMiniChart("Temperatura motor (°C)")

        self.all_charts = [
            self.chart_speed, self.chart_delta, self.chart_gear,
            self.chart_throttle, self.chart_brake, self.chart_tires, self.chart_fuel,
            self.chart_gforce, self.chart_suspension, self.chart_tire_slip,
            self.chart_turbo, self.chart_temps,
        ]
        for chart in self.all_charts:
            charts_layout.addWidget(chart)
            chart.hovered_at_distance.connect(self._on_hover)
            chart.hover_left.connect(self._on_hover_leave)

        scroll.setWidget(charts_container)
        root.addWidget(scroll, stretch=1)

        self.refresh_lap_list()

    # ---------- controles ----------

    def _build_controls(self) -> QHBoxLayout:
        controls = QHBoxLayout()

        label_a = QLabel("Volta:")
        self.combo_a = QComboBox()
        self.combo_a.setStyleSheet(f"QComboBox {{ border-left: 3px solid {COLOR_A}; padding-left: 6px; }}")
        self.combo_a.setMinimumWidth(120)

        self.compare_check = QCheckBox("Comparar com:")
        self.compare_check.setToolTip("Ative para sobrepor uma segunda volta aos gráficos.")
        self.compare_check.toggled.connect(self._on_compare_toggled)

        self.combo_b = QComboBox()
        self.combo_b.setStyleSheet(f"QComboBox {{ border-left: 3px solid {COLOR_B}; padding-left: 6px; }}")
        self.combo_b.setMinimumWidth(120)
        self.combo_b.setEnabled(False)

        refresh_button = QPushButton("Atualizar")
        refresh_button.clicked.connect(self.refresh_lap_list)

        self.plot_button = QPushButton("Analisar")
        self.plot_button.clicked.connect(self._on_plot_clicked)

        controls.addWidget(label_a)
        controls.addWidget(self.combo_a)
        controls.addSpacing(12)
        controls.addWidget(self.compare_check)
        controls.addWidget(self.combo_b)
        controls.addStretch()
        controls.addWidget(refresh_button)
        controls.addWidget(self.plot_button)
        return controls

    def _on_compare_toggled(self, checked: bool):
        self._comparison_mode = checked
        self.combo_b.setEnabled(checked)
        self.plot_button.setText("Comparar" if checked else "Analisar")

    def _build_readout_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("card")
        grid = QGridLayout(frame)
        grid.setContentsMargins(16, 10, 16, 10)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(4)

        self._readout_headers = ["", "Volta A", "Volta B", "Diferença"]
        for col, text in enumerate(self._readout_headers):
            lbl = QLabel(text)
            lbl.setStyleSheet("color: #8a8e99; font-size: 11px; font-weight: 600;")
            grid.addWidget(lbl, 0, col)
        self._readout_header_widgets = [grid.itemAtPosition(0, c).widget() for c in range(4)]

        self.readout_rows = {}
        channels = [
            ("distance", "Distância"),
            ("speed_kmh", "Velocidade"),
            ("gear", "Marcha"),
            ("throttle", "Acelerador"),
            ("brake", "Freio"),
            ("tires", "Temp. pneus"),
            ("fuel_level", "Combustível"),
            ("g_lateral", "Força G lat."),
            ("g_longitudinal", "Força G long."),
            ("turbo_boost", "Turbo"),
            ("oil_temp", "Temp. óleo"),
            ("water_temp", "Temp. água"),
        ]
        for row, (key, label) in enumerate(channels, start=1):
            name_lbl = QLabel(label)
            name_lbl.setStyleSheet("font-size: 12px;")
            val_a = QLabel("--")
            val_b = QLabel("--")
            val_diff = QLabel("--")
            for w in (val_a, val_b, val_diff):
                w.setStyleSheet("font-size: 12px; font-weight: 600;")
            grid.addWidget(name_lbl, row, 0)
            grid.addWidget(val_a, row, 1)
            grid.addWidget(val_b, row, 2)
            grid.addWidget(val_diff, row, 3)
            self.readout_rows[key] = (val_a, val_b, val_diff)

        self.readout_frame = frame
        frame.setVisible(False)
        return frame

    def _build_sector_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(6)

        title = QLabel("Tempos por setor")
        title.setStyleSheet("font-size: 12px; font-weight: 600; color: #8a8e99;")
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(4)
        self._sector_headers = ["", "Volta A", "Volta B", "Diferença"]
        for col, text in enumerate(self._sector_headers):
            lbl = QLabel(text)
            lbl.setStyleSheet("color: #8a8e99; font-size: 11px; font-weight: 600;")
            grid.addWidget(lbl, 0, col)
        self._sector_header_widgets = [grid.itemAtPosition(0, c).widget() for c in range(4)]

        self.sector_rows = []
        for row in range(1, NUM_SECTORS + 1):
            name_lbl = QLabel(f"Setor {row}")
            name_lbl.setStyleSheet("font-size: 12px;")
            val_a = QLabel("--")
            val_b = QLabel("--")
            val_diff = QLabel("--")
            for w in (val_a, val_b, val_diff):
                w.setStyleSheet("font-size: 12px; font-weight: 600;")
            grid.addWidget(name_lbl, row, 0)
            grid.addWidget(val_a, row, 1)
            grid.addWidget(val_b, row, 2)
            grid.addWidget(val_diff, row, 3)
            self.sector_rows.append((val_a, val_b, val_diff))

        layout.addLayout(grid)

        self.sector_summary_label = QLabel("")
        self.sector_summary_label.setStyleSheet("color: #8a8e99; font-size: 12px;")
        self.sector_summary_label.setWordWrap(True)
        layout.addWidget(self.sector_summary_label)

        self.sector_frame = frame
        frame.setVisible(False)
        return frame

    def _update_panel_headers(self):
        if self._comparison_mode:
            labels_readout = ["", "Volta A", "Volta B", "Diferença"]
            labels_sector = ["", "Volta A", "Volta B", "Diferença"]
        else:
            labels_readout = ["", "Valor", "", ""]
            labels_sector = ["", "Tempo", "", ""]

        for i, text in enumerate(labels_readout):
            self._readout_header_widgets[i].setText(text)
            if i >= 2:
                self._readout_header_widgets[i].setVisible(self._comparison_mode)

        for i, text in enumerate(labels_sector):
            self._sector_header_widgets[i].setText(text)
            if i >= 2:
                self._sector_header_widgets[i].setVisible(self._comparison_mode)

        for key, (wa, wb, wd) in self.readout_rows.items():
            wb.setVisible(self._comparison_mode)
            wd.setVisible(self._comparison_mode)

        for va, vb, vd in self.sector_rows:
            vb.setVisible(self._comparison_mode)
            vd.setVisible(self._comparison_mode)

    # ---------- carregar voltas ----------

    def set_track(self, track_id: int):
        self.track_id = track_id
        self.refresh_lap_list()

    def refresh_lap_list(self):
        if self.track_id is None:
            self.combo_a.clear()
            self.combo_b.clear()
            self.summary_label.setText("Conecte-se a uma pista para ver a telemetria.")
            return

        laps = lap_storage.list_laps(self.track_id)

        current_a = self.combo_a.currentData()
        current_b = self.combo_b.currentData()

        self.combo_a.clear()
        self.combo_b.clear()

        for lap_id, lap_time_ms, recorded_at, car_name in laps:
            suffix = f" — {car_name}" if car_name else ""
            label = f"#{lap_id} — {format_ms(lap_time_ms)}{suffix}"
            self.combo_a.addItem(label, lap_id)
            self.combo_b.addItem(label, lap_id)

        self._select_by_data(self.combo_a, current_a)
        self._select_by_data(self.combo_b, current_b)

        if len(laps) >= 1 and current_a is None:
            self.combo_a.setCurrentIndex(0)
        if len(laps) >= 2 and current_b is None:
            self.combo_b.setCurrentIndex(1)

    @staticmethod
    def _select_by_data(combo: QComboBox, data):
        if data is None:
            return
        index = combo.findData(data)
        if index >= 0:
            combo.setCurrentIndex(index)

    # ---------- plotagem ----------

    def _on_plot_clicked(self):
        if self._comparison_mode:
            self._plot_comparison()
        else:
            self._plot_single()

    def _plot_single(self):
        self.lap_id_a = self.combo_a.currentData()
        self.lap_id_b = None
        self.series_b = None

        if self.lap_id_a is None:
            self.summary_label.setText("Salve pelo menos uma volta para analisar.")
            return

        frames_a = lap_storage.get_lap_frames(self.lap_id_a)
        if not frames_a:
            self.summary_label.setText("A volta selecionada não tem dados suficientes.")
            return

        self.series_a = LapSeries(frames_a)
        self._update_panel_headers()

        self.chart_speed.set_series([
            ("Volta", COLOR_A, self.series_a.points("speed_kmh")),
        ])
        self.chart_gear.set_series([
            ("Volta", COLOR_A, self.series_a.points("gear")),
        ], y_range=(0, 8))
        self.chart_throttle.set_series([
            ("Volta", COLOR_A, self.series_a.points("throttle")),
        ], y_range=(0, 100))
        self.chart_brake.set_series([
            ("Volta", COLOR_A, self._to_binary_step(self.series_a.points("brake"))),
        ], y_range=(-0.1, 1.1))

        self.chart_delta.setVisible(False)

        has_fuel = self.series_a.has_channel("fuel_level")
        self.chart_fuel.setVisible(has_fuel)
        if has_fuel:
            self.chart_fuel.set_series([
                ("Volta", COLOR_A, self.series_a.points("fuel_level")),
            ])

        has_tires = self.series_a.has_channel("tire_temp_fl")
        self.chart_tires.setVisible(has_tires)
        if has_tires:
            self.chart_tires.set_series([
                ("Volta", COLOR_A, self._average_tire_points(self.series_a)),
            ])

        has_gforce = self.series_a.has_channel("g_lateral")
        self.chart_gforce.setVisible(has_gforce)
        if has_gforce:
            self.chart_gforce.set_series([
                ("Lateral", COLOR_G_LAT, self.series_a.points("g_lateral")),
                ("Longitudinal", COLOR_G_LONG, self.series_a.points("g_longitudinal")),
            ])

        has_suspension = self.series_a.has_channel("suspension_fl")
        self.chart_suspension.setVisible(has_suspension)
        if has_suspension:
            self.chart_suspension.set_series([
                ("FL", COLOR_FL, self.series_a.points("suspension_fl")),
                ("FR", COLOR_FR, self.series_a.points("suspension_fr")),
                ("RL", COLOR_RL, self.series_a.points("suspension_rl")),
                ("RR", COLOR_RR, self.series_a.points("suspension_rr")),
            ])

        has_slip = self.series_a.has_channel("tire_slip_fl")
        self.chart_tire_slip.setVisible(has_slip)
        if has_slip:
            self.chart_tire_slip.set_series([
                ("FL", COLOR_FL, self.series_a.points("tire_slip_fl")),
                ("FR", COLOR_FR, self.series_a.points("tire_slip_fr")),
                ("RL", COLOR_RL, self.series_a.points("tire_slip_rl")),
                ("RR", COLOR_RR, self.series_a.points("tire_slip_rr")),
            ])

        has_turbo = self.series_a.has_channel("turbo_boost")
        self.chart_turbo.setVisible(has_turbo)
        if has_turbo:
            self.chart_turbo.set_series([
                ("Volta", COLOR_A, self.series_a.points("turbo_boost")),
            ])

        has_temps = self.series_a.has_channel("oil_temp")
        self.chart_temps.setVisible(has_temps)
        if has_temps:
            series_list = [("Óleo", COLOR_OIL, self.series_a.points("oil_temp"))]
            if self.series_a.has_channel("water_temp"):
                series_list.append(("Água", COLOR_WATER, self.series_a.points("water_temp")))
            self.chart_temps.set_series(series_list)

        has_position = self.series_a.has_channel("position_x")
        self.track_map.setVisible(has_position)
        if has_position:
            self.track_map.set_paths([
                ("Volta", COLOR_A, self.series_a.position_points()),
            ])

        self.readout_frame.setVisible(True)
        self._build_single_summary()
        self._build_single_sectors()

        reference_distance = self.series_a.max_distance
        bounds = sector_boundaries_m(reference_distance, NUM_SECTORS)
        sector_defs = [(d, f"S{i + 2}") for i, d in enumerate(bounds[:-1])]
        for chart in self.all_charts:
            if chart.isVisible():
                chart.set_sector_lines(sector_defs)

    def _plot_comparison(self):
        self.lap_id_a = self.combo_a.currentData()
        self.lap_id_b = self.combo_b.currentData()

        if self.lap_id_a is None or self.lap_id_b is None:
            self.summary_label.setText("Salve pelo menos duas voltas antes de comparar.")
            return

        frames_a = lap_storage.get_lap_frames(self.lap_id_a)
        frames_b = lap_storage.get_lap_frames(self.lap_id_b)

        if not frames_a or not frames_b:
            self.summary_label.setText("Uma das voltas selecionadas não tem dados suficientes.")
            return

        self.series_a = LapSeries(frames_a)
        self.series_b = LapSeries(frames_b)
        self._update_panel_headers()

        self.chart_speed.set_series([
            ("Volta A", COLOR_A, self.series_a.points("speed_kmh")),
            ("Volta B", COLOR_B, self.series_b.points("speed_kmh")),
        ])
        self.chart_gear.set_series([
            ("Volta A", COLOR_A, self.series_a.points("gear")),
            ("Volta B", COLOR_B, self.series_b.points("gear")),
        ], y_range=(0, 8))
        self.chart_throttle.set_series([
            ("Volta A", COLOR_A, self.series_a.points("throttle")),
            ("Volta B", COLOR_B, self.series_b.points("throttle")),
        ], y_range=(0, 100))
        self.chart_brake.set_series([
            ("Volta A", COLOR_A, self._to_binary_step(self.series_a.points("brake"))),
            ("Volta B", COLOR_B, self._to_binary_step(self.series_b.points("brake"))),
        ], y_range=(-0.1, 1.1))

        has_fuel = self.series_a.has_channel("fuel_level") or self.series_b.has_channel("fuel_level")
        self.chart_fuel.setVisible(has_fuel)
        if has_fuel:
            self.chart_fuel.set_series([
                ("Volta A", COLOR_A, self.series_a.points("fuel_level")),
                ("Volta B", COLOR_B, self.series_b.points("fuel_level")),
            ])

        has_tires = self.series_a.has_channel("tire_temp_fl") or self.series_b.has_channel("tire_temp_fl")
        self.chart_tires.setVisible(has_tires)
        if has_tires:
            self.chart_tires.set_series([
                ("Volta A", COLOR_A, self._average_tire_points(self.series_a)),
                ("Volta B", COLOR_B, self._average_tire_points(self.series_b)),
            ])

        has_gforce = self.series_a.has_channel("g_lateral") or self.series_b.has_channel("g_lateral")
        self.chart_gforce.setVisible(has_gforce)
        if has_gforce:
            self.chart_gforce.set_series([
                ("G Lat A", COLOR_A, self.series_a.points("g_lateral")),
                ("G Lat B", COLOR_B, self.series_b.points("g_lateral")),
            ])

        has_suspension = self.series_a.has_channel("suspension_fl") or self.series_b.has_channel("suspension_fl")
        self.chart_suspension.setVisible(has_suspension)
        if has_suspension:
            self.chart_suspension.set_series([
                ("A", COLOR_A, self._average_4wheel_points(self.series_a, "suspension")),
                ("B", COLOR_B, self._average_4wheel_points(self.series_b, "suspension")),
            ])

        has_slip = self.series_a.has_channel("tire_slip_fl") or self.series_b.has_channel("tire_slip_fl")
        self.chart_tire_slip.setVisible(has_slip)
        if has_slip:
            self.chart_tire_slip.set_series([
                ("A", COLOR_A, self._average_4wheel_points(self.series_a, "tire_slip")),
                ("B", COLOR_B, self._average_4wheel_points(self.series_b, "tire_slip")),
            ])

        has_turbo = self.series_a.has_channel("turbo_boost") or self.series_b.has_channel("turbo_boost")
        self.chart_turbo.setVisible(has_turbo)
        if has_turbo:
            self.chart_turbo.set_series([
                ("A", COLOR_A, self.series_a.points("turbo_boost")),
                ("B", COLOR_B, self.series_b.points("turbo_boost")),
            ])

        has_temps = self.series_a.has_channel("oil_temp") or self.series_b.has_channel("oil_temp")
        self.chart_temps.setVisible(has_temps)
        if has_temps:
            self.chart_temps.set_series([
                ("Óleo A", COLOR_A, self.series_a.points("oil_temp")),
                ("Óleo B", COLOR_B, self.series_b.points("oil_temp")),
            ])

        has_position = self.series_a.has_channel("position_x") or self.series_b.has_channel("position_x")
        self.track_map.setVisible(has_position)
        if has_position:
            self.track_map.set_paths([
                ("Volta A", COLOR_A, self.series_a.position_points()),
                ("Volta B", COLOR_B, self.series_b.position_points()),
            ])

        delta_points = compute_delta_series(self.series_a, self.series_b)
        self.chart_delta.setVisible(True)
        self.chart_delta.set_series([("Delta", COLOR_DELTA, delta_points)])

        self.readout_frame.setVisible(True)
        self._build_comparison_summary(delta_points)
        self._build_sector_comparison()

        reference_distance = max(self.series_a.max_distance, self.series_b.max_distance)
        bounds = sector_boundaries_m(reference_distance, NUM_SECTORS)
        sector_defs = [(d, f"S{i + 2}") for i, d in enumerate(bounds[:-1])]
        for chart in self.all_charts:
            if chart.isVisible():
                chart.set_sector_lines(sector_defs)

    @staticmethod
    def _to_binary_step(points, threshold: float = 5.0):
        stepped = []
        prev_state = None
        for x, v in points:
            state = 1 if v > threshold else 0
            if prev_state is not None and state != prev_state:
                stepped.append((x, prev_state))
            stepped.append((x, state))
            prev_state = state
        return stepped

    @staticmethod
    def _average_tire_points(series: LapSeries):
        fl = series.points("tire_temp_fl")
        fr = series.points("tire_temp_fr")
        rl = series.points("tire_temp_rl")
        rr = series.points("tire_temp_rr")
        n = min(len(fl), len(fr), len(rl), len(rr))
        return [
            (fl[i][0], (fl[i][1] + fr[i][1] + rl[i][1] + rr[i][1]) / 4)
            for i in range(n)
        ]

    @staticmethod
    def _average_4wheel_points(series: LapSeries, prefix: str):
        fl = series.points(f"{prefix}_fl")
        fr = series.points(f"{prefix}_fr")
        rl = series.points(f"{prefix}_rl")
        rr = series.points(f"{prefix}_rr")
        n = min(len(fl), len(fr), len(rl), len(rr))
        return [
            (fl[i][0], (fl[i][1] + fr[i][1] + rl[i][1] + rr[i][1]) / 4)
            for i in range(n)
        ]

    @staticmethod
    def _fuel_used(series: LapSeries):
        points = series.points("fuel_level")
        if len(points) < 2:
            return None
        return points[0][1] - points[-1][1]

    def _build_single_summary(self):
        if self.series_a is None or self.series_a.is_empty:
            return
        text = f"Analisando volta #{self.lap_id_a}. "
        text += f"Distância total: {self.series_a.max_distance:.0f}m."
        fuel = self._fuel_used(self.series_a)
        if fuel is not None:
            text += f" Combustível consumido: {fuel:.1f}L."
        self.summary_label.setText(text)

    def _build_single_sectors(self):
        if self.series_a is None or self.series_a.is_empty:
            self.sector_frame.setVisible(False)
            return
        reference_distance = self.series_a.max_distance
        bounds = sector_boundaries_m(reference_distance, NUM_SECTORS)
        sectors = sector_times_from_series(self.series_a, bounds)

        for row, st in enumerate(sectors):
            val_a, val_b, val_diff = self.sector_rows[row]
            val_a.setText(format_ms(st) if st is not None else "--")

        self.sector_summary_label.setText("")
        self.sector_frame.setVisible(True)

    def _build_comparison_summary(self, delta_points):
        if not delta_points:
            self.summary_label.setText(
                f"Comparando volta #{self.lap_id_a} e volta #{self.lap_id_b}."
            )
            return

        final_delta = delta_points[-1][1]
        faster = "B" if final_delta < 0 else "A"
        diff_abs = abs(final_delta)

        biggest_gain = min(delta_points, key=lambda p: p[1])
        biggest_loss = max(delta_points, key=lambda p: p[1])

        text = (
            f"Volta {faster} foi {diff_abs:.3f}s mais rápida no trecho comparado. "
            f"Maior ganho de B em relação a A: {abs(biggest_gain[1]):.2f}s perto de {biggest_gain[0]:.0f}m. "
            f"Maior perda de B em relação a A: {biggest_loss[1]:.2f}s perto de {biggest_loss[0]:.0f}m."
        )

        fuel_a_used = self._fuel_used(self.series_a)
        fuel_b_used = self._fuel_used(self.series_b)
        if fuel_a_used is not None and fuel_b_used is not None:
            text += f" Combustível consumido — A: {fuel_a_used:.1f}L, B: {fuel_b_used:.1f}L."

        self.summary_label.setText(text)

    def _build_sector_comparison(self):
        reference_distance = max(self.series_a.max_distance, self.series_b.max_distance)
        bounds = sector_boundaries_m(reference_distance, NUM_SECTORS)
        sectors_a = sector_times_from_series(self.series_a, bounds)
        sectors_b = sector_times_from_series(self.series_b, bounds)

        for row, (ta, tb) in enumerate(zip(sectors_a, sectors_b)):
            val_a, val_b, val_diff = self.sector_rows[row]
            if ta is None or tb is None:
                val_a.setText(format_ms(ta) if ta is not None else "--")
                val_b.setText(format_ms(tb) if tb is not None else "--")
                val_diff.setText("--")
                continue
            val_a.setText(format_ms(ta))
            val_b.setText(format_ms(tb))
            diff_ms = tb - ta
            sign = "+" if diff_ms >= 0 else ""
            val_diff.setText(f"{sign}{diff_ms / 1000:.3f}s")
            color = "#ff5c5c" if diff_ms > 0 else "#3ddc84" if diff_ms < 0 else "#e8e8ec"
            val_diff.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {color};")

        best_ms, choices = best_combined_sectors(sectors_a, sectors_b)
        if best_ms is not None:
            picks = ", ".join(
                f"S{i + 1}: {choice}" for i, choice in enumerate(choices) if choice
            )
            self.sector_summary_label.setText(
                f"Melhor combinação possível: {format_ms(best_ms)} ({picks})."
            )
        else:
            self.sector_summary_label.setText("")

        self.sector_frame.setVisible(True)

    # ---------- cursor sincronizado ----------

    def _on_hover(self, distance_m: float):
        for chart in self.all_charts:
            if chart.isVisible():
                chart.show_crosshair(distance_m)
        self._update_readout(distance_m)
        self._update_track_map_markers(distance_m)

    def _on_hover_leave(self):
        for chart in self.all_charts:
            chart.hide_crosshair()
        self.track_map.clear_markers()

    def _update_track_map_markers(self, distance_m: float):
        if self.series_a is None:
            return
        xa = self.series_a.value_at(distance_m, "position_x")
        za = self.series_a.value_at(distance_m, "position_z")
        markers = [(xa, za, COLOR_MARKER_A)]

        if self.series_b is not None:
            xb = self.series_b.value_at(distance_m, "position_x")
            zb = self.series_b.value_at(distance_m, "position_z")
            markers.append((xb, zb, COLOR_MARKER_B))

        self.track_map.set_markers(markers)

    def _update_readout(self, distance_m: float):
        if self.series_a is None:
            return

        def set_row(key, val_a, val_b, fmt="{:.1f}", unit=""):
            widgets = self.readout_rows.get(key)
            if not widgets:
                return
            wa, wb, wd = widgets
            if val_a is None:
                wa.setText("--")
                wb.setText("--")
                wd.setText("--")
                return
            wa.setText(fmt.format(val_a) + unit)
            if self._comparison_mode and val_b is not None:
                wb.setText(fmt.format(val_b) + unit)
                diff = val_b - val_a
                wd.setText(("+" if diff >= 0 else "") + fmt.format(diff) + unit)
            elif not self._comparison_mode:
                pass

        wa, wb, wd = self.readout_rows["distance"]
        wa.setText(f"{distance_m:.0f}m")
        wb.setText(f"{distance_m:.0f}m" if self._comparison_mode else "")
        wd.setText("")

        set_row("speed_kmh",
                self.series_a.value_at(distance_m, "speed_kmh"),
                self.series_b.value_at(distance_m, "speed_kmh") if self.series_b else None,
                unit=" km/h")
        set_row("gear",
                self.series_a.value_at(distance_m, "gear"),
                self.series_b.value_at(distance_m, "gear") if self.series_b else None,
                fmt="{:.0f}")
        set_row("throttle",
                self.series_a.value_at(distance_m, "throttle"),
                self.series_b.value_at(distance_m, "throttle") if self.series_b else None,
                unit="%")
        set_row("brake",
                self.series_a.value_at(distance_m, "brake"),
                self.series_b.value_at(distance_m, "brake") if self.series_b else None,
                unit="%")
        set_row("fuel_level",
                self.series_a.value_at(distance_m, "fuel_level"),
                self.series_b.value_at(distance_m, "fuel_level") if self.series_b else None,
                unit="L")

        tire_a = self._tire_avg_at(self.series_a, distance_m)
        tire_b = self._tire_avg_at(self.series_b, distance_m) if self.series_b else None
        set_row("tires", tire_a, tire_b, unit="°C")
        set_row("g_lateral",
                self.series_a.value_at(distance_m, "g_lateral"),
                self.series_b.value_at(distance_m, "g_lateral") if self.series_b else None,
                fmt="{:.2f}", unit="G")
        set_row("g_longitudinal",
                self.series_a.value_at(distance_m, "g_longitudinal"),
                self.series_b.value_at(distance_m, "g_longitudinal") if self.series_b else None,
                fmt="{:.2f}", unit="G")
        set_row("turbo_boost",
                self.series_a.value_at(distance_m, "turbo_boost"),
                self.series_b.value_at(distance_m, "turbo_boost") if self.series_b else None,
                fmt="{:.2f}", unit=" bar")
        set_row("oil_temp",
                self.series_a.value_at(distance_m, "oil_temp"),
                self.series_b.value_at(distance_m, "oil_temp") if self.series_b else None,
                unit="°C")
        set_row("water_temp",
                self.series_a.value_at(distance_m, "water_temp"),
                self.series_b.value_at(distance_m, "water_temp") if self.series_b else None,
                unit="°C")

    @staticmethod
    def _tire_avg_at(series: LapSeries, distance_m: float):
        values = [
            series.value_at(distance_m, ch)
            for ch in ("tire_temp_fl", "tire_temp_fr", "tire_temp_rl", "tire_temp_rr")
        ]
        if any(v is None for v in values):
            return None
        return sum(values) / len(values)
