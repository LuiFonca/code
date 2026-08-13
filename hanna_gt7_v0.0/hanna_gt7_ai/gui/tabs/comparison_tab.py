"""
Aba "Comparação" — compara duas voltas com múltiplos gráficos sincronizados
pelo mesmo eixo de distância (velocidade, marcha, delta, freio, acelerador,
temperatura de pneus, combustível, traçado). Passar o mouse sobre qualquer
gráfico mostra a mesma posição em todos os outros, e um painel no topo com
os valores exatos de cada canal para as duas voltas naquele ponto.

Cada canal é plotado com os pontos realmente salvos daquela volta (ver
LapSeries.points) — voltas com taxas de amostragem diferentes, números
de pontos diferentes, ou colunas ausentes (voltas antigas de antes de um
canal existir no schema) não quebram os gráficos: o canal ausente
simplesmente não desenha aquela linha, em vez de travar a tela toda.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QScrollArea, QGridLayout, QFrame
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
NUM_SECTORS = 3


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
        root.addWidget(self._build_sector_panel())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        charts_container = QWidget()
        charts_layout = QVBoxLayout(charts_container)
        charts_layout.setSpacing(10)
        charts_layout.setContentsMargins(0, 0, 0, 0)

        self.track_map = TrackMapWidget("Traçado — Volta A (azul) vs Volta B (laranja)")
        charts_layout.addWidget(self.track_map)

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

    def _build_sector_panel(self) -> QWidget:
        """Comparação setor a setor (item 14): tempo de cada setor nas
        duas voltas usando os MESMOS limites de distância (ver
        telemetry_series.sector_times_from_series), então 'Setor 2' é o
        mesmo trecho físico nas duas voltas mesmo que uma delas tenha sido
        salva antes desse critério existir."""
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(6)

        title = QLabel("Comparação por setor")
        title.setStyleSheet("font-size: 12px; font-weight: 600; color: #8a8e99;")
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(4)
        headers = ["", "Volta A", "Volta B", "Diferença"]
        for col, text in enumerate(headers):
            lbl = QLabel(text)
            lbl.setStyleSheet("color: #8a8e99; font-size: 11px; font-weight: 600;")
            grid.addWidget(lbl, 0, col)

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

        for lap_id, lap_time_ms, recorded_at, car_name in laps:
            suffix = f" — {car_name}" if car_name else ""
            label = f"#{lap_id} — {format_ms(lap_time_ms)}{suffix}"
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

        # Cada canal só é desenhado se pelo menos uma das duas voltas tiver
        # dado (voltas salvas antes da v4 do schema não têm combustível,
        # pneus ou posição — ver LapSeries.has_channel). Isso evita
        # gráficos "quebrados" quando se compara uma volta antiga com uma
        # nova, em vez de simplesmente não desenhar o que falta.
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

        has_position = self.series_a.has_channel("position_x") or self.series_b.has_channel("position_x")
        self.track_map.setVisible(has_position)
        if has_position:
            self.track_map.set_paths([
                ("Volta A", COLOR_A, self.series_a.position_points()),
                ("Volta B", COLOR_B, self.series_b.position_points()),
            ])

        delta_points = compute_delta_series(self.series_a, self.series_b)
        self.chart_delta.set_series([("Delta", COLOR_DELTA, delta_points)])

        self.readout_frame.setVisible(True)
        self._build_summary(delta_points)
        self._build_sector_comparison()

        # Linhas de setor: usamos a mesma referência de distância do banco
        # (ver lap_storage._reference_lap_distance) para que "S2"/"S3"
        # caiam no mesmo trecho físico independente de qual volta está
        # sendo usada como pano de fundo dos gráficos. Diferente de um app
        # com mapa oficial da pista, não temos onde ficam as CURVAS
        # específicas (o GT7 não expõe isso via telemetria), só a divisão
        # por setor.
        reference_distance = max(self.series_a.max_distance, self.series_b.max_distance)
        bounds = sector_boundaries_m(reference_distance, NUM_SECTORS)
        sector_defs = [(d, f"S{i + 2}") for i, d in enumerate(bounds[:-1])]
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
        # Os 4 canais de pneu são sempre gravados juntos (mesmo frame), então
        # em uso normal têm o mesmo tamanho; o min() abaixo é só uma
        # salvaguarda contra um descompasso inesperado, para não estourar
        # IndexError em vez de simplesmente usar o que está disponível.
        n = min(len(fl), len(fr), len(rl), len(rr))
        return [
            (fl[i][0], (fl[i][1] + fr[i][1] + rl[i][1] + rr[i][1]) / 4)
            for i in range(n)
        ]

    @staticmethod
    def _fuel_used(series: LapSeries):
        """Combustível consumido na volta (inicial - final), ou None se a
        volta não tem essa coluna (schema antigo)."""
        points = series.points("fuel_level")
        if len(points) < 2:
            return None
        return points[0][1] - points[-1][1]

    def _build_summary(self, delta_points):
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
        """Preenche o painel de setores (item 14): tempo por setor das duas
        voltas usando limites de distância idênticos, diferença, e a soma
        do melhor setor de cada volta (volta teórica ideal)."""
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
            chart.show_crosshair(distance_m)
        self._update_readout(distance_m)
        self._update_track_map_markers(distance_m)

    def _on_hover_leave(self):
        for chart in self.all_charts:
            chart.hide_crosshair()
        self.track_map.clear_markers()

    def _update_track_map_markers(self, distance_m: float):
        if self.series_a is None or self.series_b is None:
            return
        xa, za = self.series_a.value_at(distance_m, "position_x"), self.series_a.value_at(distance_m, "position_z")
        xb, zb = self.series_b.value_at(distance_m, "position_x"), self.series_b.value_at(distance_m, "position_z")
        self.track_map.set_markers([(xa, za, COLOR_MARKER_A), (xb, zb, COLOR_MARKER_B)])

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
