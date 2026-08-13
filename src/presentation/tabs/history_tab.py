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
    QFileDialog,
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
        self._rows: list[LapRow] = []
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

        self._invalidate_button = QPushButton("Marcar inválida")
        self._invalidate_button.setObjectName("stopButton")
        self._invalidate_button.setToolTip(
            "O GT7 não informa corte de pista ou contato. Marque aqui a volta "
            "que não deve contar como recorde — ela permanece no histórico."
        )
        self._invalidate_button.clicked.connect(self._on_toggle_valid_clicked)

        self._export_button = QPushButton("Exportar")
        self._export_button.setObjectName("stopButton")
        self._export_button.setToolTip(
            "Salva a volta selecionada num arquivo, para backup ou para enviar "
            "a outra pessoa."
        )
        self._export_button.clicked.connect(self._on_export_clicked)

        self._import_button = QPushButton("Importar")
        self._import_button.setObjectName("stopButton")
        self._import_button.setToolTip(
            "Lê uma volta de arquivo e grava na pista aberta."
        )
        self._import_button.clicked.connect(self._on_import_clicked)

        self._assign_button = QPushButton("Atribuir pista")
        self._assign_button.setObjectName("stopButton")
        self._assign_button.setToolTip(
            "Move a volta selecionada para a pista escolhida na barra superior.\n"
            "Serve para as voltas que o app gravou sem pista e não conseguiu "
            "reconhecer pelo traçado."
        )
        self._assign_button.clicked.connect(self._on_assign_clicked)

        self._delete_button = QPushButton("Excluir volta")
        self._delete_button.setObjectName("stopButton")
        self._delete_button.clicked.connect(self._on_delete_clicked)

        controls.addWidget(self._search)
        controls.addWidget(self._count_label)
        controls.addStretch()
        controls.addWidget(self._export_button)
        controls.addWidget(self._import_button)
        controls.addWidget(self._invalidate_button)
        controls.addWidget(self._assign_button)
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
        # Guardado para o botão de validade saber o estado atual da linha.
        self._rows = rows
        self._empty_label.setVisible(not rows)
        self._table.setVisible(bool(rows))
        self._count_label.setText(f"{len(rows)} volta(s)" if rows else "")

        # Ordenação desligada durante o preenchimento: com ela ativa, o Qt
        # reordena a cada linha inserida e as células saem trocadas.
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))

        for r, row in enumerate(rows):
            lap = row.lap
            # Troféu para o recorde; aviso para a volta parcial, que tem tempo
            # verdadeiro mas dados de só um pedaço — sem a marca, o usuário
            # acharia que o app perdeu o recorde dele.
            if row.is_best:
                mark, mark_sort = "🏆", 0
            elif not row.is_valid:
                mark, mark_sort = "✕", 3
            elif not row.is_complete:
                mark, mark_sort = "⚠", 2
            else:
                mark, mark_sort = "", 1
            cells = [
                _SortableItem(mark, mark_sort),
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
                elif not row.is_valid:
                    item.setForeground(Qt.darkGray)
                    item.setToolTip(
                        "Volta marcada como inválida. Continua no histórico, mas "
                        "não disputa recorde nem serve de referência para o delta."
                    )
                elif not row.is_complete:
                    item.setForeground(Qt.gray)
                    item.setToolTip(
                        "Volta parcial: o app começou a observar com ela já em "
                        "andamento. O tempo é o do jogo, mas os dados cobrem só "
                        "parte do traçado — por isso não conta como recorde."
                    )
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

    def _on_assign_clicked(self):
        """Pergunta a pista e move a volta selecionada para ela.

        A pista é escolhida aqui, num diálogo próprio, e não na barra superior:
        selecionar a pista lá trocaria a lista exibida e a volta que se quer
        atribuir sumiria da tela antes de o usuário chegar ao botão.

        A lista é editável — a primeira volta de um circuito novo precisa poder
        criar a pista na hora.
        """
        from PySide6.QtWidgets import QInputDialog

        lap_id = self._selected_lap_id()
        if lap_id is None:
            QMessageBox.information(
                self, "Nenhuma volta selecionada",
                "Selecione uma volta na tabela para atribuir a pista.",
            )
            return

        conhecidas = self._vm.available_track_names()
        nome, ok = QInputDialog.getItem(
            self,
            "Atribuir pista",
            f"Em que pista foi rodada a volta {lap_id}?",
            conhecidas,
            0,
            True,   # editável: permite digitar uma pista que ainda não existe
        )
        if ok and nome.strip():
            self._vm.assign_track_by_name(lap_id, nome)

    def _on_toggle_valid_clicked(self):
        lap_id = self._selected_lap_id()
        if lap_id is None:
            QMessageBox.information(
                self, "Nenhuma volta selecionada",
                "Selecione uma volta na tabela para marcar.",
            )
            return
        linha = next((r for r in self._rows if r.lap.id == lap_id), None)
        if linha is None:
            return
        self._vm.set_lap_valid(lap_id, not linha.is_valid)

    def _on_export_clicked(self):
        lap_id = self._selected_lap_id()
        if lap_id is None:
            QMessageBox.information(
                self, "Nenhuma volta selecionada",
                "Selecione uma volta na tabela para exportar.",
            )
            return
        destino, _ = QFileDialog.getSaveFileName(
            self, "Exportar volta", f"volta-{lap_id}.json", "Volta GT7 (*.json)"
        )
        if not destino:
            return
        if self._vm.export_lap(lap_id, destino):
            self._show_info(f"Volta {lap_id} exportada.")

    def _on_import_clicked(self):
        origem, _ = QFileDialog.getOpenFileName(
            self, "Importar volta", "", "Volta GT7 (*.json)"
        )
        if not origem:
            return
        if self._vm.import_lap(origem):
            self._show_info("Volta importada para a pista aberta.")

    def _show_info(self, mensagem: str):
        QMessageBox.information(self, "Pronto", mensagem)

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
