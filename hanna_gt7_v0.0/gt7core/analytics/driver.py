"""
Perfil estatístico do piloto — §16 do briefing.

Os outros módulos analisam **uma volta**. Este analisa **um piloto**: o que ele
faz de forma consistente, volta após volta, e que portanto é característica dele
e não acaso de uma tentativa.

A diferença importa na prática. Uma frenagem tardia numa volta é um evento; a
mesma frenagem tardia em quinze voltas é um hábito, e hábito é o que vale a pena
treinar. Por isso quase tudo aqui é uma média ou um desvio sobre a janela de
voltas — que é exatamente a janela de retenção por pista (20 voltas recentes),
escolhida na Fase 3.

Onde este módulo se recusa a responder
--------------------------------------
Com poucas voltas, as estatísticas existem mas não significam nada. Em vez de
devolver um número frágil com cara de certeza, `DriverProfile.is_reliable`
marca o perfil como preliminar e os textos passam a dizer isso. Um perfil que
afirma "você é inconsistente" a partir de duas voltas está errado mesmo quando
o desvio padrão está certo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..domain.models import TelemetryPoint
from .braking import detect_braking_zones
from .corners import Corner, detect_corners
from .throttle import analyse_throttle
from .tyres import TyreEvent, detect_tyre_events, infer_slip_convention

# Abaixo disto o perfil é preliminar: as médias existem, mas o desvio padrão de
# três amostras não descreve um piloto.
MIN_LAPS_FOR_PROFILE = 5

# Desvio padrão do tempo de volta que separa consistente de irregular, em ms.
# Referência prática de carro de rua em circuito; a interface mostra o número
# junto do rótulo para que o piloto julgue por si.
CONSISTENT_STDDEV_MS = 500.0
ERRATIC_STDDEV_MS = 1500.0


@dataclass(frozen=True, slots=True)
class DriverProfile:
    """Retrato do piloto sobre uma janela de voltas da mesma pista."""

    lap_count: int
    best_lap_ms: int
    median_lap_ms: int
    lap_time_stddev_ms: float

    braking_point_stddev_m: float | None
    """Repetibilidade do ponto de frenagem. Menor é melhor."""

    average_trail_braking: float
    """Média do `trail_braking_ratio` em todas as frenagens da janela."""

    average_throttle_delay_m: float | None
    """Distância média entre o ápice e a retomada de acelerador."""

    lockups_per_lap: float
    wheelspins_per_lap: float
    lifts_per_lap: float

    pace_trend_ms_per_lap: float
    """Inclinação do tempo de volta na janela. Negativa = melhorando."""

    @property
    def is_reliable(self) -> bool:
        return self.lap_count >= MIN_LAPS_FOR_PROFILE

    @property
    def consistency_label(self) -> str:
        if self.lap_time_stddev_ms <= CONSISTENT_STDDEV_MS:
            return "consistente"
        if self.lap_time_stddev_ms <= ERRATIC_STDDEV_MS:
            return "regular"
        return "irregular"

    @property
    def braking_style(self) -> str:
        """Estilo de frenagem pela progressividade da liberação."""
        if self.average_trail_braking >= 0.55:
            return "trail braking acentuado"
        if self.average_trail_braking >= 0.25:
            return "trail braking moderado"
        return "frenagem em linha reta"

    @property
    def error_rate_per_lap(self) -> float:
        return self.lockups_per_lap + self.wheelspins_per_lap

    def strengths(self) -> list[str]:
        found: list[str] = []
        if self.lap_time_stddev_ms <= CONSISTENT_STDDEV_MS:
            found.append(
                f"consistência: desvio de {self.lap_time_stddev_ms / 1000:.3f} s "
                "entre voltas"
            )
        if self.braking_point_stddev_m is not None and self.braking_point_stddev_m < 8:
            found.append(
                f"referências de frenagem estáveis (±{self.braking_point_stddev_m:.1f} m)"
            )
        if self.average_trail_braking >= 0.4:
            found.append("usa o freio para girar o carro, não só para reduzir")
        if self.error_rate_per_lap < 0.5:
            found.append("pouquíssima perda de aderência")
        if self.pace_trend_ms_per_lap < -50:
            found.append(
                f"evoluindo {abs(self.pace_trend_ms_per_lap) / 1000:.3f} s por volta"
            )
        return found

    def weaknesses(self) -> list[str]:
        found: list[str] = []
        if self.lap_time_stddev_ms > ERRATIC_STDDEV_MS:
            found.append(
                f"inconsistência: {self.lap_time_stddev_ms / 1000:.3f} s de desvio "
                "entre voltas"
            )
        if self.braking_point_stddev_m is not None and self.braking_point_stddev_m > 20:
            found.append(
                f"ponto de frenagem varia ±{self.braking_point_stddev_m:.0f} m — "
                "faltam referências visuais fixas"
            )
        if self.average_trail_braking < 0.2:
            found.append(
                "solta o freio de uma vez; trail braking daria mais rotação na entrada"
            )
        if self.wheelspins_per_lap >= 1:
            found.append(
                f"{self.wheelspins_per_lap:.1f} patinagem(ns) por volta — "
                "acelerador aberto antes do carro aceitar"
            )
        if self.lockups_per_lap >= 1:
            found.append(f"{self.lockups_per_lap:.1f} travamento(s) por volta")
        if self.lifts_per_lap >= 1:
            found.append(
                f"{self.lifts_per_lap:.1f} alívio(s) de acelerador por volta na saída"
            )
        return found

    def summary(self) -> str:
        header = (
            f"Perfil sobre {self.lap_count} volta(s)"
            + ("" if self.is_reliable else " — preliminar, poucas voltas")
        )
        lines = [
            header,
            f"  Ritmo: melhor {_format_ms(self.best_lap_ms)}, "
            f"mediana {_format_ms(self.median_lap_ms)} ({self.consistency_label})",
            f"  Frenagem: {self.braking_style}",
        ]
        for note in self.strengths():
            lines.append(f"  + {note}")
        for note in self.weaknesses():
            lines.append(f"  - {note}")
        return "\n".join(lines)


def build_profile(laps: list[list[TelemetryPoint]]) -> DriverProfile | None:
    """Monta o perfil a partir das voltas informadas, em ordem cronológica.

    Devolve None sem nenhuma volta utilizável. Uma volta só já produz perfil —
    marcado como não confiável —, porque é melhor mostrar algo honesto e
    rotulado do que uma tela vazia depois da primeira volta de uma sessão.
    """
    usable = [lap for lap in laps if len(lap) >= 2]
    if not usable:
        return None

    lap_times = [lap[-1].elapsed_ms for lap in usable]

    trail_ratios: list[float] = []
    braking_starts: list[list[float]] = []
    throttle_delays: list[float] = []
    lockups = 0
    wheelspins = 0
    lifts = 0

    for lap in usable:
        zones = detect_braking_zones(lap)
        trail_ratios.extend(zone.trail_braking_ratio for zone in zones)
        braking_starts.append([zone.start_distance_m for zone in zones])

        corners: list[Corner] = detect_corners(lap)
        convention = infer_slip_convention(lap)
        applications = analyse_throttle(lap, corners, convention=convention)
        throttle_delays.extend(a.delay_from_apex_m for a in applications)
        lifts += sum(a.lift_count for a in applications)

        events = detect_tyre_events(lap, convention=convention)
        lockups += _incident_count(events, "travamento")
        wheelspins += _incident_count(events, "patinagem")

    count = len(usable)
    return DriverProfile(
        lap_count=count,
        best_lap_ms=min(lap_times),
        median_lap_ms=_median(lap_times),
        lap_time_stddev_ms=_stddev([float(t) for t in lap_times]),
        braking_point_stddev_m=_braking_repeatability(braking_starts),
        average_trail_braking=(
            sum(trail_ratios) / len(trail_ratios) if trail_ratios else 0.0
        ),
        average_throttle_delay_m=(
            sum(throttle_delays) / len(throttle_delays) if throttle_delays else None
        ),
        lockups_per_lap=lockups / count,
        wheelspins_per_lap=wheelspins / count,
        lifts_per_lap=lifts / count,
        pace_trend_ms_per_lap=_slope([float(t) for t in lap_times]),
    )


def _incident_count(events: list[TyreEvent], kind: str) -> int:
    """Quantos **incidentes** houve, não quantas rodas foram afetadas.

    `detect_tyre_events` é por roda de propósito: travar só a dianteira
    esquerda é um diagnóstico diferente de travar as duas, e a detecção não
    pode apagar essa distinção. Mas o perfil do piloto conta ocorrências, e
    somar eventos aqui dobra o número toda vez que as duas rodas de um eixo
    travam juntas — que é o caso normal numa frenagem em linha reta.

    O sintoma que revelou isto: uma volta com quatro frenagens saía do perfil
    como "8 travamentos por volta". Errado por um fator de dois, e alarmante
    para quem lê. Pior ainda a partir da Fase 7, porque esse número vai no
    prompt do engenheiro — que foi instruído a nunca inventar grandeza e
    repetiria fielmente a inflação.

    Eventos que se sobrepõem em distância são o mesmo incidente visto por rodas
    diferentes.
    """
    spans = sorted(
        (event.start_distance_m, event.end_distance_m)
        for event in events
        if event.kind == kind
    )

    incidents = 0
    current_end = float("-inf")
    for start, end in spans:
        if start > current_end:
            incidents += 1
            current_end = end
        else:
            current_end = max(current_end, end)
    return incidents


def _braking_repeatability(starts_per_lap: list[list[float]]) -> float | None:
    """Desvio padrão médio do ponto de frenagem entre voltas.

    Só considera voltas com o mesmo número de frenagens: comparar índice a
    índice quando uma volta teve uma freada a mais produziria um desvio enorme
    que não reflete inconsistência nenhuma, só o desalinhamento.
    """
    if len(starts_per_lap) < 2:
        return None

    counts = [len(starts) for starts in starts_per_lap]
    if not counts:
        return None

    # A contagem mais comum é a "normal" da sessão; voltas fora dela são
    # descartadas do cálculo em vez de contaminá-lo.
    common = max(set(counts), key=counts.count)
    if common == 0:
        return None

    aligned = [starts for starts in starts_per_lap if len(starts) == common]
    if len(aligned) < 2:
        return None

    deviations = [
        _stddev([starts[index] for starts in aligned]) for index in range(common)
    ]
    return sum(deviations) / len(deviations)


def _median(values: list[int]) -> int:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def _slope(values: list[float]) -> float:
    count = len(values)
    if count < 2:
        return 0.0
    mean_x = (count - 1) / 2.0
    mean_y = sum(values) / count
    numerator = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    denominator = sum((i - mean_x) ** 2 for i in range(count))
    return numerator / denominator if denominator else 0.0


def _format_ms(total_ms: int) -> str:
    minutes, remainder = divmod(total_ms, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{minutes}:{seconds:02d}.{millis:03d}"
