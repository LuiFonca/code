"""
Página de comparação — duas voltas, lado a lado, e a conta de onde saiu a
diferença.

A pergunta que esta página responde é a do §20: *onde estou perdendo tempo?*
O `TimeLossReport` da Fase 4 já a responde em texto; aqui ela vira tela.

A ordenação da tabela é por perda, não por posição na pista. Um relatório
ordenado por distância obriga o piloto a ler tudo para achar o que importa;
ordenado por perda, a primeira linha já é o que treinar amanhã.
"""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from gt7core.analytics.corners import detect_corners
from gt7core.analytics.series import LapSeries, compute_delta_series
from gt7core.analytics.timeloss import TimeLossReport, analyse_time_loss
from gt7core.domain.models import TelemetryPoint

from ..application import CoreApplication
from ..design.tokens import Space, Theme
from ..widgets.advice import AdviceCard
from ..widgets.cards import Card, MetricCard, MetricGrid
from ..widgets.charts import DistanceChart, Series
from ..widgets.selectors import TrackLapSelector
from ..widgets.trackmap import TrackMap, TrackMarker, TrackPath
from .base import Page

SEGMENT_COLUMNS = ("Trecho", "Início", "Δ tempo", "Diagnóstico")


class ComparePage(Page):
    page_id = "compare"
    nav_title = "Comparar"
    title = "Comparação de voltas"
    subtitle = "Onde a diferença foi feita"

    def __init__(self, core: CoreApplication, theme: Theme) -> None:
        self._reference: list[TelemetryPoint] = []
        self._analysed: list[TelemetryPoint] = []
        super().__init__(core, theme)

    # ---------- construção ----------

    def build(self) -> None:
        selectors = QVBoxLayout()
        selectors.setSpacing(Space.XS.px)

        self._reference_selector = TrackLapSelector(
            self.core.tracks, self.core.laps, lap_label="Referência:"
        )
        self._analysed_selector = TrackLapSelector(
            self.core.tracks, self.core.laps, lap_label="Comparar:"
        )
        self._reference_selector.lap_changed.connect(self._on_selection)
        self._analysed_selector.lap_changed.connect(self._on_selection)

        selectors.addWidget(self._reference_selector)
        selectors.addWidget(self._analysed_selector)

        holder = QVBoxLayout()
        holder.addLayout(selectors)
        wrapper = Card()
        wrapper.body().addLayout(holder)
        self.header.add_action(wrapper)

        self._summary = MetricGrid(columns=4)
        for key, label, unit in (
            ("total", "Diferença total", "s"),
            ("recoverable", "Recuperável", "s"),
            ("worst", "Pior trecho", ""),
            ("segments", "Trechos perdidos", ""),
        ):
            self._summary.add_card(key, MetricCard(label, unit))
        self.content.addWidget(self._summary)

        middle = QHBoxLayout()
        middle.setSpacing(Space.LG.px)

        charts = Card("Delta e velocidade por distância")
        self._delta_chart = DistanceChart(
            self.theme, "Delta acumulado", unit="s", height=140
        )
        self._speed_chart = DistanceChart(
            self.theme, "Velocidade", unit="km/h", height=140
        )
        charts.add(self._delta_chart)
        charts.add(self._speed_chart)
        self._delta_chart.hovered.connect(self._on_hover)
        self._speed_chart.hovered.connect(self._on_hover)
        middle.addWidget(charts, stretch=3)

        map_card = Card("Traçados sobrepostos")
        self._map = TrackMap(self.theme, height=300)
        self._map.hovered.connect(self._on_hover)
        map_card.add(self._map)
        middle.addWidget(map_card, stretch=2)
        self.content.addLayout(middle)

        table_card = Card("Perda por trecho — do pior para o menor")
        self._table = QTableWidget(0, len(SEGMENT_COLUMNS))
        self._table.setHorizontalHeaderLabels(SEGMENT_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setMinimumHeight(200)
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        table_card.add(self._table)
        self.content.addWidget(table_card)

        # O engenheiro fica **abaixo** da tabela de propósito. A tabela é o
        # fato medido; o conselho é interpretação em cima dela. Pôr a
        # interpretação primeiro convidaria a ler o texto e não conferir os
        # números — que é a inversão que este projeto inteiro tenta evitar.
        self._advice = AdviceCard(self.theme)
        self.content.addWidget(self._advice)

        service = self.core.engineer_service
        if service is None or not service.is_available:
            self._advice.show_unavailable()
        else:
            service.started.connect(self._on_engineer_started)
            service.ready.connect(self._on_advice)
            service.failed.connect(self._advice.show_error)

        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self.content.addWidget(self._hint)

    # ---------- dados ----------

    def refresh(self) -> None:
        self._reference_selector.reload()
        self._analysed_selector.reload()
        self._preselect_best()

    def _preselect_best(self) -> None:
        """Referência começa na melhor volta da pista — é o padrão útil.

        Sem isso, abrir a página compararia duas voltas arbitrárias e o piloto
        teria que configurar antes de ver qualquer coisa.
        """
        track_id = self._reference_selector.current_track_id()
        if track_id is None:
            return
        best = self.core.laps.get_best(track_id)
        if best is not None and best.id is not None:
            self._reference_selector.select_lap(best.id)

    def _on_selection(self, _lap_id: object) -> None:
        reference_id = self._reference_selector.current_lap_id()
        analysed_id = self._analysed_selector.current_lap_id()

        if reference_id is None or analysed_id is None:
            self._clear("escolha duas voltas para comparar")
            return
        if reference_id == analysed_id:
            self._clear("escolha voltas diferentes")
            return

        self._reference = self.core.laps.load_points(reference_id)
        self._analysed = self.core.laps.load_points(analysed_id)

        if len(self._reference) < 2 or len(self._analysed) < 2:
            self._clear("uma das voltas não tem amostras gravadas")
            return

        self._populate()

    def _clear(self, hint: str) -> None:
        self._summary.clear_values(self.theme)
        self._delta_chart.clear()
        self._speed_chart.clear()
        self._map.clear()
        self._table.setRowCount(0)
        self._hint.setText(hint)

    def _populate(self) -> None:
        palette = self.theme.palette
        report = analyse_time_loss(self._reference, self._analysed)

        cards = self._summary.cards
        total_s = report.total_delta_ms / 1000.0
        cards["total"].set_value(f"{total_s:+.3f}", palette.delta(total_s))
        cards["recoverable"].set_value(f"{report.recoverable_ms / 1000:.3f}")

        worst = report.worst(1)
        cards["worst"].set_value(worst[0].label if worst else "—")
        cards["segments"].set_value(str(len(report.losses)))

        delta_series = compute_delta_series(
            LapSeries(self._reference), LapSeries(self._analysed)
        )
        self._delta_chart.set_series(
            [Series("delta", palette.accent, delta_series)]
        )
        self._speed_chart.set_series(
            [
                Series(
                    "referência",
                    palette.purple,
                    [(p.distance_m, p.speed_kmh) for p in self._reference],
                ),
                Series(
                    "comparada",
                    palette.channel_speed,
                    [(p.distance_m, p.speed_kmh) for p in self._analysed],
                ),
            ]
        )

        # As marcas destacam onde se perdeu, não todas as curvas: um gráfico
        # cheio de linhas pontilhadas não destaca nada.
        self._delta_chart.set_markers(
            [
                (segment.start_distance_m, segment.label, palette.yellow)
                for segment in report.worst(3)
            ]
        )

        self._map.set_paths(
            [
                TrackPath(
                    "referência",
                    palette.purple,
                    [(p.position_x, p.position_z) for p in self._reference],
                    distances=[p.distance_m for p in self._reference],
                ),
                TrackPath(
                    "comparada",
                    palette.channel_speed,
                    [(p.position_x, p.position_z) for p in self._analysed],
                    dashed=True,
                    distances=[p.distance_m for p in self._analysed],
                ),
            ]
        )
        # Marca no mapa os três piores trechos: ver *onde* na pista se perdeu
        # tempo é a informação que a tabela sozinha não dá.
        self._map.set_markers(
            [
                TrackMarker(
                    x=spot.position_x,
                    z=spot.position_z,
                    color=palette.yellow,
                    label=segment.label,
                    hollow=True,
                )
                for segment in report.worst(3)
                if (spot := _point_at(self._reference, segment.start_distance_m))
                is not None
            ]
        )

        self._fill_table(report)
        self._request_debrief(report)
        self._hint.setText(
            "O tempo de cada trecho é a variação do delta dentro dele — "
            "não o delta acumulado. Por isso um trecho ruim não contamina os "
            "seguintes."
        )

    # ---------- engenheiro ----------

    def _request_debrief(self, report: TimeLossReport) -> None:
        """Pede o debrief da comparação exibida. Não bloqueia nada.

        O pedido sai a cada troca de volta, e é o `EngineerService` que resolve
        a corrida: pedidos que chegam com outro em andamento substituem o
        pendente, e resposta de volta que já não está na tela é descartada.
        """
        service = self.core.engineer_service
        if service is None or not service.is_available:
            return

        session = self.core.session_manager.session
        car = session.car.name if session.car else ""
        service.request_debrief(
            report,
            track=self._track_name(),
            car=car,
            lap_time_ms=self._analysed[-1].elapsed_ms if self._analysed else 0,
            reference_time_ms=(
                self._reference[-1].elapsed_ms if self._reference else None
            ),
            corners=detect_corners(self._analysed),
        )

    def _track_name(self) -> str:
        track_id = self._analysed_selector.current_track_id()
        for track in self.core.tracks.get_all():
            if track.id == track_id:
                return track.name
        return ""

    def _on_engineer_started(self, level: str) -> None:
        if level == "debrief":
            self._advice.show_thinking()

    def _on_advice(self, advice: object) -> None:
        # Só o debrief interessa a esta página: o serviço é compartilhado, e a
        # nota de rádio pedida pela página ao vivo chega aqui também.
        if str(getattr(getattr(advice, "level", ""), "value", "")) == "debrief":
            self._advice.show_advice(advice)

    def _fill_table(self, report: TimeLossReport) -> None:
        palette = self.theme.palette

        # Perdas primeiro (do pior para o menor), depois os ganhos.
        ordered = report.losses + report.gains
        self._table.setRowCount(len(ordered))

        for row, segment in enumerate(ordered):
            cells = (
                segment.label,
                f"{segment.start_distance_m:.0f} m",
                f"{segment.time_delta_ms / 1000.0:+.3f} s",
                segment.cause() or "—",
            )
            for column, text in enumerate(cells):
                self._table.setItem(row, column, QTableWidgetItem(text))

            # Só a coluna de delta é colorida: é a que carrega o julgamento, e
            # colorir a linha inteira tornaria a tabela ilegível.
            delta_item = self._table.item(row, 2)
            if delta_item is not None:
                delta_item.setForeground(
                    QColor(palette.yellow if segment.is_loss else palette.green)
                )

    def _on_hover(self, distance_m: float) -> None:
        """Um cursor só, compartilhado pelos dois gráficos e pelo mapa."""
        self._delta_chart.set_cursor(distance_m)
        self._speed_chart.set_cursor(distance_m)
        self._map.set_cursor(distance_m)

    def _on_row_selected(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        item = self._table.item(rows[0].row(), 1)
        if item is None:
            return
        try:
            distance = float(item.text().split()[0])
        except (ValueError, IndexError):
            return
        self._on_hover(distance)


def _point_at(
    points: list[TelemetryPoint], distance_m: float
) -> TelemetryPoint | None:
    """Amostra mais próxima da distância informada."""
    if not points:
        return None
    return min(points, key=lambda p: abs(p.distance_m - distance_m))
