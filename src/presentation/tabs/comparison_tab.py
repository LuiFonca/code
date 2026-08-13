"""
Aba "Comparação" — duas voltas lado a lado.

View pura: recebe um `ComparisonResult` já calculado (delta, setores, volta
teórica ideal) e apenas desenha.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...application.viewmodels.comparison_viewmodel import (
    ComparisonResult,
    ComparisonViewModel,
)
from ..widgets.widgets import format_ms
from ..widgets.widgets_chart import SyncedMiniChart, TrackMapWidget
from .chart_tab_base import ChartTabBase

COLOR_A = "#4f7cff"
COLOR_B = "#ff9f4f"
COLOR_DELTA = "#f2c94c"


class ComparisonTab(ChartTabBase):
    def __init__(self, view_model: ComparisonViewModel):
        super().__init__()
        self._vm = view_model
        self._build_ui()

        self._vm.laps_available.connect(self._on_laps_available)
        self._vm.comparison_ready.connect(self._render)
        self._vm.error.connect(self._on_error)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        outer.addLayout(self._build_controls())

        self._message = QLabel("Selecione duas voltas para comparar.")
        self._message.setAlignment(Qt.AlignCenter)
        self._message.setStyleSheet("color: #6b6f7a; font-size: 14px;")
        outer.addWidget(self._message)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self._root = QVBoxLayout(content)
        self._root.setContentsMargins(4, 4, 4, 4)
        self._root.setSpacing(8)

        # O delta vem primeiro de propósito: é a leitura que responde "onde eu
        # ganhei ou perdi tempo", que é a pergunta da tela.
        self.chart_delta = self.add_chart("Delta (s) — positivo = B mais lento", 150)
        self.chart_speed = self.add_chart("Velocidade (km/h)")
        self.chart_throttle = self.add_chart("Acelerador (%)")
        self.chart_brake = self.add_chart("Freio (%)")
        self.chart_gear = self.add_chart("Marcha")
        self.chart_rpm = self.add_chart("RPM")

        self._root.addWidget(self.section_header("SETORES"))
        self._sector_grid = QGridLayout()
        sector_frame = QFrame()
        sector_frame.setObjectName("card")
        sector_frame.setLayout(self._sector_grid)
        self._root.addWidget(sector_frame)

        self._root.addWidget(self.section_header("TRAÇADO"))
        self.track_map = TrackMapWidget("Traçado das duas voltas", height=260)
        self._root.addWidget(self.track_map)

        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)

        self._summary = QLabel("")
        self._summary.setObjectName("sectionHeader")
        outer.addWidget(self._summary)

    def _build_controls(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(4, 4, 4, 0)
        row.setSpacing(10)

        label_a = QLabel("Volta A:")
        label_a.setObjectName("sectionHeader")
        label_a.setStyleSheet(f"color: {COLOR_A}; font-weight: 700;")
        self._combo_a = QComboBox()
        self._combo_a.setMinimumWidth(220)

        label_b = QLabel("Volta B:")
        label_b.setObjectName("sectionHeader")
        label_b.setStyleSheet(f"color: {COLOR_B}; font-weight: 700;")
        self._combo_b = QComboBox()
        self._combo_b.setMinimumWidth(220)

        self._compare_button = QPushButton("Comparar")
        self._compare_button.clicked.connect(self._on_compare_clicked)

        row.addWidget(label_a)
        row.addWidget(self._combo_a)
        row.addWidget(label_b)
        row.addWidget(self._combo_b)
        row.addWidget(self._compare_button)
        row.addStretch()
        return row

    # ---------- reação ao ViewModel ----------

    def _on_laps_available(self, laps: list):
        for combo in (self._combo_a, self._combo_b):
            combo.clear()
            for lap in laps:
                combo.addItem(f"Volta {lap.id} — {format_ms(lap.lap_time_ms)}", lap.id)

        # Pré-seleção útil: a melhor volta contra a mais recente, que é a
        # comparação que o piloto quer ver logo depois de sair da pista.
        if len(laps) >= 2:
            best = min(laps, key=lambda l: l.lap_time_ms or 10**9)
            self._combo_a.setCurrentIndex(
                next(i for i, l in enumerate(laps) if l.id == best.id)
            )
            self._combo_b.setCurrentIndex(0 if laps[0].id != best.id else 1)
        self._compare_button.setEnabled(len(laps) >= 2)

    def _on_compare_clicked(self):
        a, b = self._combo_a.currentData(), self._combo_b.currentData()
        if a is not None and b is not None:
            self._vm.compare(a, b)

    def _on_error(self, message: str):
        self._message.setText(message)
        self._message.setVisible(True)

    def _render(self, result: ComparisonResult):
        if not result.is_valid:
            return
        self._message.setVisible(False)
        sa, sb = result.series_a, result.series_b

        self.chart_delta.set_series([("Delta", COLOR_DELTA, result.delta_points)])
        for chart, channel in (
            (self.chart_speed, "speed_kmh"),
            (self.chart_throttle, "throttle"),
            (self.chart_brake, "brake"),
            (self.chart_gear, "gear"),
            (self.chart_rpm, "rpm"),
        ):
            # Séries vêm do ViewModel já reamostradas e cacheadas — montar a
            # série crua a cada gráfico é o que fazia esta tela levar ~800 ms.
            chart.set_series(
                [
                    ("A", COLOR_A, self._vm.points_for("A", channel)),
                    ("B", COLOR_B, self._vm.points_for("B", channel)),
                ]
            )

        self.apply_sector_lines(result.sector_boundaries)

        self._render_sectors(result)
        self._render_summary(result)

        self.track_map.clear()
        paths = []
        if sa.position_points():
            paths.append(("A", COLOR_A, sa.position_points()))
        if sb.position_points():
            paths.append(("B", COLOR_B, sb.position_points()))
        if paths:
            self.track_map.set_paths(paths)

    def _render_sectors(self, result: ComparisonResult):
        while self._sector_grid.count():
            item = self._sector_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        headers = ["Setor", "Volta A", "Volta B", "Diferença"]
        for c, text in enumerate(headers):
            label = QLabel(text)
            label.setObjectName("sectionHeader")
            self._sector_grid.addWidget(label, 0, c)

        for r, sector in enumerate(result.sectors, start=1):
            self._sector_grid.addWidget(QLabel(f"Setor {sector.index + 1}"), r, 0)

            for col, (time_ms, side) in enumerate(
                ((sector.time_a, "A"), (sector.time_b, "B")), start=1
            ):
                label = QLabel(format_ms(time_ms) if time_ms else "--")
                if sector.winner == side:
                    label.setStyleSheet("color: #3ddc84; font-weight: 700;")
                self._sector_grid.addWidget(label, r, col)

            delta = sector.delta_ms
            if delta is None:
                text, color = "--", "#6b6f7a"
            else:
                text = f"{delta / 1000:+.3f}s"
                color = "#ff5c5c" if delta > 0 else "#3ddc84"
            delta_label = QLabel(text)
            delta_label.setStyleSheet(f"color: {color}; font-weight: 700;")
            self._sector_grid.addWidget(delta_label, r, 3)

    def _render_summary(self, result: ComparisonResult):
        a, b = result.lap_a, result.lap_b
        diff = (b.lap_time_ms - a.lap_time_ms) / 1000
        parts = [
            f"A: {format_ms(a.lap_time_ms)}",
            f"B: {format_ms(b.lap_time_ms)}",
            f"Diferença: {diff:+.3f}s",
        ]
        if result.theoretical_best_ms:
            parts.append(f"Volta ideal combinada: {format_ms(result.theoretical_best_ms)}")
        self._summary.setText("   |   ".join(parts))

