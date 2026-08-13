"""
Estado da tela de telemetria detalhada de uma volta.
"""

from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

from ...domain.config import AppConfig
from ...domain.interfaces.lap_repository import LapRepository
from ...domain.models.lap import Lap
from ...domain.services.lap_analysis import LapSeries, sector_boundaries_m
from ..events.event_bus import EventBus
from ..events.events import LapCompleted, LapDeleted, LapsPurged

NUM_SECTORS = 3

# Valor bruto de slip a partir do qual o índice satura em 100 %.
#
# O campo `tire_slip` do GT7 é uma **razão** entre a velocidade da roda e a do
# solo — não um ângulo. A versão anterior multiplicava esse número por 12 e
# rotulava o resultado como "graus", produzindo uma unidade física que não
# existe: decisão de acerto de carro tomada em cima de número inventado.
#
# Agora o valor é apresentado como índice de 0 a 100 %, sem unidade falsa. 1.0
# como teto porque é onde a roda gira ao dobro da velocidade do solo — bem
# além de qualquer deslizamento útil de se ler.
SLIP_SATURATION = 1.0

# Faixas de leitura do índice, usadas pelo indicador e pela legenda.
SLIP_STABLE_PCT = 30.0
SLIP_MODERATE_PCT = 60.0

AXIS_DISTANCE = "distance"
AXIS_TIME = "time"

# Acima disto, plotar ponto a ponto não acrescenta nada: não há pixel na tela
# para distinguir, e o custo de montar a série cresce linearmente. Uma volta de
# 90 s a 60 Hz já passa de 5.000 amostras.
MAX_PLOT_POINTS = 2000


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


def slip_index_pct(slip_value: float, saturation: float = SLIP_SATURATION) -> float:
    """Valor bruto de slip → índice de 0 a 100 %.

    Sem conversão para graus: o dado de origem é uma razão, e transformá-lo em
    ângulo exigiria a orientação do carro, que o pacote não traz. O índice
    serve para o que interessa na prática — comparar rodas entre si e voltas
    entre si — sem afirmar uma unidade física que não se sustenta.
    """
    return min(abs(slip_value) / saturation, 1.0) * 100.0


def slip_level_label(pct: float) -> str:
    """Faixa de leitura do índice, em texto."""
    if pct < SLIP_STABLE_PCT:
        return "Aderência estável"
    if pct < SLIP_MODERATE_PCT:
        return "Deslizamento moderado"
    return "Perda de aderência"


def resample(points: list[tuple[float, float]], limit: int = MAX_PLOT_POINTS):
    """Reduz a série para no máximo `limit` pontos, preservando as pontas.

    Passo constante em vez de média: o que interessa nestes gráficos são os
    picos (frenagem máxima, ápice de curva), e média os apagaria. O último
    ponto entra sempre, para o gráfico terminar onde a volta termina.
    """
    n = len(points)
    if n <= limit:
        return points
    step = n / limit
    out = [points[int(i * step)] for i in range(limit)]
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


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
        track_repository=None,
        config: AppConfig | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._laps = lap_repository
        self._bus = event_bus
        self._config = config or AppConfig()
        # Opcional: sem ele os setores caem na divisão em partes iguais.
        self._tracks = track_repository
        self._track_id: int | None = None
        self._detail = LapDetail()
        self._axis_mode = AXIS_DISTANCE
        self._series_cache: dict[tuple[str, str], list] = {}
        self._tank_capacity: float | None = None

        # A lista precisa reagir aos três eventos que mudam o conjunto de
        # voltas. Assinar só `LapCompleted` deixava o seletor oferecendo voltas
        # já excluídas: escolher uma delas simplesmente não desenhava nada.
        self._on_laps_changed = lambda _e: self._reload_after_change()
        for event_type in (LapCompleted, LapDeleted, LapsPurged):
            self._bus.subscribe(event_type, self._on_laps_changed)

    def _reload_after_change(self) -> None:
        """Recarrega a lista e larga o detalhe se a volta aberta sumiu."""
        self.refresh_lap_list()
        lap = self._detail.lap
        if lap is not None and lap.id is not None:
            if self._laps.get_by_id(lap.id) is None:
                self._detail = LapDetail()
                self._series_cache.clear()

    def dispose(self) -> None:
        """Cancela as inscrições no barramento.

        Hoje as abas são construídas uma vez só, então nada vaza. Vira
        vazamento no dia em que uma aba for reconstruída — e sem um ponto de
        saída explícito, esse dia chega como bug difícil de achar.
        """
        for event_type in (LapCompleted, LapDeleted, LapsPurged):
            self._bus.unsubscribe(event_type, self._on_laps_changed)
        self._series_cache.clear()

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
        # O cache é indexado por (canal, eixo), então trocar de eixo não
        # invalida as séries já montadas do outro.
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

        self._series_cache.clear()
        self._detail = LapDetail(
            lap=lap,
            series=series,
            sector_boundaries=self._boundaries_for(series),
            axis_mode=self._axis_mode,
        )
        self.detail_ready.emit(self._detail)

    def _boundaries_for(self, series: LapSeries) -> list[float]:
        """Limites de setor da volta, respeitando o ajuste da pista.

        Quando a pista tem frações configuradas, os cortes seguem o traçado
        real em vez de dividir a volta em partes iguais.
        """
        track = None
        if self._tracks is not None and self._track_id is not None:
            track = self._tracks.get_by_id(self._track_id)
        if track is not None and track.sector_fractions:
            return [series.max_distance * f for f in track.sector_fractions]
        return sector_boundaries_m(series.max_distance, self._config.num_sectors)

    # ---------- séries para os gráficos ----------

    def points_for(self, channel: str) -> list[tuple[float, float]]:
        """Pares (x, y) de um canal, no eixo ativo, prontos para plotar.

        Centralizar a escolha de eixo aqui evita que cada gráfico da View
        repita o `if modo == tempo` — na versão antiga essa decisão estava
        espalhada por dezenas de chamadas.

        A série sai reamostrada e cacheada: são 18 gráficos na tela, e cada
        um pedindo milhares de pontos crus tornava a troca de eixo lenta.
        """
        if not self._detail.is_valid:
            return []
        key = (channel, self._axis_mode)
        cached = self._series_cache.get(key)
        if cached is not None:
            return cached

        series = self._detail.series
        if not series.has_channel(channel):
            self._series_cache[key] = []
            return []
        raw = (
            series.points_by_time(channel)
            if self._axis_mode == AXIS_TIME
            else series.points(channel)
        )
        out = resample(raw, self._config.max_plot_points)
        self._series_cache[key] = out
        return out

    def slip_points(self, channel: str) -> list[tuple[float, float]]:
        """Série de um canal de slip como índice de 0 a 100 %."""
        saturacao = self._config.slip_saturation
        return [
            (x, slip_index_pct(v, saturacao)) for x, v in self.points_for(channel)
        ]

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
        """Índice de deslizamento médio da volta, 0-100 %."""
        if not self._detail.is_valid:
            return 0.0
        points = self._detail.series.points_raw
        if not points:
            return 0.0
        saturacao = self._config.slip_saturation
        return sum(
            slip_index_pct(p.tire_slip_avg, saturacao) for p in points
        ) / len(points)

    def fuel_used_pct(self) -> float | None:
        """Combustível gasto na volta, em % do tanque.

        Percentual e não valor bruto: o painel ao vivo já mostra o nível em %,
        e ter as duas telas em unidades diferentes fazia o resumo exibir um
        número sem significado claro. Sem a capacidade do tanque (que só chega
        no pacote ao vivo), não há como converter — daí o None.
        """
        if not self._detail.is_valid or self._tank_capacity is None:
            return None
        used = self._detail.lap.fuel_used
        if used is None or self._tank_capacity <= 0:
            return None
        return used / self._tank_capacity * 100.0

    def set_tank_capacity(self, capacity: float | None) -> None:
        """Capacidade do tanque, vinda da telemetria ao vivo."""
        self._tank_capacity = capacity if capacity and capacity > 0 else None

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
