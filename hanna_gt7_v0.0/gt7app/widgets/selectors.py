"""
Seletores de pista e de volta.

Três páginas precisam da mesma pergunta — "qual pista, qual volta?" — e na
aplicação anterior cada aba a implementava do seu jeito, com formatações de
tempo levemente diferentes. Aqui é um widget só.

O rótulo de cada volta carrega o marcador de recorde (★) e o delta contra a
melhor da pista, porque escolher uma volta sem saber quanto ela foi mais lenta
é escolher no escuro.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QLabel, QWidget

from gt7core.domain.models import Lap
from gt7core.storage.repositories import SqliteLapRepository, SqliteTrackRepository

from ..design.theme import OBJ_SELECTOR_NOTE
from ..design.tokens import Space
from .flow import FlowLayout, labelled

#: Larguras mínimas, em pixels. O catálogo tem nomes como "24 Heures du Mans
#: Racing Circuit No Chicane"; caber todos deixaria o combo maior que a janela,
#: então a medida é o suficiente para identificar sem ambiguidade.
TRACK_COMBO_MIN_W = 230
LAP_COMBO_MIN_W = 170


def format_lap_time(total_ms: int) -> str:
    """`m:ss.mmm` — o formato que o jogo mostra."""
    if total_ms <= 0:
        return "—"
    minutes, remainder = divmod(total_ms, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def format_delta(delta_ms: int) -> str:
    signal = "+" if delta_ms >= 0 else "−"
    return f"{signal}{abs(delta_ms) / 1000:.3f}"


def describe_lap(lap: Lap, best_ms: int | None) -> str:
    """Rótulo de uma volta: tempo, marcador de recorde e delta."""
    text = f"#{lap.id}  {format_lap_time(lap.lap_time_ms)}"
    if best_ms is None or lap.lap_time_ms <= 0:
        return text
    if lap.lap_time_ms == best_ms:
        return f"{text}  ★"
    return f"{text}  {format_delta(lap.lap_time_ms - best_ms)}"


class _SelfRefreshingCombo(QComboBox):
    """Combo que consulta o banco no instante em que é aberto.

    A alternativa seria a página se inscrever num evento de "algo mudou", o que
    exigiria um evento para cada operação que mexe no acervo. Recarregar na
    abertura custa uma consulta por clique e não tem como ficar desatualizado.
    """

    def __init__(self, on_open: Callable[[], None]) -> None:
        super().__init__()
        self._on_open = on_open

    def showPopup(self) -> None:  # noqa: N802  (API do Qt)
        self._on_open()
        super().showPopup()


class TrackLapSelector(QWidget):
    """Combo de pista + combo de volta, sincronizados.

    Emite `lap_changed` com o id da volta escolhida (ou None). A página assina
    isso e não precisa saber nada sobre repositórios.
    """

    lap_changed = Signal(object)   # int | None
    track_changed = Signal(object)  # int | None

    def __init__(
        self,
        tracks: SqliteTrackRepository,
        laps: SqliteLapRepository,
        *,
        lap_label: str = "Volta:",
        limit: int = 40,
        show_track: bool = True,
    ) -> None:
        super().__init__()
        self._tracks = tracks
        self._laps = laps
        self._limit = limit
        self._loading = False

        # Layout que quebra linha. Numa linha rígida a largura mínima é a
        # soma dos combos, e é ela que chegava ao cabeçalho da página e cortava
        # a tela em janela estreita. Quebrando, o mínimo vira o do combo mais
        # largo — e a barra continua sendo uma linha só sempre que couber.
        layout = FlowLayout(self, spacing=Space.SM.px)

        # Combos que se recarregam ao **abrir**. Sem isso, uma volta apagada no
        # Histórico continuava listada aqui até a página ser reconstruída, e
        # escolhê-la abria uma volta sem amostras — dados fantasma que parecem
        # reais até o gráfico sair vazio.
        self._track_combo = _SelfRefreshingCombo(self._reload_tracks_only)
        self._lap_combo = _SelfRefreshingCombo(self._reload_laps_only)

        # Comparar duas voltas de **pistas diferentes** não significa nada: o
        # alinhamento é por distância no mesmo traçado. O segundo seletor da
        # comparação esconde a pista e segue a do primeiro — oferecer a escolha
        # era convidar para uma comparação sem sentido.
        # "Autodromo de Interlagos" aparecia como "Interlago": o combo assumia a
        # largura do item mais curto da lista. Os nomes do catálogo do GT7 são
        # longos, e um nome de pista cortado no meio deixa de identificar a pista.
        self._track_combo.setMinimumWidth(TRACK_COMBO_MIN_W)
        self._lap_combo.setMinimumWidth(LAP_COMBO_MIN_W)

        self._car_label = QLabel("")
        self._car_label.setObjectName(OBJ_SELECTOR_NOTE)
        self._car_label.setVisible(False)

        # Cada par rótulo+combo é um **bloco indivisível**: quebrando linha, a
        # barra corta entre os pares e nunca deixa um rótulo órfão no fim de
        # uma linha com o campo dele no começo da seguinte.
        #
        # O bloco inteiro some quando a pista não é oferecida — comparar duas
        # voltas de pistas diferentes não significa nada, e o segundo seletor
        # da comparação segue a pista do primeiro.
        self._track_block = labelled("Pista:", self._track_combo)
        self._track_block.setVisible(show_track)

        # O carro vem **sempre depois do combo de volta**, e a posição não
        # depende de a pista estar à mostra. Já dependeu: na comparação, a linha
        # de referência mostrava o carro antes da volta e a de comparação
        # depois, e as duas lado a lado pareciam ter os campos trocados. Ordem
        # igual nas duas é o que alinha a coluna e faz a leitura ser sempre a
        # mesma: qual volta, de qual carro.
        layout.addWidget(self._track_block)
        layout.addWidget(labelled(lap_label, self._lap_combo))
        layout.addWidget(self._car_label)

        self._track_combo.currentIndexChanged.connect(self._on_track_changed)
        self._lap_combo.currentIndexChanged.connect(self._on_lap_changed)

    def set_car(self, name: str) -> None:
        """Mostra qual carro fez a volta escolhida. Vazio esconde o rótulo.

        Esconder, e não escrever "—": um travessão ocuparia largura na barra
        para não dizer nada, e a barra já disputa espaço com dois combos de nome
        comprido.
        """
        self._car_label.setText(f"🚗 {name}" if name else "")
        self._car_label.setVisible(bool(name))

    # ---------- carga ----------

    def _reload_tracks_only(self) -> None:
        """Recarrega pistas ao abrir o combo, preservando a escolha."""
        if not self._loading:
            self.reload()

    def _reload_laps_only(self) -> None:
        if not self._loading:
            self._reload_laps(self.current_track_id())

    def reload(self) -> None:
        """Recarrega o catálogo de pistas preservando a seleção, se possível."""
        previous = self.current_track_id()
        self._loading = True
        self._track_combo.clear()
        for track in self._tracks.get_all():
            self._track_combo.addItem(track.name, track.id)
        self._loading = False

        if previous is not None:
            index = self._track_combo.findData(previous)
            if index >= 0:
                self._track_combo.setCurrentIndex(index)
                return
        self._on_track_changed()

    def _on_track_changed(self) -> None:
        if self._loading:
            return
        track_id = self.current_track_id()
        self.track_changed.emit(track_id)
        self._reload_laps(track_id)

    def _reload_laps(self, track_id: int | None) -> None:
        self._loading = True
        self._lap_combo.clear()

        if track_id is not None:
            laps = self._laps.get_by_track(track_id, limit=self._limit)
            best = self._laps.get_best(track_id)
            best_ms = best.lap_time_ms if best else None
            for lap in laps:
                self._lap_combo.addItem(describe_lap(lap, best_ms), lap.id)

        self._loading = False
        self._on_lap_changed()

    def _on_lap_changed(self) -> None:
        if self._loading:
            return
        self.lap_changed.emit(self.current_lap_id())

    # ---------- leitura ----------

    def current_track_id(self) -> int | None:
        data = self._track_combo.currentData()
        return int(data) if data is not None else None

    def current_lap_id(self) -> int | None:
        data = self._lap_combo.currentData()
        return int(data) if data is not None else None

    def select_track(self, track_id: object) -> None:
        """Aponta para uma pista de fora. Usado pelo seletor que não a mostra."""
        if track_id is None:
            return
        index = self._track_combo.findData(track_id)
        if index >= 0 and index != self._track_combo.currentIndex():
            self._track_combo.setCurrentIndex(index)

    def select_lap(self, lap_id: int) -> bool:
        index = self._lap_combo.findData(lap_id)
        if index < 0:
            return False
        self._lap_combo.setCurrentIndex(index)
        return True

    @property
    def has_laps(self) -> bool:
        return self._lap_combo.count() > 0
