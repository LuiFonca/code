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

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from gt7core.domain.models import Lap
from gt7core.storage.repositories import SqliteLapRepository, SqliteTrackRepository

from ..design.tokens import Space


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
    ) -> None:
        super().__init__()
        self._tracks = tracks
        self._laps = laps
        self._limit = limit
        self._loading = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.SM.px)

        self._track_combo = QComboBox()
        self._lap_combo = QComboBox()

        layout.addWidget(QLabel("Pista:"))
        layout.addWidget(self._track_combo)
        layout.addWidget(QLabel(lap_label))
        layout.addWidget(self._lap_combo)

        self._track_combo.currentIndexChanged.connect(self._on_track_changed)
        self._lap_combo.currentIndexChanged.connect(self._on_lap_changed)

    # ---------- carga ----------

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

    def select_lap(self, lap_id: int) -> bool:
        index = self._lap_combo.findData(lap_id)
        if index < 0:
            return False
        self._lap_combo.setCurrentIndex(index)
        return True

    @property
    def has_laps(self) -> bool:
        return self._lap_combo.count() > 0
