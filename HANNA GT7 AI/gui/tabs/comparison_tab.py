"""
Aba "Comparação" — compara duas voltas com múltiplos gráficos sincronizados
pelo mesmo eixo de distância (velocidade, marcha, delta, freio, acelerador,
temperatura de pneus, combustível). Passar o mouse sobre qualquer gráfico
mostra a mesma posição em todos os outros, e um painel no topo com os
valores exatos de cada canal para as duas voltas naquele ponto.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QScrollArea, QGridLayout, QFrame
)
from PySide6.QtCore import Qt

from analysis import lap_storage
from analysis.telemetry_series import LapSeries, compute_delta_series
from gui.widgets import format_ms
from gui.widgets_chart import SyncedMiniChart

COLOR_A = "#4f7cff"
COLOR_B = "#ff9f4f"
COLOR_DELTA = "#f2c94c"


class ComparisonTab(QWidget):
    def __init__(self, track_id):
        super().__init__()
        self.track_id = track_id
        self.series_a: LapSeries | None = None
        self.series_b: LapSeries | None = None
        self.lap_id_a = None
        self.lap_id_b = None

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 16, 4, 4)
        root.setSpacing(10)

        root.addLayout(self._build_controls())
        self.summary_label = QLabel("Escolha duas voltas e clique em Comparar.")
        self.summary_label.setStyleSheet("color: #8a8e99; font-size: 12px;")
        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)

        root.addWidget(self._build_readout_panel())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        charts_container = QWidget()
        charts_layout = QVBoxLayout(charts_container)
        charts_layout.setSpacing(10)
        charts_layout.setContentsMargins(0, 0, 0, 0)

        self.chart_speed = SyncedMiniChart("Velocidade (km/h)")
        self.chart_delta = SyncedMiniChart("Delta acumulado (s) — abaixo de 0 = Volta B mais rápida")
        self.chart_gear = SyncedMiniChart("Marcha")
        self.chart_throttle = SyncedMiniChart("Acelerador (%)")
        self.chart_brake = SyncedMiniChart("Freio (%)")
        self.chart_tires = SyncedMiniChart("Temperatura média dos pneus (°C)")
        self.chart_fuel = SyncedMiniChart("Combustível (L)")

        self.all_charts = [
            self.chart_speed, self.chart_delta, self.chart_gear,
            self.chart_throttle, self.chart_brake, self.chart_tires, self.chart_fuel,
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

        label_a = QLabel("Volta A:")
        self.combo_a = QComboBox()
        self.combo_a.setStyleSheet(f"QComboBox {{ border-left: 3px solid {COLOR_A}; padding-left: 6px; }}")

        label_b = QLabel("Volta B:")
        self.combo_b = QComboBox()
        self.combo_b.setStyleSheet(f"QComboBox {{ border-left: 3px solid {COLOR_B}; padding-left: 6px; }}")

        refresh_button = QPushButton("Atualizar lista")
        refresh_button.clicked.connect(self.refresh_lap_list)

        compare_button = QPushButton("Comparar")
        compare_button.clicked.connect(self.plot_comparison)

        controls.addWidget(label_a)
        controls.addWidget(self.combo_a)
        controls.addSpacing(16)
        controls.addWidget(label_b)
        controls.addWidget(self.combo_b)
        controls.addStretch()
        controls.addWidget(refresh_button)
        controls.addWidget(compare_button)
        return controls

    def _build_readout_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("card")
        grid = QGridLayout(frame)
        grid.setContentsMargins(16, 10, 16, 10)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(4)

        headers = ["", "Volta A", "Volta B", "Diferença"]
        for col, text in enumerate(headers):
            lbl = QLabel(text)
            lbl.setStyleSheet("color: #8a8e99; font-size: 11px; font-weight: 600;")
            grid.addWidget(lbl, 0, col)

        self.readout_rows = {}
        channels = [
            ("distance", "Distância"),
            ("speed_kmh", "Velocidade"),
            ("gear", "Marcha"),
            ("throttle", "Acelerador"),
            ("brake", "Freio"),
            ("tires", "Temp. pneus"),
            ("fuel_level", "Combustível"),
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

    # ---------- carregar voltas ----------

    def set_track(self, track_id: int):
        self.track_id = track_id
        self.refresh_lap_list()

    def refresh_lap_list(self):
        if self.track_id is None:
            self.combo_a.clear()
            self.combo_b.clear()
            self.summary_label.setText("Conecte-se a uma pista para comparar voltas.")
            return

        laps = lap_storage.list_laps(self.track_id)

        current_a = self.combo_a.currentData()
        current_b = self.combo_b.currentData()

        self.combo_a.clear()
        self.combo_b.clear()

        for lap_id, lap_time_ms, recorded_at in laps:
            label = f"#{lap_id} — {format_ms(lap_time_ms)}"
            self.combo_a.addItem(label, lap_id)
            self.combo_b.addItem(label, lap_id)

        self._select_by_data(self.combo_a, current_a)
        self._select_by_data(self.combo_b, current_b)

        if len(laps) >= 2 and current_a is None:
            self.combo_a.setCurrentIndex(0)
            self.combo_b.setCurrentIndex(1)

    @staticmethod
    def _select_by_data(combo: QComboBox, data):
        if data is None:
            return
        index = combo.findData(data)
        if index >= 0:
            combo.setCurrentIndex(index)

    # ---------- plotagem ----------

    def plot_comparison(self):
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
        self.chart_fuel.set_series([
            ("Volta A", COLOR_A, self.series_a.points("fuel_level")),
            ("Volta B", COLOR_B, self.series_b.points("fuel_level")),
        ])

        tires_a = self._average_tire_points(self.series_a)
        tires_b = self._average_tire_points(self.series_b)
        self.chart_tires.set_series([
            ("Volta A", COLOR_A, tires_a),
            ("Volta B", COLOR_B, tires_b),
        ])

        delta_points = compute_delta_series(self.series_a, self.series_b)
        self.chart_delta.set_series([("Delta", COLOR_DELTA, delta_points)])

        self.readout_frame.setVisible(True)
        self._build_summary(delta_points, frames_a, frames_b)

        # Linhas de setor: dividimos a volta de referência (A) em 3 partes
        # iguais por distância (mesma lógica usada no Histórico), e marcamos
        # onde o setor 2 e o setor 3 começam — no estilo da imagem de
        # referência (linhas verticais tracejadas). Diferente de um app com
        # mapa oficial da pista, não temos onde ficam as CURVAS específicas
        # (o GT7 não expõe isso via telemetria), só a divisão por setor.
        if self.series_a and self.series_a.max_distance > 0:
            total = self.series_a.max_distance
            sector_defs = [(total / 3, "S2"), (total * 2 / 3, "S3")]
        else:
            sector_defs = []
        for chart in self.all_charts:
            chart.set_sector_lines(sector_defs)

    @staticmethod
    def _to_binary_step(points, threshold: float = 5.0):
        """Converte uma série contínua (0-100%) em uma série binária em
        formato degrau (0 ou 1), no estilo 'ON/OFF' de ferramentas
        profissionais de telemetria (como a imagem de referência do freio)."""
        stepped = []
        prev_state = None
        for x, v in points:
            state = 1 if v > threshold else 0
            if prev_state is not None and state != prev_state:
                # ponto extra na mesma distância, com o valor ANTERIOR,
                # para criar a transição vertical do degrau
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
        return [
            (fl[i][0], (fl[i][1] + fr[i][1] + rl[i][1] + rr[i][1]) / 4)
            for i in range(len(fl))
        ]

    def _build_summary(self, delta_points, frames_a, frames_b):
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

        fuel_a_used = frames_a[0][7] - frames_a[-1][7]
        fuel_b_used = frames_b[0][7] - frames_b[-1][7]

        self.summary_label.setText(
            f"Volta {faster} foi {diff_abs:.3f}s mais rápida no trecho comparado. "
            f"Maior ganho de B em relação a A: {abs(biggest_gain[1]):.2f}s perto de {biggest_gain[0]:.0f}m. "
            f"Maior perda de B em relação a A: {biggest_loss[1]:.2f}s perto de {biggest_loss[0]:.0f}m. "
            f"Combustível consumido — A: {fuel_a_used:.1f}L, B: {fuel_b_used:.1f}L."
        )

    # ---------- cursor sincronizado ----------

    def _on_hover(self, distance_m: float):
        for chart in self.all_charts:
            chart.show_crosshair(distance_m)
        self._update_readout(distance_m)

    def _on_hover_leave(self):
        for chart in self.all_charts:
            chart.hide_crosshair()

    def _update_readout(self, distance_m: float):
        if self.series_a is None or self.series_b is None:
            return

        def set_row(key, val_a, val_b, fmt="{:.1f}", unit=""):
            widgets = self.readout_rows.get(key)
            if not widgets:
                return
            wa, wb, wd = widgets
            if val_a is None or val_b is None:
                wa.setText("--")
                wb.setText("--")
                wd.setText("--")
                return
            wa.setText(fmt.format(val_a) + unit)
            wb.setText(fmt.format(val_b) + unit)
            diff = val_b - val_a
            wd.setText(("+" if diff >= 0 else "") + fmt.format(diff) + unit)

        wa, wb, wd = self.readout_rows["distance"]
        wa.setText(f"{distance_m:.0f}m")
        wb.setText(f"{distance_m:.0f}m")
        wd.setText("")

        set_row("speed_kmh",
                 self.series_a.value_at(distance_m, "speed_kmh"),
                 self.series_b.value_at(distance_m, "speed_kmh"), unit=" km/h")
        set_row("gear",
                 self.series_a.value_at(distance_m, "gear"),
                 self.series_b.value_at(distance_m, "gear"), fmt="{:.0f}")
        set_row("throttle",
                 self.series_a.value_at(distance_m, "throttle"),
                 self.series_b.value_at(distance_m, "throttle"), unit="%")
        set_row("brake",
                 self.series_a.value_at(distance_m, "brake"),
                 self.series_b.value_at(distance_m, "brake"), unit="%")
        set_row("fuel_level",
                 self.series_a.value_at(distance_m, "fuel_level"),
                 self.series_b.value_at(distance_m, "fuel_level"), unit="L")

        tire_a = self._tire_avg_at(self.series_a, distance_m)
        tire_b = self._tire_avg_at(self.series_b, distance_m)
        set_row("tires", tire_a, tire_b, unit="°C")

    @staticmethod
    def _tire_avg_at(series: LapSeries, distance_m: float):
        values = [
            series.value_at(distance_m, ch)
            for ch in ("tire_temp_fl", "tire_temp_fr", "tire_temp_rl", "tire_temp_rr")
        ]
        if any(v is None for v in values):
            return None
        return sum(values) / len(values)
