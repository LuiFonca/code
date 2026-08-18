"""
Onde a volta foi perdida — §20 e §31 do briefing.

Este é o módulo que responde à única pergunta que o piloto faz de verdade:
*"onde estou perdendo tempo?"*. Tudo o que veio antes — curvas, frenagem,
acelerador — existe para alimentar esta resposta.

Como o tempo é atribuído
------------------------
A volta é fatiada em segmentos ancorados nas curvas detectadas: cada curva é um
segmento (da entrada à saída) e o trecho entre duas curvas é outro. O tempo
perdido num segmento é a **variação do delta** entre o início e o fim dele —
não o delta absoluto.

A distinção é o ponto todo. O delta acumulado ao fim de um segmento inclui tudo
o que já se perdeu antes; usá-lo diretamente atribuiria a cada curva os erros
das anteriores, e o relatório apontaria sempre a última curva como a pior. A
variação isola o que aconteceu **dentro** daquele trecho.

Um segmento com variação negativa é um trecho onde o piloto **ganhou** tempo, e
o relatório mostra isso: saber onde se está indo bem tem valor, e um relatório
que só acusa erros é um relatório que se aprende a ignorar.

Sobre a causa
-------------
A causa atribuída a cada segmento é uma **hipótese ordenada por evidência**, não
um veredito. Perder tempo numa curva com frenagem tardia e velocidade mínima
baixa é compatível com "entrou demais"; também é compatível com uma marcha
errada. O texto diz o que foi medido e sugere a leitura mais provável, e é assim
que ele deve ser lido — o número é medido, a causa é inferida.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.models import TelemetryPoint
from .braking import BrakingComparison, compare_braking
from .corners import Corner, detect_corners, match_corners
from .series import LapSeries
from .throttle import ThrottleComparison, analyse_throttle, compare_throttle

# Abaixo disto a diferença está dentro da variação normal entre voltas de um
# mesmo piloto e apontá-la geraria ruído.
SIGNIFICANT_LOSS_MS = 30.0


@dataclass(frozen=True, slots=True)
class SegmentLoss:
    """Tempo ganho ou perdido num trecho da volta."""

    label: str
    """`"Curva 3"` ou `"Reta 2"` — rótulo para interface e para o relatório."""

    start_distance_m: float
    end_distance_m: float
    time_delta_ms: float
    """Positivo = perdeu tempo neste trecho; negativo = ganhou."""

    corner: Corner | None
    """A curva do segmento, quando ele é uma curva."""

    braking: BrakingComparison | None
    throttle: ThrottleComparison | None

    @property
    def is_loss(self) -> bool:
        return self.time_delta_ms > SIGNIFICANT_LOSS_MS

    @property
    def is_gain(self) -> bool:
        return self.time_delta_ms < -SIGNIFICANT_LOSS_MS

    def cause(self) -> str:
        """Hipótese de causa, montada a partir do que os detectores mediram."""
        if not self.is_loss:
            return ""

        evidence: list[str] = []
        if self.braking is not None and self.braking.analysed is not None:
            note = self.braking.describe()
            if note != "frenagem equivalente à referência":
                evidence.append(note)
        if self.throttle is not None and self.throttle.analysed is not None:
            note = self.throttle.describe()
            if note != "saída equivalente à referência":
                evidence.append(note)

        if not evidence:
            return "sem diferença clara de frenagem ou acelerador — velocidade de passagem"
        return "; ".join(evidence)

    def describe(self) -> str:
        seconds = self.time_delta_ms / 1000.0
        if self.is_gain:
            return f"{self.label}: {abs(seconds):.3f} s ganhos"
        if not self.is_loss:
            return f"{self.label}: equivalente"

        cause = self.cause()
        return f"{self.label}: {seconds:.3f} s perdidos — {cause}"


@dataclass(frozen=True, slots=True)
class TimeLossReport:
    """O resultado da comparação de duas voltas, segmento a segmento."""

    segments: list[SegmentLoss]
    total_delta_ms: float
    """Diferença total entre as duas voltas, positiva quando a analisada é mais
    lenta."""

    @property
    def losses(self) -> list[SegmentLoss]:
        """Só os trechos onde se perdeu tempo, do pior para o menos ruim."""
        return sorted(
            (s for s in self.segments if s.is_loss),
            key=lambda s: s.time_delta_ms,
            reverse=True,
        )

    @property
    def gains(self) -> list[SegmentLoss]:
        return sorted((s for s in self.segments if s.is_gain), key=lambda s: s.time_delta_ms)

    def worst(self, count: int = 3) -> list[SegmentLoss]:
        return self.losses[:count]

    @property
    def recoverable_ms(self) -> float:
        """Soma das perdas, ignorando os ganhos.

        É o teto realista de melhora: repetir nesta volta o que já se fez de
        melhor em cada trecho. Não promete uma volta ideal — promete a volta que
        o piloto já demonstrou saber fazer, pedaço por pedaço.
        """
        return sum(s.time_delta_ms for s in self.segments if s.is_loss)

    def summary(self) -> str:
        """Relatório curto, no formato que o §20 pede."""
        if not self.segments:
            return "sem trechos comparáveis entre as duas voltas"

        lines = [
            f"Diferença total: {self.total_delta_ms / 1000:+.3f} s "
            f"(recuperáveis: {self.recoverable_ms / 1000:.3f} s)"
        ]
        lines.extend(f"  {segment.describe()}" for segment in self.worst(3))
        best = self.gains[:1]
        if best:
            lines.append(f"  {best[0].describe()}")
        return "\n".join(lines)


def analyse_time_loss(
    reference: list[TelemetryPoint], analysed: list[TelemetryPoint]
) -> TimeLossReport:
    """Compara duas voltas e diz onde a diferença foi feita.

    `reference` é normalmente a melhor volta da pista; `analysed` é a volta que
    se quer entender. Voltas sem sobreposição de distância devolvem um relatório
    vazio, não um erro — comparar duas voltas de pistas diferentes é um engano do
    chamador, mas não deve derrubar a aplicação.
    """
    if len(reference) < 2 or len(analysed) < 2:
        return TimeLossReport(segments=[], total_delta_ms=0.0)

    reference_series = LapSeries(reference)
    analysed_series = LapSeries(analysed)

    reference_corners = detect_corners(reference)
    analysed_corners = detect_corners(analysed)

    braking = {
        comparison.reference.start_distance_m: comparison
        for comparison in compare_braking(reference, analysed)
    }
    throttle = {
        comparison.reference.apex_distance_m: comparison
        for comparison in compare_throttle(
            analyse_throttle(reference, reference_corners),
            analyse_throttle(analysed, analysed_corners),
        )
    }
    corner_match = dict(match_corners(reference_corners, analysed_corners))

    limit = min(reference_series.max_distance, analysed_series.max_distance)
    segments: list[SegmentLoss] = []

    for start, end, label, corner in _segment_bounds(reference_corners, limit):
        delta = _delta_change(reference_series, analysed_series, start, end)
        if delta is None:
            continue

        segments.append(
            SegmentLoss(
                label=label,
                start_distance_m=start,
                end_distance_m=end,
                time_delta_ms=delta,
                corner=corner_match.get(corner) if corner is not None else None,
                braking=_nearest(braking, start, end),
                throttle=(
                    throttle.get(corner.apex_distance_m) if corner is not None else None
                ),
            )
        )

    total = _delta_change(reference_series, analysed_series, 0.0, limit) or 0.0
    return TimeLossReport(segments=segments, total_delta_ms=total)


def _segment_bounds(
    corners: list[Corner], limit: float
) -> list[tuple[float, float, str, Corner | None]]:
    """Fatia a volta em curvas e trechos entre curvas.

    Sem curvas detectadas a volta vira um segmento só — o relatório fica pobre,
    mas continua correto, que é o comportamento certo para um teste de
    aceleração ou um traçado oval.
    """
    if not corners:
        return [(0.0, limit, "Volta", None)]

    bounds: list[tuple[float, float, str, Corner | None]] = []
    cursor = 0.0
    straight_number = 1

    for corner in corners:
        entry = max(cursor, min(corner.entry_distance_m, limit))
        exit_at = max(entry, min(corner.exit_distance_m, limit))

        if entry - cursor > 1.0:
            bounds.append((cursor, entry, f"Reta {straight_number}", None))
            straight_number += 1

        if exit_at > entry:
            bounds.append((entry, exit_at, f"Curva {corner.index}", corner))
        cursor = exit_at

    if limit - cursor > 1.0:
        bounds.append((cursor, limit, f"Reta {straight_number}", None))

    return bounds


def _delta_change(
    reference: LapSeries, analysed: LapSeries, start_m: float, end_m: float
) -> float | None:
    """Quanto o delta variou entre duas distâncias.

    É a diferença de *duração* dos dois trechos: quanto cada volta levou para
    cobrir o mesmo pedaço de asfalto. Por isso mede o que aconteceu ali dentro e
    não arrasta o que veio antes.
    """
    reference_start = reference.elapsed_ms_at(start_m)
    reference_end = reference.elapsed_ms_at(end_m)
    analysed_start = analysed.elapsed_ms_at(start_m)
    analysed_end = analysed.elapsed_ms_at(end_m)

    if None in (reference_start, reference_end, analysed_start, analysed_end):
        return None

    assert reference_start is not None and reference_end is not None
    assert analysed_start is not None and analysed_end is not None

    return (analysed_end - analysed_start) - (reference_end - reference_start)


def _nearest(
    zones: dict[float, BrakingComparison], start_m: float, end_m: float
) -> BrakingComparison | None:
    """A frenagem que começa dentro do segmento, se houver.

    Uma frenagem tipicamente começa antes da entrada geométrica da curva, então
    a janela é esticada 80 m para trás — sem isso, quase nenhuma curva teria
    frenagem associada e o diagnóstico perderia sua evidência principal.
    """
    window_start = start_m - 80.0
    inside = [
        comparison
        for distance, comparison in zones.items()
        if window_start <= distance <= end_m
    ]
    if not inside:
        return None
    return min(inside, key=lambda c: abs(c.reference.start_distance_m - start_m))
