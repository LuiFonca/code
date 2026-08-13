"""
Estado da tela de comparação entre duas voltas.
"""

from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

from ...domain.interfaces.lap_repository import LapRepository
from ...domain.models.lap import Lap
from ...domain.services.lap_analysis import (
    LapSeries,
    best_combined_sectors,
    compute_delta_series,
    sector_boundaries_m,
    sector_times_from_series,
)
from ..events.event_bus import EventBus
from ..events.events import LapCompleted

NUM_SECTORS = 3


@dataclass(slots=True)
class SectorComparison:
    """Um setor, lado a lado nas duas voltas."""

    index: int
    time_a: int | None
    time_b: int | None

    @property
    def delta_ms(self) -> int | None:
        if self.time_a is None or self.time_b is None:
            return None
        return self.time_b - self.time_a

    @property
    def winner(self) -> str | None:
        """'A', 'B', ou None quando falta dado num dos lados."""
        if self.time_a is None or self.time_b is None:
            return None
        return "A" if self.time_a <= self.time_b else "B"


@dataclass(slots=True)
class ComparisonResult:
    """Tudo que a View precisa para desenhar a comparação, já calculado."""

    lap_a: Lap | None = None
    lap_b: Lap | None = None
    series_a: LapSeries | None = None
    series_b: LapSeries | None = None
    delta_points: list[tuple[float, float]] = field(default_factory=list)
    sectors: list[SectorComparison] = field(default_factory=list)
    theoretical_best_ms: int | None = None
    sector_boundaries: list[float] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return self.series_a is not None and self.series_b is not None


class ComparisonViewModel(QObject):
    """Compara duas voltas e entrega séries prontas para os gráficos."""

    laps_available = Signal(list)     # list[Lap] para popular os seletores
    comparison_ready = Signal(object)  # ComparisonResult
    error = Signal(str)

    def __init__(
        self,
        lap_repository: LapRepository,
        event_bus: EventBus,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._laps = lap_repository
        self._bus = event_bus
        self._track_id: int | None = None
        self._result = ComparisonResult()

        self._bus.subscribe(LapCompleted, lambda _e: self.refresh_lap_list())

    @property
    def result(self) -> ComparisonResult:
        return self._result

    def set_track(self, track_id: int | None) -> None:
        self._track_id = track_id
        self._result = ComparisonResult()
        self.refresh_lap_list()

    def refresh_lap_list(self) -> None:
        if self._track_id is None:
            self.laps_available.emit([])
            return
        self.laps_available.emit(self._laps.get_by_track(self._track_id))

    def compare(self, lap_id_a: int, lap_id_b: int) -> None:
        """Monta a comparação entre duas voltas."""
        if lap_id_a == lap_id_b:
            self.error.emit("Selecione duas voltas diferentes para comparar.")
            return

        lap_a = self._laps.get_by_id(lap_id_a)
        lap_b = self._laps.get_by_id(lap_id_b)
        if lap_a is None or lap_b is None:
            self.error.emit("Não foi possível carregar uma das voltas.")
            return

        series_a = LapSeries(lap_a.points)
        series_b = LapSeries(lap_b.points)
        if series_a.is_empty or series_b.is_empty:
            self.error.emit("Uma das voltas não tem amostras suficientes.")
            return

        # Os limites de setor saem do trecho comum às duas voltas: usar a
        # distância de cada uma separadamente faria "setor 2" cair em pontos
        # físicos diferentes, e a comparação perderia o sentido.
        reference_distance = min(series_a.max_distance, series_b.max_distance)
        boundaries = sector_boundaries_m(reference_distance, NUM_SECTORS)

        times_a = sector_times_from_series(series_a, boundaries)
        times_b = sector_times_from_series(series_b, boundaries)
        theoretical, _choices = best_combined_sectors(times_a, times_b)

        self._result = ComparisonResult(
            lap_a=lap_a,
            lap_b=lap_b,
            series_a=series_a,
            series_b=series_b,
            delta_points=compute_delta_series(series_a, series_b),
            sectors=[
                SectorComparison(index=i, time_a=ta, time_b=tb)
                for i, (ta, tb) in enumerate(zip(times_a, times_b))
            ],
            theoretical_best_ms=theoretical,
            sector_boundaries=boundaries,
        )
        self.comparison_ready.emit(self._result)

    def values_at(self, distance_m: float) -> dict:
        """Valores dos dois lados numa distância — alimenta o painel que segue
        o cursor sobre os gráficos."""
        if not self._result.is_valid:
            return {}
        channels = ("speed_kmh", "throttle", "brake", "gear", "rpm", "fuel_level")
        return {
            ch: (
                self._result.series_a.value_at(distance_m, ch),
                self._result.series_b.value_at(distance_m, ch),
            )
            for ch in channels
        }
