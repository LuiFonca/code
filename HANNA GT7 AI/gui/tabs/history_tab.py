"""
Aba "Histórico" — lista as voltas salvas de uma pista, com tempo total e
tempo por setor. Também mostra o pódio das 5 voltas mais rápidas.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView
)
from PySide6.QtCore import Qt

from analysis import lap_storage
from gui.widgets import format_ms


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
        refresh_button = QPushButton("Atualizar")
        refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.title_label)
        header.addStretch()
        header.addWidget(refresh_button)
        layout.addLayout(header)

        self.podium_label = QLabel("")
        self.podium_label.setStyleSheet("color: #f2c94c; font-size: 12px;")
        self.podium_label.setWordWrap(True)
        layout.addWidget(self.podium_label)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Volta", "Data", "Tempo total", "Setor 1", "Setor 2", "Setor 3"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
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
        if self.track_id is None:
            self.table.setRowCount(0)
            self.table.setVisible(False)
            self.podium_label.setText("")
            self.empty_label.setText("Conecte-se a uma pista para ver o histórico de voltas.")
            self.empty_label.setVisible(True)
            return

        laps = lap_storage.list_laps(self.track_id)
        self.table.setRowCount(0)
        self.empty_label.setVisible(len(laps) == 0)
        self.table.setVisible(len(laps) > 0)

        top_laps = lap_storage.get_top_laps(self.track_id)
        if top_laps:
            medals = ["🥇", "🥈", "🥉", "4º", "5º"]
            parts = [
                f"{medals[i]} {format_ms(lap_time_ms)}"
                for i, (lap_id, lap_time_ms, _) in enumerate(top_laps)
            ]
            self.podium_label.setText("Melhores voltas: " + "   ".join(parts))
        else:
            self.podium_label.setText("")

        best_time = top_laps[0][1] if top_laps else None

        for row_index, (lap_id, lap_time_ms, recorded_at) in enumerate(laps):
            self.table.insertRow(row_index)

            id_item = QTableWidgetItem(f"#{lap_id}")
            date_item = QTableWidgetItem(self._format_date(recorded_at))
            time_item = QTableWidgetItem(format_ms(lap_time_ms))

            if lap_time_ms == best_time:
                time_item.setForeground(Qt.green)
                id_item.setText(f"#{lap_id} 🏆")

            self.table.setItem(row_index, 0, id_item)
            self.table.setItem(row_index, 1, date_item)
            self.table.setItem(row_index, 2, time_item)

            sector_times = lap_storage.get_sector_times(lap_id)
            for sector_index in range(3):
                sector_ms = sector_times[sector_index] if sector_index < len(sector_times) else None
                self.table.setItem(row_index, 3 + sector_index, QTableWidgetItem(format_ms(sector_ms)))

    @staticmethod
    def _format_date(timestamp: float) -> str:
        import datetime
        return datetime.datetime.fromtimestamp(timestamp).strftime("%d/%m %H:%M")
