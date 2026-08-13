"""
Aba "Histórico" — lista as voltas salvas de uma pista (a pista já filtra o
histórico; o carro aparece como coluna própria, então o agrupamento
"Pista + carro" do item 10 fica: escolha a pista no topo da janela, e
compare/filtre por carro aqui dentro). Mostra tempo total e tempo por
setor, o pódio das 5 voltas mais rápidas, e permite ordenar por qualquer
coluna e buscar por carro/id.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox
)
from PySide6.QtCore import Qt

from analysis import lap_storage
from gui.widgets import format_ms


class _SortableItem(QTableWidgetItem):
    """QTableWidgetItem que ordena por uma chave numérica própria
    (`sort_key`) em vez do texto exibido."""

    def __init__(self, text: str, sort_key):
        super().__init__(text)
        self.sort_key = sort_key

    def __lt__(self, other):
        if isinstance(other, _SortableItem):
            return self.sort_key < other.sort_key
        return super().__lt__(other)


class HistoryTab(QWidget):
    def __init__(self, track_id):
        super().__init__()
        self.track_id = track_id

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 16, 4, 4)
        layout.setSpacing(12)

        header = QHBoxLayout()
        self.title_label = QLabel("Voltas salvas")
        self.title_label.setStyleSheet("font-size: 15px; font-weight: 600;")

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Buscar por carro, nº da volta ou tempo...")
        self.search_input.setMinimumWidth(140)
        self.search_input.setMaximumWidth(300)
        self.search_input.textChanged.connect(self._apply_filter)

        self.clear_button = QPushButton("Limpar dados")
        self.clear_button.setStyleSheet(
            "QPushButton { background-color: #3a1414; color: #ff5c5c; }"
            "QPushButton:hover { background-color: #4a1a1a; }"
        )
        self.clear_button.setToolTip("Remove todas as voltas salvas desta pista.")
        self.clear_button.clicked.connect(self._on_clear_clicked)

        refresh_button = QPushButton("Atualizar")
        refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.title_label)
        header.addStretch()
        header.addWidget(self.search_input)
        header.addWidget(refresh_button)
        header.addWidget(self.clear_button)
        layout.addLayout(header)

        self.podium_label = QLabel("")
        self.podium_label.setStyleSheet("color: #f2c94c; font-size: 12px;")
        self.podium_label.setWordWrap(True)
        layout.addWidget(self.podium_label)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Volta", "Carro", "Data", "Tempo total", "Setor 1", "Setor 2", "Setor 3"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #1a1d25;
                border: 1px solid #23262f;
                border-radius: 10px;
                gridline-color: #23262f;
            }
            QHeaderView::section {
                background-color: #1c1f27;
                color: #8a8e99;
                padding: 8px;
                border: none;
                font-weight: 600;
                font-size: 11px;
            }
            QTableWidget::item {
                padding: 6px;
            }
            QTableWidget::item:selected {
                background-color: #2a3550;
            }
        """)
        layout.addWidget(self.table)

        self.empty_label = QLabel(
            "Nenhuma volta salva ainda. Conecte e complete voltas na aba 'Ao Vivo' para vê-las aqui."
        )
        self.empty_label.setStyleSheet("color: #6b6f7a; font-size: 12px;")
        self.empty_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.empty_label)

        self.refresh()

    def set_track(self, track_id: int):
        self.track_id = track_id
        self.refresh()

    def refresh(self):
        self.clear_button.setEnabled(self.track_id is not None)

        if self.track_id is None:
            self.table.setRowCount(0)
            self.table.setVisible(False)
            self.podium_label.setText("")
            self.empty_label.setText("Conecte-se a uma pista para ver o histórico de voltas.")
            self.empty_label.setVisible(True)
            return

        laps = lap_storage.list_laps(self.track_id)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.empty_label.setVisible(len(laps) == 0)
        self.table.setVisible(len(laps) > 0)

        top_laps = lap_storage.get_top_laps(self.track_id)
        if top_laps:
            medals = ["🥇", "🥈", "🥉", "4º", "5º"]
            parts = [
                f"{medals[i]} {format_ms(lap_time_ms)}"
                for i, (lap_id, lap_time_ms, _recorded_at, _car_name) in enumerate(top_laps)
            ]
            self.podium_label.setText("Melhores voltas: " + "   ".join(parts))
        else:
            self.podium_label.setText("")

        best_time = top_laps[0][1] if top_laps else None

        lap_ids = [lap_id for lap_id, *_ in laps]
        all_sectors = lap_storage.get_sector_times_batch(lap_ids)

        for row_index, (lap_id, lap_time_ms, recorded_at, car_name) in enumerate(laps):
            self.table.insertRow(row_index)

            id_item = _SortableItem(f"#{lap_id}", lap_id)
            car_item = QTableWidgetItem(car_name or "—")
            date_item = _SortableItem(self._format_date(recorded_at), recorded_at)
            time_item = _SortableItem(format_ms(lap_time_ms), lap_time_ms)

            if lap_time_ms == best_time:
                time_item.setForeground(Qt.green)
                id_item.setText(f"#{lap_id} 🏆")

            self.table.setItem(row_index, 0, id_item)
            self.table.setItem(row_index, 1, car_item)
            self.table.setItem(row_index, 2, date_item)
            self.table.setItem(row_index, 3, time_item)

            sector_times = all_sectors.get(lap_id, [])
            for sector_index in range(3):
                sector_ms = sector_times[sector_index] if sector_index < len(sector_times) else None
                sort_key = sector_ms if sector_ms is not None else -1
                self.table.setItem(
                    row_index, 4 + sector_index,
                    _SortableItem(format_ms(sector_ms), sort_key),
                )

        self.table.setSortingEnabled(True)
        self._apply_filter(self.search_input.text())

    def _on_clear_clicked(self):
        if self.track_id is None:
            return

        reply = QMessageBox.warning(
            self,
            "Limpar dados da pista",
            "Tem certeza que deseja remover TODAS as voltas salvas desta pista?\n\n"
            "Esta ação não pode ser desfeita.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if reply != QMessageBox.Yes:
            return

        lap_storage.delete_track_data(self.track_id)
        self.refresh()

    def _apply_filter(self, text: str):
        needle = text.strip().lower()
        for row in range(self.table.rowCount()):
            if not needle:
                self.table.setRowHidden(row, False)
                continue
            id_text = self.table.item(row, 0).text().lower()
            car_text = self.table.item(row, 1).text().lower()
            time_text = self.table.item(row, 3).text().lower()
            match = needle in id_text or needle in car_text or needle in time_text
            self.table.setRowHidden(row, not match)

    @staticmethod
    def _format_date(timestamp: float) -> str:
        import datetime
        return datetime.datetime.fromtimestamp(timestamp).strftime("%d/%m %H:%M")
