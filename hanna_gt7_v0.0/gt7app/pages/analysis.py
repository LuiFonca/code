"""
Página de análise — uma volta, dissecada.

É aqui que a Fase 4 fica visível. Até agora curvas, frenagem, acelerador e
pneus só existiam no terminal, via `python3 -m gt7core.demo`; esta página é a
interface deles.

Organização: os canais empilhados à esquerda compartilham o cursor, o traçado à
direita marca os ápices, e a tabela embaixo lista as curvas com o que foi medido
em cada uma. Passar o mouse por qualquer gráfico move o cursor de todos e
destaca a curva correspondente — é a leitura que um engenheiro faz, relacionando
o que aconteceu no pedal com onde aconteceu na pista.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from gt7core.analytics.braking import BrakingZone, detect_braking_zones
from gt7core.analytics.corners import Corner, corner_at, detect_corners
from gt7core.analytics.series import sector_boundaries_m
from gt7core.analytics.throttle import ThrottleApplication, analyse_throttle
from gt7core.analytics.tyres import detect_tyre_events, temperature_balance
from gt7core.domain.models import TelemetryPoint

from ..application import CoreApplication
from ..design.tokens import Space, Theme
from ..widgets.cards import Card, MetricCard, MetricGrid, StatRow
from ..widgets.charts import DistanceChart, Series
from ..widgets.gforce import GForceDiagram
from ..widgets.selectors import TrackLapSelector, format_lap_time
from ..widgets.trackmap import TrackMap, TrackMarker, TrackPath
from .base import Page

CORNER_COLUMNS = ("Curva", "Ápice", "Vel. mín.", "Raio", "Freada", "Saída")

# Os mesmos setores do histórico — o corte precisa ser o mesmo entre telas.
NUM_SECTORS = 3


class AnalysisPage(Page):
    page_id = "analysis"
    nav_title = "Análise"
    title = "Análise de volta"
    subtitle = "Curvas, frenagem, acelerador e pneus"

    def __init__(self, core: CoreApplication, theme: Theme) -> None:
        self._points: list[TelemetryPoint] = []
        self._corners: list[Corner] = []
        super().__init__(core, theme)

    # ---------- construção ----------

    def build(self) -> None:
        self._selector = TrackLapSelector(self.core.tracks, self.core.laps)
        self._selector.lap_changed.connect(self._on_lap_selected)
        self.header.add_action(self._selector)

        self._summary = MetricGrid(columns=5)
        for key, label, unit in (
            ("time", "Tempo", ""),
            ("corners", "Curvas", ""),
            ("top", "Vel. máxima", "km/h"),
            ("braking", "Freada máx.", "g"),
            ("events", "Perdas aderência", ""),
        ):
            self._summary.add_card(key, MetricCard(label, unit))
        self.content.addWidget(self._summary)

        middle = QHBoxLayout()
        middle.setSpacing(Space.LG.px)

        channels = Card("Canais por distância")
        self._charts = [
            DistanceChart(self.theme, "Velocidade", unit="km/h", height=130),
            DistanceChart(
                self.theme, "Pedais", unit="%", height=110, y_range=(0.0, 105.0)
            ),
        ]
        for chart in self._charts:
            channels.add(chart)
            chart.hovered.connect(self._on_hover)
            chart.hover_left.connect(self._on_hover_left)
        # O círculo mora na coluna larga, abaixo dos canais. Sair de dois
        # gráficos para três deixou um vazio grande aqui, e o envelope de
        # aderência é justamente o gráfico que precisa de área: espremido, a
        # nuvem vira um borrão e a forma — que é a informação inteira — some.
        grip_card = Card("Círculo de atrito")
        self._gforce = GForceDiagram(self.theme, height=300)
        grip_card.add(self._gforce)

        left = QVBoxLayout()
        left.setSpacing(Space.LG.px)
        left.addWidget(channels)
        left.addWidget(grip_card)
        left.addStretch(1)
        middle.addLayout(left, stretch=3)

        right = QVBoxLayout()
        right.setSpacing(Space.LG.px)

        map_card = Card("Traçado — cor por velocidade")
        self._map = TrackMap(self.theme, height=240, heatmap_label="km/h")
        # A ligação nos dois sentidos é o que faz o mapa e os gráficos serem uma
        # leitura só: os gráficos dizem *o que* aconteceu, o mapa diz *onde*.
        self._map.hovered.connect(self._on_hover)
        self._map.hover_left.connect(self._on_hover_left)
        map_card.add(self._map)
        right.addWidget(map_card)

        self._detail = Card("No cursor")
        self._detail_rows = {
            key: StatRow(label)
            for key, label in (
                ("distance", "Distância"),
                ("speed", "Velocidade"),
                ("throttle", "Acelerador"),
                ("brake", "Freio"),
                ("gear", "Marcha"),
                ("corner", "Curva"),
            )
        }
        for row in self._detail_rows.values():
            self._detail.add(row)
        right.addWidget(self._detail)
        right.addStretch(1)

        middle.addLayout(right, stretch=2)
        self.content.addLayout(middle)

        table_card = Card("Curvas")
        self._table = QTableWidget(0, len(CORNER_COLUMNS))
        self._table.setHorizontalHeaderLabels(CORNER_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._table.setMinimumHeight(180)
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        table_card.add(self._table)
        self.content.addWidget(table_card)

        self._tyres = QLabel("")
        self._tyres.setWordWrap(True)
        self.content.addWidget(self._tyres)

    # ---------- dados ----------

    def refresh(self) -> None:
        self._selector.reload()

    def _on_lap_selected(self, lap_id: object) -> None:
        # O sinal do seletor carrega `object` porque um `Signal(int)` do Qt não
        # transporta None — e "nenhuma volta escolhida" é um estado legítimo.
        if not isinstance(lap_id, int):
            self._clear()
            return

        lap = self.core.laps.get_by_id(lap_id)
        if lap is None:
            self._clear()
            return

        self._points = self.core.laps.load_points(lap_id)
        if len(self._points) < 2:
            self._clear()
            self.header.set_subtitle("volta sem amostras gravadas")
            return

        self._corners = detect_corners(self._points)
        self._populate(lap.lap_time_ms)

    def _clear(self) -> None:
        self._points = []
        self._corners = []
        self._summary.clear_values(self.theme)
        for chart in self._charts:
            chart.clear()
        self._map.clear()
        self._table.setRowCount(0)
        self._tyres.setText("")

    def _populate(self, lap_time_ms: int) -> None:
        palette = self.theme.palette
        points = self._points

        zones = detect_braking_zones(points)
        applications = analyse_throttle(points, self._corners)
        events = detect_tyre_events(points)

        cards = self._summary.cards
        cards["time"].set_value(format_lap_time(lap_time_ms))
        cards["corners"].set_value(str(len(self._corners)))
        cards["top"].set_value(f"{max(p.speed_kmh for p in points):.0f}")
        cards["braking"].set_value(
            f"{max((z.average_deceleration_g for z in zones), default=0.0):.2f}"
        )
        cards["events"].set_value(
            str(len(events)),
            palette.yellow if events else palette.green,
        )

        self._charts[0].set_series(
            [
                Series(
                    "vel",
                    palette.channel_speed,
                    [(p.distance_m, p.speed_kmh) for p in points],
                )
            ]
        )
        self._charts[1].set_series(
            [
                Series(
                    "acel",
                    palette.channel_throttle,
                    [(p.distance_m, p.throttle) for p in points],
                ),
                Series(
                    "freio",
                    palette.channel_brake,
                    [(p.distance_m, p.brake) for p in points],
                ),
            ]
        )
        # A volta inteira vira nuvem no círculo de atrito. G por distância
        # respondia "quanto de G houve no metro 1.200", que não é pergunta que
        # alguém faça; o envelope bidimensional mostra como a aderência
        # disponível foi repartida entre frear, acelerar e curvar.
        self._gforce.set_points(
            [(p.g_lateral, p.g_longitudinal) for p in points]
        )
        self._gforce.set_current(None)

        apex_marks = [
            (corner.apex_distance_m, f"C{corner.index}", palette.text_muted)
            for corner in self._corners
        ]
        for chart in self._charts:
            chart.set_markers(apex_marks)

        self._map.set_paths(
            [
                TrackPath(
                    "traçado",
                    palette.accent,
                    [(p.position_x, p.position_z) for p in points],
                    values=[p.speed_kmh for p in points],
                    distances=[p.distance_m for p in points],
                )
            ]
        )

        markers = [
            TrackMarker(
                x=apex.position_x,
                z=apex.position_z,
                color=palette.purple,
                label=f"C{corner.index}",
                hollow=True,
            )
            for corner in self._corners
            if (apex := _point_at(points, corner.apex_distance_m)) is not None
        ]
        # Limites de setor: os mesmos cortes por distância que o histórico usa,
        # para que "setor 2" signifique o mesmo pedaço de asfalto nas duas telas.
        for number, boundary in enumerate(
            sector_boundaries_m(points[-1].distance_m, NUM_SECTORS)[:-1], start=1
        ):
            edge = _point_at(points, boundary)
            if edge is not None:
                markers.append(
                    TrackMarker(
                        x=edge.position_x,
                        z=edge.position_z,
                        color=palette.text_muted,
                        label=f"S{number}",
                        radius=3.0,
                    )
                )
        self._map.set_markers(markers)

        self._fill_table(zones, applications)

        balance = temperature_balance(points)
        self._tyres.setText(f"Pneus: {balance.describe()}" if balance else "")

    def _fill_table(
        self, zones: list[BrakingZone], applications: list[ThrottleApplication]
    ) -> None:
        by_corner = {a.corner_index: a for a in applications}
        self._table.setRowCount(len(self._corners))

        for row, corner in enumerate(self._corners):
            zone = min(
                (z for z in zones if z.start_distance_m <= corner.apex_distance_m),
                key=lambda z: corner.apex_distance_m - z.start_distance_m,
                default=None,
            )
            application = by_corner.get(corner.index)

            values = [
                f"{corner.index}  ({corner.severity})",
                f"{corner.apex_distance_m:.0f} m",
                f"{corner.minimum_speed_kmh:.1f} km/h",
                f"{corner.radius_m:.0f} m" if corner.radius_m else "—",
                f"{zone.average_deceleration_g:.2f} g" if zone else "—",
                application.describe() if application else "—",
            ]
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )
                self._table.setItem(row, column, item)

    # ---------- interação ----------

    def _on_hover(self, distance_m: float) -> None:
        for chart in self._charts:
            chart.set_cursor(distance_m)
        self._map.set_cursor(distance_m)

        point = _point_at(self._points, distance_m)
        if point is None:
            return

        rows = self._detail_rows
        rows["distance"].set_value(f"{point.distance_m:.0f} m")
        rows["speed"].set_value(f"{point.speed_kmh:.1f} km/h")
        rows["throttle"].set_value(f"{point.throttle:.0f} %")
        rows["brake"].set_value(f"{point.brake:.0f} %")
        rows["gear"].set_value(str(point.gear) if point.gear > 0 else "N")
        # A bola liga o ponto da pista sob o cursor ao lugar dele no envelope
        # de aderência — é o que transforma a nuvem em leitura, em vez de
        # decoração.
        self._gforce.set_current((point.g_lateral, point.g_longitudinal))

        corner = corner_at(self._corners, distance_m)
        rows["corner"].set_value(
            f"{corner.index} ({corner.severity})" if corner else "—"
        )

    def _on_hover_left(self) -> None:
        for chart in self._charts:
            chart.set_cursor(None)
        self._map.set_cursor(None)
        self._gforce.set_current(None)

    def _on_row_selected(self) -> None:
        """Selecionar uma curva na tabela move o cursor dos gráficos até ela."""
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        index = rows[0].row()
        if 0 <= index < len(self._corners):
            self._on_hover(self._corners[index].apex_distance_m)


def _point_at(
    points: list[TelemetryPoint], distance_m: float
) -> TelemetryPoint | None:
    """Amostra mais próxima da distância informada."""
    if not points:
        return None
    return min(points, key=lambda p: abs(p.distance_m - distance_m))
