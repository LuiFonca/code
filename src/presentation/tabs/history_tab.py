"""
Aba "Histórico" — tabela de voltas gravadas.

View pura: recebe `HistoryViewModel` e desenha `LapRow`s. Não consulta banco e
não formata tempo de setor — na versão antiga esta aba chamava `lap_storage`
diretamente, com SQL dentro do widget.
"""

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...application.viewmodels.history_viewmodel import HistoryViewModel, LapRow
from ..widgets.widgets import format_ms

COLUMNS = ["", "Volta", "Tempo", "Setor 1", "Setor 2", "Setor 3", "Carro", "Data"]


class _SortableItem(QTableWidgetItem):
    """Célula que ordena pelo valor real, não pelo texto.

    Sem isto, "1:28.450" e "1:9.200" ordenam alfabeticamente e a tabela mente
    sobre qual volta foi mais rápida.
    """

    def __init__(self, text: str, sort_key):
        super().__init__(text)
        self._sort_key = sort_key
        self.setFlags(self.flags() & ~Qt.ItemIsEditable)

    def __lt__(self, other):
        if isinstance(other, _SortableItem):
            return self._sort_key < other._sort_key
        return super().__lt__(other)


class HistoryTab(QWidget):
    def __init__(self, view_model: HistoryViewModel):
        super().__init__()
        self._vm = view_model
        self._build_ui()

        self._vm.laps_changed.connect(self._render)
        self._vm.error.connect(self._show_error)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 6, 4, 4)
        root.setSpacing(8)

        controls = QHBoxLayout()
        controls.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Buscar por carro, número da volta ou tempo...")
        self._search.textChanged.connect(self._vm.set_filter)
        self._search.setMaximumWidth(360)

        self._count_label = QLabel("")
        self._count_label.setObjectName("sectionHeader")

        self._clear_button = QPushButton("Limpar dados")
        self._clear_button.setObjectName("dangerButton")
        self._clear_button.clicked.connect(self._on_clear_clicked)

        self._delete_button = QPushButton("Excluir volta")
        self._delete_button.setObjectName("stopButton")
        self._delete_button.clicked.connect(self._on_delete_clicked)

        controls.addWidget(self._search)
        controls.addWidget(self._count_label)
        controls.addStretch()
        controls.addWidget(self._delete_button)
        controls.addWidget(self._clear_button)
        root.addLayout(controls)

        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        root.addWidget(self._table)

        self._empty_label = QLabel(
            "Nenhuma volta gravada nesta pista ainda.\n"
            "Conecte ao PlayStation, defina a pista e complete uma volta."
        )
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setStyleSheet("color: #6b6f7a; font-size: 14px;")
        root.addWidget(self._empty_label)

    # ---------- renderização ----------

    def _render(self, rows: list[LapRow]):
        self._empty_label.setVisible(not rows)
        self._table.setVisible(bool(rows))
        self._count_label.setText(f"{len(rows)} volta(s)" if rows else "")

        # Ordenação desligada durante o preenchimento: com ela ativa, o Qt
        # reordena a cada linha inserida e as células saem trocadas.
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))

        for r, row in enumerate(rows):
            lap = row.lap
            trophy = "🏆" if row.is_best else ""
            cells = [
                _SortableItem(trophy, 0 if row.is_best else 1),
                _SortableItem(str(lap.id or "--"), lap.id or 0),
                _SortableItem(format_ms(lap.lap_time_ms), lap.lap_time_ms),
            ]
            for i in range(3):
                ms = row.sector_times[i] if i < len(row.sector_times) else None
                cells.append(
                    _SortableItem(
                        format_ms(ms) if ms else "--",
                        ms if ms else 10**9,  # sem dado vai para o fim da ordenação
                    )
                )
            cells.append(_SortableItem(row.car_name or "--", (row.car_name or "").lower()))
            ts = lap.start_time
            cells.append(
                _SortableItem(
                    ts.strftime("%d/%m/%Y %H:%M") if ts else "--",
                    ts.timestamp() if ts else 0,
                )
            )

            for c, item in enumerate(cells):
                if row.is_best:
                    item.setForeground(Qt.green)
                self._table.setItem(r, c, item)

        self._table.setSortingEnabled(True)

    # ---------- ações ----------

    def _selected_lap_id(self) -> int | None:
        selected = self._table.selectedItems()
        if not selected:
            return None
        item = self._table.item(selected[0].row(), 1)
        try:
            return int(item.text())
        except (AttributeError, ValueError):
            return None

    def _on_delete_clicked(self):
        lap_id = self._selected_lap_id()
        if lap_id is None:
            QMessageBox.information(
                self, "Nenhuma volta selecionada",
                "Selecione uma volta na tabela para excluir.",
            )
            return
        confirm = QMessageBox.warning(
            self,
            "Excluir volta",
            f"Excluir a volta {lap_id}? Esta ação não pode ser desfeita.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm == QMessageBox.Yes:
            self._vm.delete_lap(lap_id)

    def _on_clear_clicked(self):
        if self._vm.track_id is None:
            return
        confirm = QMessageBox.warning(
            self,
            "Limpar dados da pista",
            "Isto apagará TODAS as voltas gravadas nesta pista, incluindo os "
            "recordes.\n\nEsta ação não pode ser desfeita. Continuar?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm == QMessageBox.Yes:
            self._vm.clear_track()

    def _show_error(self, message: str):
        QMessageBox.critical(self, "Erro", message)
