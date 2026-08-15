"""
Análise de frenagem — §13 do briefing.

Mede o que um engenheiro de pista mede: onde o piloto pisa, com que pressão,
por quanto tempo, e se solta o freio progressivamente (trail braking) ou de uma
vez. Depois compara com a referência e diz o que mudou.

Todas as funções são puras sobre uma lista de amostras. Nenhuma toca banco,
rede ou interface — é o que permite testá-las com uma volta sintética e nada
mais.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.models import TelemetryPoint
from .matching import match_by_distance

# Abaixo disto é folga de pedal ou toque involuntário, não frenagem.
BRAKE_THRESHOLD_PCT = 5.0

# Uma zona de frenagem precisa durar o mínimo para não ser um toque de
# estabilização no meio da reta.
MIN_ZONE_DURATION_MS = 150


@dataclass(frozen=True, slots=True)
class BrakingZone:
    """Uma aplicação contínua de freio."""

    start_distance_m: float
    end_distance_m: float
    start_time_ms: int
    end_time_ms: int

    entry_speed_kmh: float
    exit_speed_kmh: float
    max_pressure_pct: float
    average_pressure_pct: float

    trail_braking_ratio: float
    """Fração da zona em que o freio foi solto progressivamente.

    Perto de 1 significa liberação suave até a entrada da curva; perto de 0,
    freio mantido e solto de uma vez. É o indicador que separa um piloto que
    usa o freio para girar o carro de um que só reduz velocidade em linha reta.
    """

    @property
    def duration_ms(self) -> int:
        return self.end_time_ms - self.start_time_ms

    @property
    def distance_m(self) -> float:
        return self.end_distance_m - self.start_distance_m

    @property
    def speed_drop_kmh(self) -> float:
        return self.entry_speed_kmh - self.exit_speed_kmh

    @property
    def average_deceleration_g(self) -> float:
        """Desaceleração média em g, derivada da queda de velocidade."""
        seconds = self.duration_ms / 1000.0
        if seconds <= 0:
            return 0.0
        return (self.speed_drop_kmh / 3.6) / seconds / 9.81


def detect_braking_zones(
    points: list[TelemetryPoint],
    *,
    threshold_pct: float = BRAKE_THRESHOLD_PCT,
    min_duration_ms: int = MIN_ZONE_DURATION_MS,
) -> list[BrakingZone]:
    """Encontra as zonas de frenagem contínuas de uma volta."""
    zones: list[BrakingZone] = []
    start_index: int | None = None

    for index, point in enumerate(points):
        braking = point.brake > threshold_pct

        if braking and start_index is None:
            start_index = index
        elif not braking and start_index is not None:
            zone = _build_zone(points, start_index, index - 1, min_duration_ms)
            if zone is not None:
                zones.append(zone)
            start_index = None

    # Zona que ainda estava aberta quando a volta acabou.
    if start_index is not None:
        zone = _build_zone(points, start_index, len(points) - 1, min_duration_ms)
        if zone is not None:
            zones.append(zone)

    return zones


def _build_zone(
    points: list[TelemetryPoint], start: int, end: int, min_duration_ms: int
) -> BrakingZone | None:
    if end <= start:
        return None

    segment = points[start : end + 1]
    duration = segment[-1].elapsed_ms - segment[0].elapsed_ms
    if duration < min_duration_ms:
        return None

    pressures = [p.brake for p in segment]
    peak_index = pressures.index(max(pressures))

    # Trail braking: depois do pico, quantas amostras seguem reduzindo a
    # pressão. Liberação monotônica dá 1.0; freio constante até soltar dá ~0.
    after_peak = pressures[peak_index:]
    if len(after_peak) > 1:
        decreasing = sum(
            1 for a, b in zip(after_peak, after_peak[1:], strict=False) if b < a
        )
        trail_ratio = decreasing / (len(after_peak) - 1)
    else:
        trail_ratio = 0.0

    return BrakingZone(
        start_distance_m=segment[0].distance_m,
        end_distance_m=segment[-1].distance_m,
        start_time_ms=segment[0].elapsed_ms,
        end_time_ms=segment[-1].elapsed_ms,
        entry_speed_kmh=segment[0].speed_kmh,
        exit_speed_kmh=segment[-1].speed_kmh,
        max_pressure_pct=max(pressures),
        average_pressure_pct=sum(pressures) / len(pressures),
        trail_braking_ratio=trail_ratio,
    )


@dataclass(frozen=True, slots=True)
class BrakingComparison:
    """Diferença de frenagem entre a volta analisada e a referência."""

    reference: BrakingZone
    analysed: BrakingZone | None

    @property
    def brake_point_delta_m(self) -> float | None:
        """Positivo = freou **depois** da referência (mais tarde)."""
        if self.analysed is None:
            return None
        return self.analysed.start_distance_m - self.reference.start_distance_m

    @property
    def pressure_delta_pct(self) -> float | None:
        if self.analysed is None:
            return None
        return self.analysed.max_pressure_pct - self.reference.max_pressure_pct

    @property
    def minimum_speed_delta_kmh(self) -> float | None:
        if self.analysed is None:
            return None
        return self.analysed.exit_speed_kmh - self.reference.exit_speed_kmh

    def describe(self) -> str:
        """Diagnóstico em texto, no vocabulário do §13.

        Os limiares vêm da prática: menos de 5 m de diferença no ponto de
        frenagem está dentro da variação normal de uma volta para outra, e
        apontar isso como erro geraria ruído em vez de informação.
        """
        if self.analysed is None:
            return "sem frenagem correspondente nesta volta"

        notes: list[str] = []
        delta_m = self.brake_point_delta_m or 0.0
        if delta_m < -5:
            notes.append(f"freou {abs(delta_m):.0f} m mais cedo")
        elif delta_m > 5:
            notes.append(f"freou {delta_m:.0f} m mais tarde")

        pressure = self.pressure_delta_pct or 0.0
        if pressure < -8:
            notes.append(f"{abs(pressure):.0f}% menos pressão")
        elif pressure > 8:
            notes.append(f"{pressure:.0f}% mais pressão")

        trail = self.analysed.trail_braking_ratio - self.reference.trail_braking_ratio
        if trail < -0.2:
            notes.append("liberação do freio menos progressiva")

        speed = self.minimum_speed_delta_kmh or 0.0
        if speed < -3:
            notes.append(f"{abs(speed):.0f} km/h a menos na saída da freada")

        return "; ".join(notes) if notes else "frenagem equivalente à referência"


def compare_braking(
    reference: list[TelemetryPoint],
    analysed: list[TelemetryPoint],
    *,
    tolerance_m: float = 150.0,
) -> list[BrakingComparison]:
    """Casa as zonas de frenagem de duas voltas **por distância**.

    Casar por índice quebraria assim que uma volta tivesse uma frenagem a mais
    ou a menos — e é justamente a volta atípica que mais interessa analisar.
    """
    pairs = match_by_distance(
        detect_braking_zones(reference),
        detect_braking_zones(analysed),
        reference_key=lambda z: z.start_distance_m,
        candidate_key=lambda z: z.start_distance_m,
        tolerance_m=tolerance_m,
    )
    return [BrakingComparison(reference=zone, analysed=matched) for zone, matched in pairs]


def braking_consistency(laps: list[list[TelemetryPoint]]) -> float | None:
    """Consistência do ponto de frenagem entre várias voltas, em metros.

    Devolve o desvio padrão médio dos pontos de frenagem: quanto menor, mais
    repetível o piloto. None com menos de duas voltas comparáveis.

    Mede repetibilidade, não acerto: um piloto pode ser consistentemente tarde
    demais. Por isso o número anda junto da comparação com a referência, nunca
    sozinho.
    """
    if len(laps) < 2:
        return None

    zones_per_lap = [detect_braking_zones(lap) for lap in laps]
    zone_counts = {len(z) for z in zones_per_lap}
    if len(zone_counts) != 1 or zone_counts == {0}:
        # Número diferente de frenagens entre voltas: comparar índice a índice
        # daria um número sem significado.
        return None

    deviations: list[float] = []
    for zone_index in range(len(zones_per_lap[0])):
        starts = [lap_zones[zone_index].start_distance_m for lap_zones in zones_per_lap]
        mean = sum(starts) / len(starts)
        variance = sum((s - mean) ** 2 for s in starts) / len(starts)
        deviations.append(variance**0.5)

    return sum(deviations) / len(deviations)
