"""
Página de histórico — as voltas gravadas, por pista.

Substitui a aba de histórico da aplicação anterior. A diferença de fundo é que
esta lê do núcleo (`core.laps`), não de um ViewModel com SQL próprio.

Os tempos de setor usam **os mesmos limites de distância para todas as voltas**
(`sector_boundaries_m` sobre a melhor volta da pista), e não os limites vigentes
quando cada volta foi salva. É o que torna honesto comparar "setor 2 da volta A"
com "setor 2 da volta B" — a alternativa compara pedaços diferentes de asfalto.

O melhor tempo de cada setor recebe a cor roxa da torre de cronometragem, e a
linha do recorde fica destacada: um piloto lê a tabela procurando exatamente
essas duas coisas.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from gt7core.analytics.series import LapSeries, sector_boundaries_m, sector_times_from_series
from gt7core.domain.models import Lap

from ..application import CoreApplication
from ..design.theme import OBJ_GHOST_BUTTON, OBJ_STATUS_BAR
from ..design.tokens import Space, Theme
from ..widgets.cards import Card, MetricCard, MetricGrid
from ..widgets.selectors import format_delta, format_lap_time
from .base import Page

HISTORY_COLUMNS = ("Volta", "Tempo", "Δ melhor", "Setor 1", "Setor 2", "Setor 3", "Data")
NUM_SECTORS = 3

# Quantas voltas carregar com amostras para calcular setores. Carregar todas
# significa ler dezenas de milhares de linhas para preencher uma tabela — a
# janela de retenção por pista é 20, então este teto raramente aparece.
SECTOR_COMPUTE_LIMIT = 20


class HistoryPage(Page):
    page_id = "history"
    nav_title = "Histórico"
    title = "Histórico"
    subtitle = "Voltas gravadas por pista"

    def __init__(self, core: CoreApplication, theme: Theme) -> None:
        self._laps: list[Lap] = []
        super().__init__(core, theme)

    def build(self) -> None:
        self.header.add_action(self._build_toolbar())

        self._summary = MetricGrid(columns=4)
        for key, label, unit in (
            ("count", "Voltas", ""),
            ("best", "Melhor", ""),
            ("median", "Mediana", ""),
            ("ideal", "Volta ideal", ""),
        ):
            self._summary.add_card(key, MetricCard(label, unit))
        self.content.addWidget(self._summary)

        table_card = Card()
        self._table = QTableWidget(0, len(HISTORY_COLUMNS))
        self._table.setHorizontalHeaderLabels(HISTORY_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        table_card.add(self._table)
        self.content.addWidget(table_card)

        self._note = QLabel("")
        self._note.setObjectName(OBJ_STATUS_BAR)
        self.content.addWidget(self._note)

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.SM.px)

        self._track_combo = QComboBox()
        self._track_combo.currentIndexChanged.connect(lambda _: self._reload_laps())

        reload_button = QPushButton("Atualizar")
        reload_button.setObjectName(OBJ_GHOST_BUTTON)
        reload_button.clicked.connect(self.refresh)

        layout.addWidget(QLabel("Pista:"))
        layout.addWidget(self._track_combo)
        layout.addWidget(reload_button)
        return bar

    # ---------- dados ----------

    def refresh(self) -> None:
        previous = self._track_combo.currentData()
        self._track_combo.blockSignals(True)
        self._track_combo.clear()
        for track in self.core.tracks.get_all():
            self._track_combo.addItem(track.name, track.id)
        if previous is not None:
            index = self._track_combo.findData(previous)
            if index >= 0:
                self._track_combo.setCurrentIndex(index)
        self._track_combo.blockSignals(False)
        self._reload_laps()

    def _reload_laps(self) -> None:
        track_id = self._track_combo.currentData()
        if track_id is None:
            self._laps = []
            self._table.setRowCount(0)
            self._summary.clear_values(self.theme)
            self._note.setText("nenhuma pista gravada ainda")
            return

        self._laps = self.core.laps.get_by_track(int(track_id))
        self._populate(int(track_id))

    def _populate(self, track_id: int) -> None:
        palette = self.theme.palette
        if not self._laps:
            self._table.setRowCount(0)
            self._summary.clear_values(self.theme)
            self._note.setText("nenhuma volta gravada nesta pista")
            return

        times = [lap.lap_time_ms for lap in self._laps if lap.lap_time_ms > 0]
        best_ms = min(times) if times else None

        sectors = self._compute_sectors(track_id)
        best_sectors = _best_per_sector(sectors)

        cards = self._summary.cards
        cards["count"].set_value(str(len(self._laps)))
        cards["best"].set_value(format_lap_time(best_ms) if best_ms else "—")
        cards["median"].set_value(
            format_lap_time(sorted(times)[len(times) // 2]) if times else "—"
        )

        # Volta ideal: a soma dos melhores setores. Não é um tempo que o piloto
        # fez — é o que ele já demonstrou ser capaz de fazer, pedaço por pedaço.
        if all(value is not None for value in best_sectors) and best_sectors:
            ideal = sum(value for value in best_sectors if value is not None)
            cards["ideal"].set_value(format_lap_time(ideal), palette.purple)
        else:
            cards["ideal"].set_value("—")

        self._table.setRowCount(len(self._laps))
        for row, lap in enumerate(self._laps):
            lap_sectors = sectors.get(lap.id, [None] * NUM_SECTORS)
            cells = [
                f"#{lap.id}",
                format_lap_time(lap.lap_time_ms),
                (
                    "★"
                    if best_ms is not None and lap.lap_time_ms == best_ms
                    else format_delta(lap.lap_time_ms - best_ms)
                    if best_ms
                    else "—"
                ),
                *[
                    format_lap_time(value) if value else "—"
                    for value in lap_sectors[:NUM_SECTORS]
                ],
                lap.start_time.strftime("%d/%m %H:%M") if lap.start_time else "—",
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignCenter
                    if column
                    else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )
                self._table.setItem(row, column, item)

            if best_ms is not None and lap.lap_time_ms == best_ms:
                _colour_row(self._table, row, palette.purple)

            # Melhor setor em roxo, como na torre de cronometragem.
            for sector_index in range(NUM_SECTORS):
                value = (
                    lap_sectors[sector_index]
                    if sector_index < len(lap_sectors)
                    else None
                )
                if value is not None and value == best_sectors[sector_index]:
                    cell = self._table.item(row, 3 + sector_index)
                    if cell is not None:
                        cell.setForeground(QColor(palette.purple))

        self._note.setText(
            f"setores calculados sobre os mesmos limites de distância "
            f"(melhor volta da pista, {NUM_SECTORS} setores)"
        )

    def _compute_sectors(self, track_id: int) -> dict[int | None, list[int | None]]:
        """Tempos de setor com limites comuns a todas as voltas."""
        best = self.core.laps.get_best(track_id)
        if best is None or best.id is None:
            return {}

        reference_points = self.core.laps.load_points(best.id)
        if len(reference_points) < 2:
            return {}

        boundaries = sector_boundaries_m(
            reference_points[-1].distance_m, NUM_SECTORS
        )

        result: dict[int | None, list[int | None]] = {}
        for lap in self._laps[:SECTOR_COMPUTE_LIMIT]:
            if lap.id is None:
                continue
            points = self.core.laps.load_points(lap.id)
            if len(points) < 2:
                continue
            result[lap.id] = sector_times_from_series(LapSeries(points), boundaries)
        return result


def _best_per_sector(
    sectors: dict[int | None, list[int | None]],
) -> list[int | None]:
    best: list[int | None] = [None] * NUM_SECTORS
    for values in sectors.values():
        for index in range(min(NUM_SECTORS, len(values))):
            value = values[index]
            if value is None:
                continue
            current = best[index]
            if current is None or value < current:
                best[index] = value
    return best


def _colour_row(table: QTableWidget, row: int, colour: str) -> None:
    for column in range(table.columnCount()):
        item = table.item(row, column)
        if item is not None:
            item.setForeground(QColor(colour))
