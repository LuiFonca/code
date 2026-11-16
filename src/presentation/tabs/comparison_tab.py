"""
Aba "Comparação" — duas voltas lado a lado.

View pura: recebe um `ComparisonResult` já calculado (delta, setores, volta
teórica ideal) e apenas desenha.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
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
from ..widgets.widgets_chart import TrackMapWidget
from .chart_tab_base import ChartTabBase

COLOR_A = "#4f7cff"
COLOR_B = "#ff9f4f"
COLOR_DELTA = "#f2c94c"
# Voltas opcionais além de A e B, na mesma ordem dos seletores.
EXTRA_COLORS = {"C": "#3ddc84", "D": "#e06cff"}

CORNER_COLUMNS = [
    "Curva", "Lado", "Tempo na curva (B − A)", "Vel. mínima (B − A)",
    "Frenagem (B − A)",
]
COLOR_GAIN = (61, 220, 132)
COLOR_LOSS = (255, 92, 92)
COLOR_EVEN = (107, 111, 122)


def gain_color(valor_ms: float, maximo_ms: float) -> str:
    """Cor de um trecho do mapa: verde onde B ganha, vermelho onde perde.

    A intensidade acompanha o tamanho da diferença em relação à maior da
    volta. Com escala fixa em milissegundos, uma comparação apertada sairia
    toda cinza e uma folgada toda saturada — nos dois casos sem informação.
    """
    if maximo_ms <= 0:
        return "#%02x%02x%02x" % COLOR_EVEN
    peso = min(abs(valor_ms) / maximo_ms, 1.0)
    alvo = COLOR_LOSS if valor_ms > 0 else COLOR_GAIN
    cor = tuple(int(n + (a - n) * peso) for n, a in zip(COLOR_EVEN, alvo))
    return "#%02x%02x%02x" % cor


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

        self._root.addWidget(self.section_header("CURVA A CURVA  (B contra A)"))
        nota = QLabel(
            "Positivo no tempo é B mais lenta naquela curva. A frenagem positiva "
            "significa que B freou mais tarde."
        )
        nota.setStyleSheet("color: #8b93a7; font-size: 11px;")
        self._root.addWidget(nota)
        self.corners_table = QTableWidget(0, len(CORNER_COLUMNS))
        self.corners_table.setHorizontalHeaderLabels(CORNER_COLUMNS)
        self.corners_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.corners_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.corners_table.verticalHeader().setVisible(False)
        self.corners_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.corners_table.setMinimumHeight(170)
        self._root.addWidget(self.corners_table)

        self._root.addWidget(self.section_header("ONDE B GANHA E PERDE"))
        self.gain_map = TrackMapWidget(
            "Verde: B mais rápida no trecho — vermelho: mais lenta", height=260
        )
        self._root.addWidget(self.gain_map)

        self._root.addWidget(self.section_header("TRAÇADO"))
        self.track_map = TrackMapWidget("Traçado das voltas", height=260)
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

        # Voltas opcionais. Começam em "—": a comparação de duas voltas segue
        # sendo o caso normal, e quem quer mais escolhe.
        self._extra_combos = {}
        for lado, cor in EXTRA_COLORS.items():
            rotulo = QLabel(f"{lado}:")
            rotulo.setStyleSheet(f"color: {cor}; font-weight: 700;")
            combo = QComboBox()
            combo.setMinimumWidth(150)
            combo.setToolTip(
                "Volta adicional (opcional). Entra nos gráficos e no delta, "
                "sempre medida contra a volta A."
            )
            self._extra_combos[lado] = combo
            row.addWidget(rotulo)
            row.addWidget(combo)
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
        for combo in self._extra_combos.values():
            combo.clear()
            combo.addItem("—", None)
            for lap in laps:
                combo.addItem(f"Volta {lap.id} — {format_ms(lap.lap_time_ms)}", lap.id)
        self._compare_button.setEnabled(len(laps) >= 2)

    def _on_compare_clicked(self):
        a, b = self._combo_a.currentData(), self._combo_b.currentData()
        if a is not None and b is not None:
            extras = [c.currentData() for c in self._extra_combos.values()]
            self._vm.compare(a, b, [e for e in extras if e is not None])

    def _on_error(self, message: str):
        self._message.setText(message)
        self._message.setVisible(True)

    def _render(self, result: ComparisonResult):
        if not result.is_valid:
            return
        self._message.setVisible(False)
        sa, sb = result.series_a, result.series_b

        # Lados extras efetivamente carregados, com a cor de cada um.
        extras = list(zip(EXTRA_COLORS.items(), result.extra_deltas))

        self.chart_delta.set_series(
            [("B − A", COLOR_DELTA, result.delta_points)]
            + [(f"{lado} − A", cor, delta) for (lado, cor), delta in extras]
        )
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
                + [
                    (lado, cor, self._vm.points_for(lado, channel))
                    for (lado, cor), _delta in extras
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
        for (lado, cor), serie in zip(EXTRA_COLORS.items(), result.extra_series):
            if serie.position_points():
                paths.append((lado, cor, serie.position_points()))
        if paths:
            self.track_map.set_paths(paths)

        self._render_corners(result)
        self._render_gain_map(result)

    def _render_corners(self, result: ComparisonResult):
        """Tabela de curvas: onde B ganhou ou perdeu, e por quê."""
        deltas = result.corner_deltas
        self.corners_table.setRowCount(len(deltas))
        for linha, d in enumerate(deltas):
            tempo = d.time_delta_ms
            celulas = [
                (str(d.corner.index), None),
                (d.corner.direction, None),
                ("—" if tempo is None else f"{tempo / 1000:+.3f} s", tempo),
                (
                    "—" if d.min_speed_delta_kmh is None
                    else f"{d.min_speed_delta_kmh:+.1f} km/h",
                    # Velocidade maior é ganho: sinal invertido para a cor.
                    None if d.min_speed_delta_kmh is None else -d.min_speed_delta_kmh,
                ),
                (
                    "—" if d.brake_point_delta_m is None
                    else f"{d.brake_point_delta_m:+.0f} m",
                    None,
                ),
            ]
            for coluna, (texto, sinal) in enumerate(celulas):
                item = QTableWidgetItem(texto)
                item.setTextAlignment(Qt.AlignCenter)
                if sinal is not None and abs(sinal) > 1e-9:
                    item.setForeground(
                        QColor("#ff5c5c") if sinal > 0 else QColor("#3ddc84")
                    )
                self.corners_table.setItem(linha, coluna, item)

    def _render_gain_map(self, result: ComparisonResult):
        """Traçado colorido por onde B ganha e perde tempo contra A."""
        self.gain_map.clear()
        pedacos = result.delta_map
        if not pedacos:
            return
        maximo = max(abs(g) for _, g in pedacos)
        self.gain_map.set_paths(
            [
                (f"trecho {i}", gain_color(ganho, maximo), pontos)
                for i, (pontos, ganho) in enumerate(pedacos)
            ]
        )

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

