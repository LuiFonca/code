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
    QInputDialog,
    QLabel,
    QMessageBox,
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

HISTORY_COLUMNS = (
    "Volta", "Carro", "Tempo", "Δ melhor", "Setor 1", "Setor 2", "Setor 3", "Data",
)

#: Índice da coluna de carro, para o alinhamento à esquerda junto com "Volta".
#: Nome de carro é texto e centralizado fica ilegível numa lista.
CAR_COLUMN = 1
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
        # A barra vai para o **corpo**, e não para o cabeçalho. No cabeçalho ela
        # disputava a linha com o título: seis controles à direita de "Histórico"
        # não cabem na largura, e o resultado era a barra flutuando no meio da
        # página com um vazio enorme em volta. No corpo ela é uma linha comum,
        # ancorada à esquerda, e sobra largura para a tabela.
        self.content.addWidget(self._build_toolbar())

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
        # Duplo clique abre a volta na Análise. O caminho antes era: anotar
        # o número da volta, trocar de aba, reencontrar a pista no combo e
        # procurar a volta na lista — quatro passos para uma intenção só.
        self._table.itemDoubleClicked.connect(self._on_lap_double_clicked)
        # Todas as colunas ao conteúdo, **menos a do carro**, que fica com a
        # sobra. Esticando todas por igual, tempo e setor — que são números
        # curtos e de largura fixa — recebiam o mesmo espaço que "Porsche 911
        # GT3 RS", e só o nome do carro saía cortado. Cortar o nome é o pior
        # lugar para economizar largura: é o único campo da linha que não se
        # deduz do resto.
        cabecalho = self._table.horizontalHeader()
        cabecalho.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        cabecalho.setSectionResizeMode(CAR_COLUMN, QHeaderView.ResizeMode.Stretch)
        table_card.add(self._table)
        # Esticador na tabela: é ela que se beneficia de espaço, e sem isto a
        # sobra ia para os vãos entre os cartões.
        self.content.addWidget(table_card, stretch=1)

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

        # Filtro **opcional** de carro. Uma pista com dois carros diferentes
        # tem duas tabelas de tempo misturadas numa, e comparar as duas não
        # diz nada sobre pilotagem. Opcional porque o caso comum é um carro
        # só, e um filtro obrigatório cobraria uma escolha a mais de quem não
        # precisa dela.
        self._car_combo = QComboBox()
        self._car_combo.currentIndexChanged.connect(lambda _: self._reload_laps())

        reload_button = QPushButton("Atualizar")
        reload_button.setObjectName(OBJ_GHOST_BUTTON)
        reload_button.clicked.connect(self.refresh)

        self._delete_selected = QPushButton("Excluir selecionadas")
        self._delete_selected.setObjectName(OBJ_GHOST_BUTTON)
        self._delete_selected.clicked.connect(self._on_delete_selected)

        self._delete_all = QPushButton("Excluir tudo da pista")
        self._delete_all.setObjectName(OBJ_GHOST_BUTTON)
        self._delete_all.clicked.connect(self._on_delete_all)

        # Renomear fica **antes** dos botões de excluir, e não ao lado deles:
        # é a operação que salva o acervo quando o nome saiu errado, e a única
        # alternativa que existia antes era apagar as voltas.
        self._rename = QPushButton("Renomear pista…")
        self._rename.setObjectName(OBJ_GHOST_BUTTON)
        self._rename.clicked.connect(self._on_rename_track)

        layout.addWidget(QLabel("Pista:"))
        layout.addWidget(self._track_combo)
        layout.addWidget(QLabel("Carro:"))
        layout.addWidget(self._car_combo)
        layout.addWidget(reload_button)
        layout.addWidget(self._rename)
        layout.addWidget(self._delete_selected)
        layout.addWidget(self._delete_all)
        layout.addStretch(1)
        return bar

    def _on_lap_double_clicked(self, item: QTableWidgetItem) -> None:
        """Abre a volta da linha na aba de Análise.

        A pista vem do combo e não da linha: a tabela mostra uma pista de
        cada vez, e ler dali evita depender de uma coluna que pode mudar de
        posição.
        """
        volta = self._lap_at_row(item.row())
        track_id = self._track_combo.currentData()
        if volta is None or volta.id is None or track_id is None:
            return

        shell = self.window()
        abrir = getattr(shell, "open_lap_in_analysis", None)
        if abrir is not None:
            abrir(int(track_id), volta.id)

    def _lap_at_row(self, row: int) -> Lap | None:
        """A volta de uma linha da tabela, se a linha existir."""
        if 0 <= row < len(self._laps):
            return self._laps[row]
        return None

    # ---------- renomear ----------

    def _on_rename_track(self) -> None:
        """Troca o nome da pista sem perder as voltas dela.

        O catálogo entra como sugestão editável em vez de lista fechada: os 105
        circuitos cobrem o caso comum, mas layouts e variantes que o catálogo não
        tem continuam digitáveis. Uma lista fechada obrigaria a escolher um nome
        errado quando o certo não estivesse nela.
        """
        track_id = self._track_combo.currentData()
        if track_id is None:
            return
        atual = self._track_combo.currentText()

        nomes = sorted(
            {t.name for t in self.core.catalog.tracks.values() if t.name},
            key=str.lower,
        )
        novo, confirmou = QInputDialog.getItem(
            self,
            "Renomear pista",
            f"Novo nome para “{atual}”:",
            nomes,
            nomes.index(atual) if atual in nomes else 0,
            True,
        )
        if not confirmou or not novo.strip() or novo.strip() == atual:
            return

        destino = self.core.tracks.rename(int(track_id), novo.strip())
        self.refresh()
        index = self._track_combo.findData(destino)
        if index >= 0:
            self._track_combo.setCurrentIndex(index)
        self._note.setText(f"Pista renomeada para “{novo.strip()}”.")

    # ---------- exclusão ----------

    def _selected_laps(self) -> list[Lap]:
        """As voltas das linhas marcadas, na ordem da tabela."""
        linhas = sorted({i.row() for i in self._table.selectedIndexes()})
        return [self._laps[i] for i in linhas if 0 <= i < len(self._laps)]

    def _confirm(self, titulo: str, texto: str) -> bool:
        """Exclusão é irreversível e não tem desfazer — então pergunta.

        A telemetria de uma volta são ~6.000 amostras que só existem porque
        alguém pilotou. Um clique errado num botão ao lado de "Atualizar"
        apagaria uma sessão inteira sem nada a recuperar.
        """
        resposta = QMessageBox.question(
            self,
            titulo,
            texto,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return resposta == QMessageBox.StandardButton.Yes

    def _on_delete_selected(self) -> None:
        voltas = self._selected_laps()
        if not voltas:
            self._note.setText("selecione ao menos uma volta na tabela")
            return

        if not self._confirm(
            "Excluir voltas",
            f"Excluir {len(voltas)} volta(s) selecionada(s)?\n\n"
            "A telemetria gravada é apagada e não há como recuperar.",
        ):
            return

        for volta in voltas:
            # `id` é opcional no modelo (uma volta ainda não gravada não tem
            # um), mas tudo que chega à tabela veio do banco.
            if volta.id is not None:
                self.core.laps.delete(volta.id)
        self._after_delete(f"{len(voltas)} volta(s) excluída(s)")

    def _on_delete_all(self) -> None:
        track_id = self._track_combo.currentData()
        if track_id is None:
            return
        nome = self._track_combo.currentText()
        total = len(self._laps)
        if not total:
            self._note.setText("não há voltas para excluir")
            return

        if not self._confirm(
            "Excluir tudo da pista",
            f"Excluir TODAS as {total} voltas de {nome}?\n\n"
            "A telemetria gravada é apagada e não há como recuperar.",
        ):
            return

        self.core.laps.delete_by_track(int(track_id))
        self._after_delete(f"todas as voltas de {nome} foram excluídas")

    def _after_delete(self, mensagem: str) -> None:
        """Recarrega e avisa as outras páginas, que agora seguram voltas mortas.

        Sem `invalidate`, Análise e Comparar continuariam exibindo uma volta que
        não existe mais no banco — e o próximo clique nelas iria buscar amostras
        de um `lap_id` apagado.
        """
        self._reload_laps()
        self._note.setText(mensagem)

        # A janela é quem conhece as páginas; `core` não, e nem deveria. Marcar
        # as irmãs como sujas faz cada uma recarregar ao ser aberta.
        shell = self.window()
        for page in getattr(shell, "_pages", ()):
            if page is not self:
                page.invalidate()

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

        todas = self.core.laps.get_by_track(int(track_id))
        self._reload_cars(todas)

        car_id = self._car_combo.currentData()
        self._laps = (
            todas if car_id is None else [x for x in todas if x.car_id == car_id]
        )
        self._populate(int(track_id))

    def _reload_cars(self, laps: list[Lap]) -> None:
        """Repõe a lista com os carros que **esta pista** tem gravados.

        Só os desta pista: oferecer o catálogo inteiro seria oferecer
        filtros que não filtram nada, e a lista útil tem dois ou três nomes.
        A escolha atual sobrevive à reposição quando o carro ainda existe —
        senão trocar de pista descartaria o filtro sem avisar.
        """
        anterior = self._car_combo.currentData()
        ids = {x.car_id for x in laps if x.car_id is not None}

        self._car_combo.blockSignals(True)
        self._car_combo.clear()
        self._car_combo.addItem("todos", None)
        for car_id in sorted(ids, key=lambda i: self.car_name(i).lower()):
            self._car_combo.addItem(self.car_name(car_id), car_id)
        indice = self._car_combo.findData(anterior)
        self._car_combo.setCurrentIndex(max(0, indice))
        self._car_combo.blockSignals(False)

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
                self.car_name(lap.car_id),
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
                    if column and column != CAR_COLUMN
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
