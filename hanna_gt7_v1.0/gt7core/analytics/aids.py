"""
Auxílios eletrônicos — onde o TCS e o ASM entraram em ação.

Um canal de auxílio não é enfeite: cada trecho em que o controle de tração atua
é um trecho em que o piloto pediu mais do que o carro tinha, e o computador
cobriu a diferença. Ver **onde** isso acontece volta após volta é ver onde falta
acerto ou falta paciência com o acelerador — e é informação que nenhum outro
canal entrega, porque no gráfico de acelerador a entrada aparece igual: 100%.

Sobre o ABS
-----------
**O bit do ABS não está identificado.** O campo de flags do pacote (0x8E) tem 16
bits e a engenharia reversa da comunidade nomeou doze; o freio antitravamento
não está entre eles. Este módulo não inventa o décimo terceiro: `unknown_bits()`
existe justamente para achá-lo com método, reportando quais bits sem nome mudam
de estado durante a volta. Uma sessão freando forte com ABS ligado e outra com
ele desligado isolam o bit em duas voltas — e aí ele entra aqui como fato
verificado, não como palpite. Foi um palpite de offset que gravou distância
0,0 m em toda volta de PS5 real; o preço já foi pago uma vez.

Voltas antigas
--------------
`flags` só passou a ser gravado na versão 7 do banco. Em volta anterior ele vem
`None`, e isso **não** é "nenhum auxílio atuou" — é "não foi medido".
`was_recorded()` separa os dois casos, para a tela poder dizer qual dos dois é.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.models import TelemetryPoint
from ..telemetry.protocol import (
    FLAG_ASM_ACTIVE,
    FLAG_CAR_ON_TRACK,
    FLAG_HANDBRAKE,
    FLAG_HAS_TURBO,
    FLAG_HIGH_BEAM,
    FLAG_IN_GEAR,
    FLAG_LIGHTS_ON,
    FLAG_LOADING,
    FLAG_LOW_BEAM,
    FLAG_PAUSED,
    FLAG_REV_LIMITER,
    FLAG_TCS_ACTIVE,
)

#: Os auxílios que o pacote nomeia, na ordem em que interessam ao piloto.
#:
#: As máscaras vêm de `protocol.py` em vez de serem reescritas aqui. Repetir um
#: número mágico em dois arquivos é o mesmo erro que deixou o defeito de offset
#: passar pelos testes: o `conftest` escrevia o pacote nos mesmos endereços
#: errados que o leitor lia, e os dois concordavam sobre uma mentira.
AIDS: dict[str, int] = {
    "TCS": FLAG_TCS_ACTIVE,
    "ASM": FLAG_ASM_ACTIVE,
}

#: Todos os bits com nome conhecido. O complemento disto é o território do ABS.
KNOWN_BITS = (
    FLAG_CAR_ON_TRACK
    | FLAG_PAUSED
    | FLAG_LOADING
    | FLAG_IN_GEAR
    | FLAG_HAS_TURBO
    | FLAG_REV_LIMITER
    | FLAG_HANDBRAKE
    | FLAG_LIGHTS_ON
    | FLAG_HIGH_BEAM
    | FLAG_LOW_BEAM
    | FLAG_ASM_ACTIVE
    | FLAG_TCS_ACTIVE
)

#: Duas atuações separadas por menos que isto viram uma só.
#:
#: O controle de tração cicla em dezenas de milissegundos; desenhado cru, um
#: único episódio de 1,5 s vira um pente de trinta fatias de dois pixels que não
#: se lê como nada. Unir os intervalos curtos mostra o episódio — que é a
#: unidade de que o piloto precisa —, sem apagar que houve modulação.
MERGE_GAP_MS = 120


@dataclass(frozen=True, slots=True)
class AidSpan:
    """Um trecho contínuo com o auxílio atuando."""

    aid: str
    start_distance_m: float
    end_distance_m: float
    start_time_ms: int
    end_time_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_time_ms - self.start_time_ms

    @property
    def length_m(self) -> float:
        return self.end_distance_m - self.start_distance_m


def was_recorded(points: list[TelemetryPoint]) -> bool:
    """A volta tem o estado dos auxílios gravado?

    Distingue "não atuou" de "não foi medido". Sem esta pergunta, uma volta de
    antes da versão 7 do banco desenharia uma faixa vazia e afirmaria uma
    pilotagem sem auxílios que ninguém observou.
    """
    return any(p.flags is not None for p in points)


def aid_spans(points: list[TelemetryPoint], aid: str) -> list[AidSpan]:
    """Trechos em que o auxílio esteve atuando, em ordem de distância."""
    mask = AIDS.get(aid)
    if mask is None or not points:
        return []

    spans: list[AidSpan] = []
    start: TelemetryPoint | None = None
    previous: TelemetryPoint | None = None

    for point in points:
        active = point.flags is not None and bool(point.flags & mask)
        if active and start is None:
            start = point
        elif not active and start is not None and previous is not None:
            spans.append(_span(aid, start, previous))
            start = None
        previous = point

    if start is not None and previous is not None:
        spans.append(_span(aid, start, previous))

    return _merge_close(spans)


def unknown_bits(points: list[TelemetryPoint]) -> int:
    """Bits sem nome que aparecem ligados em alguma amostra.

    É o instrumento para achar o ABS: freie forte com ele ligado, depois com ele
    desligado, e compare o retorno das duas voltas. O bit que aparece só na
    primeira é o candidato — e vira fato só depois de repetir.
    """
    seen = 0
    for point in points:
        if point.flags is not None:
            seen |= point.flags
    return seen & ~KNOWN_BITS


def _span(aid: str, start: TelemetryPoint, end: TelemetryPoint) -> AidSpan:
    return AidSpan(
        aid=aid,
        start_distance_m=start.distance_m,
        end_distance_m=end.distance_m,
        start_time_ms=start.elapsed_ms,
        end_time_ms=end.elapsed_ms,
    )


def _merge_close(spans: list[AidSpan]) -> list[AidSpan]:
    """Une atuações separadas por menos que `MERGE_GAP_MS`."""
    if not spans:
        return []

    merged = [spans[0]]
    for span in spans[1:]:
        last = merged[-1]
        if span.start_time_ms - last.end_time_ms <= MERGE_GAP_MS:
            merged[-1] = AidSpan(
                aid=last.aid,
                start_distance_m=last.start_distance_m,
                end_distance_m=span.end_distance_m,
                start_time_ms=last.start_time_ms,
                end_time_ms=span.end_time_ms,
            )
        else:
            merged.append(span)
    return merged
