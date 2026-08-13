"""
Estado da tela de telemetria detalhada de uma volta.
"""

from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

from ...domain.interfaces.lap_repository import LapRepository
from ...domain.models.lap import Lap
from ...domain.services.lap_analysis import LapSeries, sector_boundaries_m
from ..events.event_bus import EventBus
from ..events.events import LapCompleted

NUM_SECTORS = 3

# Ângulo de deriva máximo assumido ao converter o valor bruto de slip do jogo.
# O GT7 não transmite ângulo em graus; o campo é adimensional e esta constante
# o traduz para uma escala interpretável. É uma aproximação para leitura
# comparativa entre rodas e entre voltas, não uma medida física exata.
SLIP_ANGLE_MAX_DEG = 12.0

AXIS_DISTANCE = "distance"
AXIS_TIME = "time"


@dataclass(slots=True)
class LapDetail:
    """Uma volta carregada e pronta para os gráficos."""

    lap: Lap | None = None
    series: LapSeries | None = None
    sector_boundaries: list[float] = field(default_factory=list)
    axis_mode: str = AXIS_DISTANCE

    @property
    def is_valid(self) -> bool:
        return self.series is not None and not self.series.is_empty


def estimate_slip_angle_deg(slip_value: float) -> float:
    """Valor bruto de slip → graus, saturando no máximo assumido."""
    return min(abs(slip_value) * SLIP_ANGLE_MAX_DEG, SLIP_ANGLE_MAX_DEG)


def normalize_slip_pct(slip_value: float) -> float:
    """Valor bruto de slip → 0-100%, para o indicador visual."""
    return min(abs(slip_value) * 100.0, 100.0)


class TelemetryViewModel(QObject):
    """Detalhe de uma volta: canais, eixo distância/tempo e mosaicos por roda."""

    laps_available = Signal(list)
    detail_ready = Signal(object)   # LapDetail
    axis_changed = Signal(str)
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
        self._detail = LapDetail()
        self._axis_mode = AXIS_DISTANCE

        self._bus.subscribe(LapCompleted, lambda _e: self.refresh_lap_list())

    @property
    def detail(self) -> LapDetail:
        return self._detail

    @property
    def axis_mode(self) -> str:
        return self._axis_mode

    def set_track(self, track_id: int | None) -> None:
        self._track_id = track_id
        self._detail = LapDetail()
        self.refresh_lap_list()

    def refresh_lap_list(self) -> None:
        if self._track_id is None:
            self.laps_available.emit([])
            return
        self.laps_available.emit(self._laps.get_by_track(self._track_id))

    def set_axis_mode(self, mode: str) -> None:
        """Alterna entre eixo de distância e de tempo.

        Os dois respondem perguntas diferentes: distância mostra *onde* na pista
        algo aconteceu (e é o único que permite comparar voltas), tempo mostra
        *quando* dentro da volta. Nenhum substitui o outro.
        """
        if mode not in (AXIS_DISTANCE, AXIS_TIME) or mode == self._axis_mode:
            return
        self._axis_mode = mode
        self._detail.axis_mode = mode
        self.axis_changed.emit(mode)
        if self._detail.is_valid:
            self.detail_ready.emit(self._detail)

    def load_lap(self, lap_id: int) -> None:
        lap = self._laps.get_by_id(lap_id)
        if lap is None:
            self.error.emit("Volta não encontrada.")
            return

        series = LapSeries(lap.points)
        if series.is_empty:
            self.error.emit("Esta volta não tem amostras suficientes para exibir.")
            return

        self._detail = LapDetail(
            lap=lap,
            series=series,
            sector_boundaries=sector_boundaries_m(series.max_distance, NUM_SECTORS),
            axis_mode=self._axis_mode,
        )
        self.detail_ready.emit(self._detail)

    # ---------- séries para os gráficos ----------

    def points_for(self, channel: str) -> list[tuple[float, float]]:
        """Pares (x, y) de um canal, no eixo ativo.

        Centralizar a escolha de eixo aqui evita que cada gráfico da View
        repita o `if modo == tempo` — na versão antiga essa decisão estava
        espalhada por dezenas de chamadas.
        """
        if not self._detail.is_valid:
            return []
        series = self._detail.series
        if not series.has_channel(channel):
            return []
        if self._axis_mode == AXIS_TIME:
            return series.points_by_time(channel)
        return series.points(channel)

    def slip_angle_points(self, channel: str) -> list[tuple[float, float]]:
        """Série de um canal de slip já convertida para graus."""
        return [(x, estimate_slip_angle_deg(v)) for x, v in self.points_for(channel)]

    def has_channel(self, channel: str) -> bool:
        return self._detail.is_valid and self._detail.series.has_channel(channel)

    @property
    def axis_max(self) -> float:
        """Fim do eixo ativo — distância total ou duração da volta."""
        if not self._detail.is_valid:
            return 0.0
        series = self._detail.series
        return series.max_time if self._axis_mode == AXIS_TIME else series.max_distance

    def average_slip_pct(self) -> float:
        """Slip médio da volta, 0-100%, para o indicador central do mosaico."""
        if not self._detail.is_valid:
            return 0.0
        points = self._detail.series.points_raw
        if not points:
            return 0.0
        return sum(normalize_slip_pct(p.tire_slip_avg) for p in points) / len(points)

    def values_at(self, distance_m: float) -> dict[str, float | None]:
        """Valores de todos os canais exibidos numa distância — painel do cursor."""
        if not self._detail.is_valid:
            return {}
        channels = (
            "speed_kmh", "rpm", "gear", "throttle", "brake", "fuel_level",
            "g_lateral", "g_longitudinal",
            "tire_temp_fl", "tire_temp_fr", "tire_temp_rl", "tire_temp_rr",
            "tire_slip_fl", "tire_slip_fr", "tire_slip_rl", "tire_slip_rr",
        )
        return {ch: self._detail.series.value_at(distance_m, ch) for ch in channels}
