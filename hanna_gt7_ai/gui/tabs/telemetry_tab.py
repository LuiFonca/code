"""
Aba "Telemetria" — modo padrão: análise de uma única volta com todos os
canais de telemetria. Modo comparação: sobreposição de múltiplas voltas.
Arquitetura única que suporta ambos os modos sem duplicação de código.

Gráficos sincronizados pelo eixo de distância (ou tempo, selecionável),
com crosshair, tooltips, zoom/pan via mouse, e painel de valores no hover.
Inclui mosaico de pneus (temperatura e derrapagem por roda) e análise
de slip angle com normalização 0-100%.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QScrollArea, QGridLayout, QFrame, QCheckBox, QButtonGroup, QRadioButton
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
COLOR_RPM = "#e06cff"
NUM_SECTORS = 3

SLIP_ANGLE_MAX_DEG = 12.0


def _estimate_slip_angle_deg(slip_value: float) -> float:
    return min(abs(slip_value) * SLIP_ANGLE_MAX_DEG, SLIP_ANGLE_MAX_DEG)


def _normalize_slip_pct(slip_value: float) -> float:
    return min(abs(slip_value) * 100.0, 100.0)


class TelemetryTab(QWidget):
    def __init__(self, track_id):
        super().__init__()
        self.track_id = track_id
        self.series_a: LapSeries | None = None
        self.series_b: LapSeries | None = None
        self.lap_id_a = None
        self.lap_id_b = None
        self._comparison_mode = False
        self._use_time_axis = False

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
        self.chart_rpm = SyncedMiniChart("RPM")
        self.chart_gear = SyncedMiniChart("Marcha")
        self.chart_throttle = SyncedMiniChart("Acelerador (%)")
        self.chart_brake = SyncedMiniChart("Freio (%)")
        self.chart_fuel = SyncedMiniChart("Combustível (L)")
        self.chart_gforce = SyncedMiniChart("Força G (lateral / longitudinal)")
        self.chart_turbo = SyncedMiniChart("Turbo (bar)")
        self.chart_temps = SyncedMiniChart("Temperatura motor (°C)")

        self.all_charts = [
            self.chart_speed, self.chart_delta, self.chart_rpm, self.chart_gear,
            self.chart_throttle, self.chart_brake, self.chart_fuel,
            self.chart_gforce, self.chart_turbo, self.chart_temps,
        ]
        for chart in self.all_charts:
            charts_layout.addWidget(chart)
            chart.hovered_at_distance.connect(self._on_hover)
            chart.hover_left.connect(self._on_hover_leave)

        # --- Mosaico de pneus: temperatura por roda ---
        self._tire_temp_label = QLabel("TEMPERATURA DE PNEUS POR RODA")
        self._tire_temp_label.setStyleSheet(
            "color: #c8cad0; font-size: 12px; font-weight: 700; letter-spacing: 1px;"
        )
        charts_layout.addWidget(self._tire_temp_label)

        tire_temp_grid = QHBoxLayout()
        tire_temp_grid.setSpacing(6)
        self.chart_tire_fl = SyncedMiniChart("Temp. DE (°C)", height=100)
        self.chart_tire_fr = SyncedMiniChart("Temp. DD (°C)", height=100)
        self.chart_tire_rl = SyncedMiniChart("Temp. TE (°C)", height=100)
        self.chart_tire_rr = SyncedMiniChart("Temp. TD (°C)", height=100)
        self._tire_temp_charts = [self.chart_tire_fl, self.chart_tire_fr, self.chart_tire_rl, self.chart_tire_rr]

        tire_top = QHBoxLayout()
        tire_top.setSpacing(6)
        tire_top.addWidget(self.chart_tire_fl, stretch=1)
        tire_top.addWidget(self.chart_tire_fr, stretch=1)

        tire_bottom = QHBoxLayout()
        tire_bottom.setSpacing(6)
        tire_bottom.addWidget(self.chart_tire_rl, stretch=1)
        tire_bottom.addWidget(self.chart_tire_rr, stretch=1)

        tire_mosaic = QVBoxLayout()
        tire_mosaic.setSpacing(4)
        tire_mosaic.addLayout(tire_top)
        tire_mosaic.addLayout(tire_bottom)

        tire_wrapper = QWidget()
        tire_wrapper.setLayout(tire_mosaic)
        charts_layout.addWidget(tire_wrapper)
        self._tire_temp_wrapper = tire_wrapper

        for chart in self._tire_temp_charts:
            chart.hovered_at_distance.connect(self._on_hover)
            chart.hover_left.connect(self._on_hover_leave)

        # --- Suspensão por roda ---
        self._suspension_label = QLabel("SUSPENSÃO POR RODA")
        self._suspension_label.setStyleSheet(
            "color: #c8cad0; font-size: 12px; font-weight: 700; letter-spacing: 1px;"
        )
        charts_layout.addWidget(self._suspension_label)

        self.chart_susp_fl = SyncedMiniChart("Susp. DE (mm)", height=100)
        self.chart_susp_fr = SyncedMiniChart("Susp. DD (mm)", height=100)
        self.chart_susp_rl = SyncedMiniChart("Susp. TE (mm)", height=100)
        self.chart_susp_rr = SyncedMiniChart("Susp. TD (mm)", height=100)
        self._suspension_charts = [self.chart_susp_fl, self.chart_susp_fr, self.chart_susp_rl, self.chart_susp_rr]

        susp_top = QHBoxLayout()
        susp_top.setSpacing(6)
        susp_top.addWidget(self.chart_susp_fl, stretch=1)
        susp_top.addWidget(self.chart_susp_fr, stretch=1)
        susp_bottom = QHBoxLayout()
        susp_bottom.setSpacing(6)
        susp_bottom.addWidget(self.chart_susp_rl, stretch=1)
        susp_bottom.addWidget(self.chart_susp_rr, stretch=1)
        susp_mosaic = QVBoxLayout()
        susp_mosaic.setSpacing(4)
        susp_mosaic.addLayout(susp_top)
        susp_mosaic.addLayout(susp_bottom)
        susp_wrapper = QWidget()
        susp_wrapper.setLayout(susp_mosaic)
        charts_layout.addWidget(susp_wrapper)
        self._suspension_wrapper = susp_wrapper

        for chart in self._suspension_charts:
            chart.hovered_at_distance.connect(self._on_hover)
            chart.hover_left.connect(self._on_hover_leave)

        # --- Mosaico de Slip Angle (#26-#31) ---
        self._slip_label = QLabel("SLIP ANGLE / DERRAPAGEM POR RODA")
        self._slip_label.setStyleSheet(
            "color: #c8cad0; font-size: 12px; font-weight: 700; letter-spacing: 1px;"
        )
        charts_layout.addWidget(self._slip_label)

        self.chart_slip_fl = SyncedMiniChart("Slip DE (°/100%)", height=100)
        self.chart_slip_fr = SyncedMiniChart("Slip DD (°/100%)", height=100)
        self.chart_slip_rl = SyncedMiniChart("Slip TE (°/100%)", height=100)
        self.chart_slip_rr = SyncedMiniChart("Slip TD (°/100%)", height=100)
        self._slip_charts = [self.chart_slip_fl, self.chart_slip_fr, self.chart_slip_rl, self.chart_slip_rr]

        self._slip_indicator = QLabel("")
        self._slip_indicator.setAlignment(Qt.AlignCenter)
        self._slip_indicator.setMinimumSize(120, 50)
        self._slip_indicator.setStyleSheet(
            "background-color: #1a1d25; border: 1px solid #23262f; "
            "border-radius: 8px; color: #e8e8ec; font-size: 14px; font-weight: 700;"
        )

        slip_top = QHBoxLayout()
        slip_top.setSpacing(6)
        slip_top.addWidget(self.chart_slip_fl, stretch=1)
        slip_top.addWidget(self.chart_slip_fr, stretch=1)
        slip_middle = QHBoxLayout()
        slip_middle.addStretch()
        slip_middle.addWidget(self._slip_indicator)
        slip_middle.addStretch()
        slip_bottom = QHBoxLayout()
        slip_bottom.setSpacing(6)
        slip_bottom.addWidget(self.chart_slip_rl, stretch=1)
        slip_bottom.addWidget(self.chart_slip_rr, stretch=1)

        slip_mosaic = QVBoxLayout()
        slip_mosaic.setSpacing(4)
        slip_mosaic.addLayout(slip_top)
        slip_mosaic.addLayout(slip_middle)
        slip_mosaic.addLayout(slip_bottom)

        slip_wrapper = QWidget()
        slip_wrapper.setLayout(slip_mosaic)
        charts_layout.addWidget(slip_wrapper)
        self._slip_wrapper = slip_wrapper

        for chart in self._slip_charts:
            chart.hovered_at_distance.connect(self._on_hover)
            chart.hover_left.connect(self._on_hover_leave)

        scroll.setWidget(charts_container)
        root.addWidget(scroll, stretch=1)

        self.refresh_lap_list()

    # ---------- controles ----------

    def _build_controls(self) -> QHBoxLayout:
        controls = QHBoxLayout()

        label_a = QLabel("Volta:")
        label_a.setStyleSheet("color: #e8e8ec; font-size: 13px; font-weight: 600;")
        self.combo_a = QComboBox()
        self.combo_a.setStyleSheet(
            f"QComboBox {{ border-left: 3px solid {COLOR_A}; padding-left: 6px; "
            f"color: #e8e8ec; background-color: #1c1f27; min-width: 180px; }}"
        )

        self.compare_check = QCheckBox("Comparar com:")
        self.compare_check.setStyleSheet("QCheckBox { color: #e8e8ec; font-size: 13px; font-weight: 600; }")
        self.compare_check.setToolTip("Ative para sobrepor uma segunda volta aos gráficos.")
        self.compare_check.toggled.connect(self._on_compare_toggled)

        self.combo_b = QComboBox()
        self.combo_b.setStyleSheet(
            f"QComboBox {{ border-left: 3px solid {COLOR_B}; padding-left: 6px; "
            f"color: #e8e8ec; background-color: #1c1f27; min-width: 180px; }}"
        )
        self.combo_b.setEnabled(False)

        axis_label = QLabel("Eixo:")
        axis_label.setStyleSheet("color: #c8cad0; font-size: 12px; font-weight: 600;")
        self._radio_distance = QRadioButton("Distância")
        self._radio_distance.setChecked(True)
        self._radio_distance.setStyleSheet("color: #e8e8ec; font-size: 12px;")
        self._radio_time = QRadioButton("Tempo")
        self._radio_time.setStyleSheet("color: #e8e8ec; font-size: 12px;")
        self._axis_group = QButtonGroup(self)
        self._axis_group.addButton(self._radio_distance, 0)
        self._axis_group.addButton(self._radio_time, 1)
        self._axis_group.idClicked.connect(self._on_axis_changed)

        refresh_button = QPushButton("Atualizar")
        refresh_button.clicked.connect(self.refresh_lap_list)

        self.plot_button = QPushButton("Analisar")
        self.plot_button.clicked.connect(self._on_plot_clicked)

        controls.addWidget(label_a)
        controls.addWidget(self.combo_a)
        controls.addSpacing(12)
        controls.addWidget(self.compare_check)
        controls.addWidget(self.combo_b)
        controls.addSpacing(12)
        controls.addWidget(axis_label)
        controls.addWidget(self._radio_distance)
        controls.addWidget(self._radio_time)
        controls.addStretch()
        controls.addWidget(refresh_button)
        controls.addWidget(self.plot_button)
        return controls

    def _on_compare_toggled(self, checked: bool):
        self._comparison_mode = checked
        self.combo_b.setEnabled(checked)
        self.plot_button.setText("Comparar" if checked else "Analisar")

    def _on_axis_changed(self, btn_id: int):
        self._use_time_axis = (btn_id == 1)

    def _get_points(self, series: LapSeries, channel: str):
        if self._use_time_axis:
            return series.points_by_time(channel)
        return series.points(channel)

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
            ("rpm", "RPM"),
            ("gear", "Marcha"),
            ("throttle", "Acelerador"),
            ("brake", "Freio"),
            ("tires", "Temp. pneus"),
            ("fuel_level", "Combustível"),
            ("g_lateral", "Força G lat."),
            ("g_longitudinal", "Força G long."),
            ("slip_angle", "Slip Angle"),
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
            ("Volta", COLOR_A, self._get_points(self.series_a, "speed_kmh")),
        ])
        self.chart_rpm.set_series([
            ("Volta", COLOR_RPM, self._get_points(self.series_a, "rpm")),
        ])
        self.chart_gear.set_series([
            ("Volta", COLOR_A, self._get_points(self.series_a, "gear")),
        ], y_range=(0, 8))
        self.chart_throttle.set_series([
            ("Volta", COLOR_A, self._get_points(self.series_a, "throttle")),
        ], y_range=(0, 100))
        self.chart_brake.set_series([
            ("Volta", COLOR_A, self._to_binary_step(self._get_points(self.series_a, "brake"))),
        ], y_range=(-0.1, 1.1))

        self.chart_delta.setVisible(False)

        has_fuel = self.series_a.has_channel("fuel_level")
        self.chart_fuel.setVisible(has_fuel)
        if has_fuel:
            self.chart_fuel.set_series([
                ("Volta", COLOR_A, self._get_points(self.series_a, "fuel_level")),
            ])

        has_tires = self.series_a.has_channel("tire_temp_fl")
        self._tire_temp_wrapper.setVisible(has_tires)
        self._tire_temp_label.setVisible(has_tires)
        if has_tires:
            self.chart_tire_fl.set_series([("DE", COLOR_FL, self._get_points(self.series_a, "tire_temp_fl"))])
            self.chart_tire_fr.set_series([("DD", COLOR_FR, self._get_points(self.series_a, "tire_temp_fr"))])
            self.chart_tire_rl.set_series([("TE", COLOR_RL, self._get_points(self.series_a, "tire_temp_rl"))])
            self.chart_tire_rr.set_series([("TD", COLOR_RR, self._get_points(self.series_a, "tire_temp_rr"))])

        has_gforce = self.series_a.has_channel("g_lateral")
        self.chart_gforce.setVisible(has_gforce)
        if has_gforce:
            self.chart_gforce.set_series([
                ("Lateral", COLOR_G_LAT, self._get_points(self.series_a, "g_lateral")),
                ("Longitudinal", COLOR_G_LONG, self._get_points(self.series_a, "g_longitudinal")),
            ])

        has_suspension = self.series_a.has_channel("suspension_fl")
        self._suspension_wrapper.setVisible(has_suspension)
        self._suspension_label.setVisible(has_suspension)
        if has_suspension:
            self.chart_susp_fl.set_series([("DE", COLOR_FL, self._get_points(self.series_a, "suspension_fl"))])
            self.chart_susp_fr.set_series([("DD", COLOR_FR, self._get_points(self.series_a, "suspension_fr"))])
            self.chart_susp_rl.set_series([("TE", COLOR_RL, self._get_points(self.series_a, "suspension_rl"))])
            self.chart_susp_rr.set_series([("TD", COLOR_RR, self._get_points(self.series_a, "suspension_rr"))])

        has_slip = self.series_a.has_channel("tire_slip_fl")
        self._slip_wrapper.setVisible(has_slip)
        self._slip_label.setVisible(has_slip)
        if has_slip:
            self.chart_slip_fl.set_series([
                ("DE °", COLOR_FL, self._slip_angle_points(self.series_a, "tire_slip_fl")),
            ], y_range=(0, SLIP_ANGLE_MAX_DEG))
            self.chart_slip_fr.set_series([
                ("DD °", COLOR_FR, self._slip_angle_points(self.series_a, "tire_slip_fr")),
            ], y_range=(0, SLIP_ANGLE_MAX_DEG))
            self.chart_slip_rl.set_series([
                ("TE °", COLOR_RL, self._slip_angle_points(self.series_a, "tire_slip_rl")),
            ], y_range=(0, SLIP_ANGLE_MAX_DEG))
            self.chart_slip_rr.set_series([
                ("TD °", COLOR_RR, self._slip_angle_points(self.series_a, "tire_slip_rr")),
            ], y_range=(0, SLIP_ANGLE_MAX_DEG))
            self._update_slip_indicator(self.series_a)

        has_turbo = self.series_a.has_channel("turbo_boost")
        self.chart_turbo.setVisible(has_turbo)
        if has_turbo:
            self.chart_turbo.set_series([
                ("Volta", COLOR_A, self._get_points(self.series_a, "turbo_boost")),
            ])

        has_temps = self.series_a.has_channel("oil_temp")
        self.chart_temps.setVisible(has_temps)
        if has_temps:
            series_list = [("Óleo", COLOR_OIL, self._get_points(self.series_a, "oil_temp"))]
            if self.series_a.has_channel("water_temp"):
                series_list.append(("Água", COLOR_WATER, self._get_points(self.series_a, "water_temp")))
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

        if not self._use_time_axis:
            reference_distance = self.series_a.max_distance
            bounds = sector_boundaries_m(reference_distance, NUM_SECTORS)
            sector_defs = [(d, f"S{i + 2}") for i, d in enumerate(bounds[:-1])]
            all_visible = self.all_charts + self._tire_temp_charts + self._suspension_charts + self._slip_charts
            for chart in all_visible:
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
            ("Volta A", COLOR_A, self._get_points(self.series_a, "speed_kmh")),
            ("Volta B", COLOR_B, self._get_points(self.series_b, "speed_kmh")),
        ])
        self.chart_rpm.set_series([
            ("Volta A", COLOR_RPM, self._get_points(self.series_a, "rpm")),
            ("Volta B", COLOR_B, self._get_points(self.series_b, "rpm")),
        ])
        self.chart_gear.set_series([
            ("Volta A", COLOR_A, self._get_points(self.series_a, "gear")),
            ("Volta B", COLOR_B, self._get_points(self.series_b, "gear")),
        ], y_range=(0, 8))
        self.chart_throttle.set_series([
            ("Volta A", COLOR_A, self._get_points(self.series_a, "throttle")),
            ("Volta B", COLOR_B, self._get_points(self.series_b, "throttle")),
        ], y_range=(0, 100))
        self.chart_brake.set_series([
            ("Volta A", COLOR_A, self._to_binary_step(self._get_points(self.series_a, "brake"))),
            ("Volta B", COLOR_B, self._to_binary_step(self._get_points(self.series_b, "brake"))),
        ], y_range=(-0.1, 1.1))

        has_fuel = self.series_a.has_channel("fuel_level") or self.series_b.has_channel("fuel_level")
        self.chart_fuel.setVisible(has_fuel)
        if has_fuel:
            self.chart_fuel.set_series([
                ("Volta A", COLOR_A, self._get_points(self.series_a, "fuel_level")),
                ("Volta B", COLOR_B, self._get_points(self.series_b, "fuel_level")),
            ])

        has_tires = self.series_a.has_channel("tire_temp_fl") or self.series_b.has_channel("tire_temp_fl")
        self._tire_temp_wrapper.setVisible(has_tires)
        self._tire_temp_label.setVisible(has_tires)
        if has_tires:
            for chart, ch in zip(self._tire_temp_charts, ["tire_temp_fl", "tire_temp_fr", "tire_temp_rl", "tire_temp_rr"]):
                chart.set_series([
                    ("A", COLOR_A, self._get_points(self.series_a, ch)),
                    ("B", COLOR_B, self._get_points(self.series_b, ch)),
                ])

        has_gforce = self.series_a.has_channel("g_lateral") or self.series_b.has_channel("g_lateral")
        self.chart_gforce.setVisible(has_gforce)
        if has_gforce:
            self.chart_gforce.set_series([
                ("G Lat A", COLOR_A, self._get_points(self.series_a, "g_lateral")),
                ("G Lat B", COLOR_B, self._get_points(self.series_b, "g_lateral")),
            ])

        has_suspension = self.series_a.has_channel("suspension_fl") or self.series_b.has_channel("suspension_fl")
        self._suspension_wrapper.setVisible(has_suspension)
        self._suspension_label.setVisible(has_suspension)
        if has_suspension:
            for chart, ch in zip(self._suspension_charts, ["suspension_fl", "suspension_fr", "suspension_rl", "suspension_rr"]):
                chart.set_series([
                    ("A", COLOR_A, self._get_points(self.series_a, ch)),
                    ("B", COLOR_B, self._get_points(self.series_b, ch)),
                ])

        has_slip = self.series_a.has_channel("tire_slip_fl") or self.series_b.has_channel("tire_slip_fl")
        self._slip_wrapper.setVisible(has_slip)
        self._slip_label.setVisible(has_slip)
        if has_slip:
            for chart, ch in zip(self._slip_charts, ["tire_slip_fl", "tire_slip_fr", "tire_slip_rl", "tire_slip_rr"]):
                chart.set_series([
                    ("A", COLOR_A, self._slip_angle_points(self.series_a, ch)),
                    ("B", COLOR_B, self._slip_angle_points(self.series_b, ch)),
                ], y_range=(0, SLIP_ANGLE_MAX_DEG))
            self._update_slip_indicator(self.series_a)

        has_turbo = self.series_a.has_channel("turbo_boost") or self.series_b.has_channel("turbo_boost")
        self.chart_turbo.setVisible(has_turbo)
        if has_turbo:
            self.chart_turbo.set_series([
                ("A", COLOR_A, self._get_points(self.series_a, "turbo_boost")),
                ("B", COLOR_B, self._get_points(self.series_b, "turbo_boost")),
            ])

        has_temps = self.series_a.has_channel("oil_temp") or self.series_b.has_channel("oil_temp")
        self.chart_temps.setVisible(has_temps)
        if has_temps:
            self.chart_temps.set_series([
                ("Óleo A", COLOR_A, self._get_points(self.series_a, "oil_temp")),
                ("Óleo B", COLOR_B, self._get_points(self.series_b, "oil_temp")),
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

        if not self._use_time_axis:
            reference_distance = max(self.series_a.max_distance, self.series_b.max_distance)
            bounds = sector_boundaries_m(reference_distance, NUM_SECTORS)
            sector_defs = [(d, f"S{i + 2}") for i, d in enumerate(bounds[:-1])]
            all_visible = self.all_charts + self._tire_temp_charts + self._suspension_charts + self._slip_charts
            for chart in all_visible:
                if chart.isVisible():
                    chart.set_sector_lines(sector_defs)

    # ---------- helpers ----------

    def _slip_angle_points(self, series: LapSeries, channel: str):
        raw = self._get_points(series, channel)
        return [(x, _estimate_slip_angle_deg(v)) for x, v in raw]

    def _update_slip_indicator(self, series: LapSeries):
        channels = ["tire_slip_fl", "tire_slip_fr", "tire_slip_rl", "tire_slip_rr"]
        avgs = []
        for ch in channels:
            pts = series.points(ch)
            if pts:
                avg = sum(_normalize_slip_pct(v) for _, v in pts) / len(pts)
                avgs.append(avg)
        if not avgs:
            self._slip_indicator.setText("Slip: N/D")
            return
        overall = sum(avgs) / len(avgs)
        if overall < 15:
            color = "#3ddc84"
            level = "Baixo"
        elif overall < 40:
            color = "#f2c94c"
            level = "Moderado"
        else:
            color = "#ff5c5c"
            level = "Alto"
        self._slip_indicator.setText(f"Slip médio: {overall:.0f}%\n{level}")
        self._slip_indicator.setStyleSheet(
            f"background-color: #1a1d25; border: 2px solid {color}; "
            f"border-radius: 8px; color: {color}; font-size: 14px; font-weight: 700; padding: 8px;"
        )

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
    def _fuel_used(series: LapSeries):
        points = series.points("fuel_level")
        if len(points) < 2:
            return None
        return points[0][1] - points[-1][1]

    def _build_single_summary(self):
        if self.series_a is None or self.series_a.is_empty:
            return
        text = f"Analisando volta #{self.lap_id_a}. "
        text += f"Distância total: {self.series_a.max_distance:.0f}m. "
        text += f"Tempo: {format_ms(int(self.series_a.max_time * 1000))}."
        fuel = self._fuel_used(self.series_a)
        if fuel is not None:
            text += f" Combustível consumido: {fuel:.2f}L."
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
            f"Maior ganho de B em relação a A: {abs(biggest_gain[1]):.3f}s perto de {biggest_gain[0]:.0f}m. "
            f"Maior perda de B em relação a A: {biggest_loss[1]:.3f}s perto de {biggest_loss[0]:.0f}m."
        )

        fuel_a_used = self._fuel_used(self.series_a)
        fuel_b_used = self._fuel_used(self.series_b)
        if fuel_a_used is not None and fuel_b_used is not None:
            text += f" Combustível consumido — A: {fuel_a_used:.2f}L, B: {fuel_b_used:.2f}L."

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
        all_visible = self.all_charts + self._tire_temp_charts + self._suspension_charts + self._slip_charts
        for chart in all_visible:
            if chart.isVisible():
                chart.show_crosshair(distance_m)
        self._update_readout(distance_m)
        if not self._use_time_axis:
            self._update_track_map_markers(distance_m)

    def _on_hover_leave(self):
        all_visible = self.all_charts + self._tire_temp_charts + self._suspension_charts + self._slip_charts
        for chart in all_visible:
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
        set_row("rpm",
                self.series_a.value_at(distance_m, "rpm"),
                self.series_b.value_at(distance_m, "rpm") if self.series_b else None,
                fmt="{:.0f}")
        set_row("gear",
                self.series_a.value_at(distance_m, "gear"),
                self.series_b.value_at(distance_m, "gear") if self.series_b else None,
                fmt="{:.0f}")
        set_row("throttle",
                self.series_a.value_at(distance_m, "throttle"),
                self.series_b.value_at(distance_m, "throttle") if self.series_b else None,
                fmt="{:.1f}", unit="%")
        set_row("brake",
                self.series_a.value_at(distance_m, "brake"),
                self.series_b.value_at(distance_m, "brake") if self.series_b else None,
                fmt="{:.1f}", unit="%")
        set_row("fuel_level",
                self.series_a.value_at(distance_m, "fuel_level"),
                self.series_b.value_at(distance_m, "fuel_level") if self.series_b else None,
                fmt="{:.2f}", unit=" L")

        tire_a = self._tire_avg_at(self.series_a, distance_m)
        tire_b = self._tire_avg_at(self.series_b, distance_m) if self.series_b else None
        set_row("tires", tire_a, tire_b, fmt="{:.1f}", unit="°C")
        set_row("g_lateral",
                self.series_a.value_at(distance_m, "g_lateral"),
                self.series_b.value_at(distance_m, "g_lateral") if self.series_b else None,
                fmt="{:.2f}", unit=" G")
        set_row("g_longitudinal",
                self.series_a.value_at(distance_m, "g_longitudinal"),
                self.series_b.value_at(distance_m, "g_longitudinal") if self.series_b else None,
                fmt="{:.2f}", unit=" G")

        slip_a = self._avg_slip_angle_at(self.series_a, distance_m)
        slip_b = self._avg_slip_angle_at(self.series_b, distance_m) if self.series_b else None
        set_row("slip_angle", slip_a, slip_b, fmt="{:.1f}", unit="°")

        set_row("turbo_boost",
                self.series_a.value_at(distance_m, "turbo_boost"),
                self.series_b.value_at(distance_m, "turbo_boost") if self.series_b else None,
                fmt="{:.2f}", unit=" bar")
        set_row("oil_temp",
                self.series_a.value_at(distance_m, "oil_temp"),
                self.series_b.value_at(distance_m, "oil_temp") if self.series_b else None,
                fmt="{:.1f}", unit="°C")
        set_row("water_temp",
                self.series_a.value_at(distance_m, "water_temp"),
                self.series_b.value_at(distance_m, "water_temp") if self.series_b else None,
                fmt="{:.1f}", unit="°C")

    @staticmethod
    def _tire_avg_at(series: LapSeries, distance_m: float):
        values = [
            series.value_at(distance_m, ch)
            for ch in ("tire_temp_fl", "tire_temp_fr", "tire_temp_rl", "tire_temp_rr")
        ]
        if any(v is None for v in values):
            return None
        return sum(values) / len(values)

    @staticmethod
    def _avg_slip_angle_at(series: LapSeries, distance_m: float):
        values = [
            series.value_at(distance_m, ch)
            for ch in ("tire_slip_fl", "tire_slip_fr", "tire_slip_rl", "tire_slip_rr")
        ]
        if any(v is None for v in values):
            return None
        return sum(_estimate_slip_angle_deg(v) for v in values) / len(values)
