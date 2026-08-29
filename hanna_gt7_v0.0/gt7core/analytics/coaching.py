"""
Diagnóstico por curva — onde melhorar, e o que fazer.

O perfil do piloto (`analytics.driver`) responde "quanto": 7 travamentos por
volta, ponto de frenagem variando ±25 m. Números certos, e inúteis para quem
vai entrar na pista de novo — ninguém treina "travar menos". Treina-se **a
curva 7**, e treina-se com uma instrução: freiar mais dentro, com menos pressão.

Este módulo faz a mesma medição olhando por curva, e não pela volta inteira. O
que muda é onde a estatística é feita: em vez de somar as ocorrências da volta e
dividir pelo número de voltas, cada ocorrência é atribuída à curva em que
aconteceu, e a recorrência **naquela curva** é o que vira apontamento.

Duas regras de honestidade, que são as mesmas do resto do projeto:

- **Recorrência, não evento.** Travar uma vez em oito voltas é acaso; travar em
  seis é hábito. Só o segundo caso vira apontamento, e o texto sempre diz em
  quantas voltas de quantas aconteceu — quem lê julga o peso por si.
- **Sintoma medido, ação sugerida.** Cada apontamento carrega a medida que o
  gerou. A sugestão é uma leitura da medida, e as duas ficam lado a lado
  justamente para que uma sugestão errada seja conferível contra o número.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.models import TelemetryPoint
from .braking import BrakingZone, detect_braking_zones
from .corners import Corner, detect_corners
from .throttle import ThrottleApplication, analyse_throttle
from .tyres import TyreEvent, detect_tyre_events, infer_slip_convention

#: Fração das voltas em que o sintoma precisa se repetir para virar apontamento.
#: Metade é um limiar deliberadamente exigente: abaixo dele, o que se aponta é
#: variação normal entre voltas, e um conselho baseado nisso manda o piloto
#: corrigir o que não está errado.
MIN_RECURRENCE = 0.5

#: Piso absoluto de ocorrências. Com duas voltas na janela, 50% é uma volta só —
#: e uma ocorrência nunca é hábito, por mais alta que a fração fique.
MIN_OCCURRENCES = 2

#: Tolerância para casar a mesma curva entre voltas, em metros. Mesma do
#: `match_corners`: a distância acumulada de duas voltas diverge um pouco, e o
#: ápice de uma curva lenta anda algumas dezenas de metros conforme a linha.
CORNER_TOLERANCE_M = 120.0

#: Desvio do ponto de frenagem, em metros, a partir do qual a referência é
#: instável. 20 m a 200 km/h são 0,36 s de diferença na mesma freada.
UNSTABLE_BRAKING_M = 20.0

#: Desvio da velocidade mínima, em km/h, a partir do qual a entrada é irregular.
UNSTABLE_APEX_SPEED_KMH = 6.0

#: Pressão máxima de freio, em %, acima da qual a freada é considerada de
#: pressão cheia. Travamento com pressão cheia é excesso de pedal; travamento
#: com pressão baixa é falta de carga na dianteira, e o conselho é outro.
FULL_BRAKE_PCT = 92.0

#: Trail braking abaixo disto é freio solto de uma vez.
LOW_TRAIL = 0.2


@dataclass(frozen=True, slots=True)
class CornerIssue:
    """Um sintoma recorrente numa curva, com a medida e o que fazer."""

    kind: str
    """Chave do sintoma: `travamento`, `patinagem`, `alivio`,
    `referencia_instavel`, `entrada_irregular`, `freio_seco`."""

    occurrences: int
    laps_seen: int
    measure: str
    """A medida que gerou o apontamento, na unidade em que foi medida."""

    advice: str
    """O que fazer na próxima volta."""

    severity: float
    """Para ordenar. Recorrência ponderada pela gravidade do sintoma."""

    @property
    def frequency(self) -> str:
        return f"em {self.occurrences} de {self.laps_seen} voltas"


@dataclass(frozen=True, slots=True)
class CornerReport:
    """Tudo o que se apontou numa curva."""

    number: int
    """Número da curva na volta, começando em 1 — como o piloto conta."""

    apex_distance_m: float
    issues: list[CornerIssue] = field(default_factory=list)

    @property
    def severity(self) -> float:
        return sum(issue.severity for issue in self.issues)

    def as_lines(self) -> list[str]:
        """Texto no formato que a tela e o rádio usam."""
        return [
            f"curva {self.number} — {issue.advice}  ({issue.measure}, {issue.frequency})"
            for issue in self.issues
        ]


@dataclass(slots=True)
class _CornerTally:
    """Acumulador de uma curva ao longo da janela. Interno."""

    number: int
    apexes: list[float] = field(default_factory=list)
    laps_seen: int = 0
    lockups: int = 0
    lockups_full_pressure: int = 0
    wheelspins: int = 0
    lifts: int = 0
    braking_starts: list[float] = field(default_factory=list)
    apex_speeds: list[float] = field(default_factory=list)
    trail_ratios: list[float] = field(default_factory=list)


def diagnose_corners(laps: list[list[TelemetryPoint]]) -> list[CornerReport]:
    """Apontamentos por curva sobre uma janela de voltas da mesma pista.

    As voltas vêm em ordem cronológica. A primeira volta utilizável define
    quais são as curvas e como elas se numeram; as demais são casadas contra
    ela pela distância do ápice — pelo mesmo motivo de `match_corners`, uma
    curva perdida pelo detector numa volta desalinharia todas as seguintes se
    o casamento fosse por índice.

    Devolve as curvas com apontamento, da mais grave para a menos, e nada mais:
    uma curva sem sintoma recorrente é uma curva que está boa, e listá-la com
    "nada a apontar" encheria a tela com o que não precisa de ação.
    """
    usable = [lap for lap in laps if len(lap) >= 2]
    if not usable:
        return []

    referencia = detect_corners(usable[0])
    if not referencia:
        return []

    tallies = [
        _CornerTally(number=i, apexes=[c.apex_distance_m])
        for i, c in enumerate(referencia, start=1)
    ]

    for lap in usable:
        curvas = detect_corners(lap)
        if not curvas:
            continue
        convention = infer_slip_convention(lap)
        zonas = detect_braking_zones(lap)
        eventos = detect_tyre_events(lap, convention=convention)
        retomadas = analyse_throttle(lap, curvas, convention=convention)

        for tally, alvo in zip(tallies, referencia, strict=True):
            curva = _closest_corner(curvas, alvo.apex_distance_m)
            if curva is None:
                continue
            tally.laps_seen += 1
            tally.apexes.append(curva.apex_distance_m)
            tally.apex_speeds.append(curva.minimum_speed_kmh)
            _tally_braking(tally, curva, zonas)
            _tally_tyres(tally, curva, eventos, zonas)
            _tally_throttle(tally, curva, retomadas)

    relatorios = [
        relatorio
        for relatorio in (_report_for(tally) for tally in tallies)
        if relatorio is not None
    ]
    relatorios.sort(key=lambda r: r.severity, reverse=True)
    return relatorios


# ---------- acumulação ----------


def _closest_corner(corners: list[Corner], apex_m: float) -> Corner | None:
    """A curva desta volta que corresponde ao ápice de referência."""
    melhor = min(corners, key=lambda c: abs(c.apex_distance_m - apex_m))
    if abs(melhor.apex_distance_m - apex_m) > CORNER_TOLERANCE_M:
        return None
    return melhor


def _braking_zone_for(corner: Corner, zones: list[BrakingZone]) -> BrakingZone | None:
    """A freada que prepara esta curva: a última que termina antes do ápice.

    "Antes do ápice", e não "dentro da curva": trail braking legítimo passa da
    entrada, e uma zona que começou na reta é a freada da curva mesmo que
    termine depois do ponto de entrada detectado.
    """
    candidatas = [
        zona
        for zona in zones
        if zona.start_distance_m <= corner.apex_distance_m
        and zona.end_distance_m >= corner.entry_distance_m - CORNER_TOLERANCE_M
    ]
    return candidatas[-1] if candidatas else None


def _tally_braking(
    tally: _CornerTally, corner: Corner, zones: list[BrakingZone]
) -> None:
    zona = _braking_zone_for(corner, zones)
    if zona is None:
        return
    tally.braking_starts.append(zona.start_distance_m)
    tally.trail_ratios.append(zona.trail_braking_ratio)


def _tally_tyres(
    tally: _CornerTally,
    corner: Corner,
    events: list[TyreEvent],
    zones: list[BrakingZone],
) -> None:
    """Conta travamentos na entrada e patinagens na saída.

    A separação é o diagnóstico: travar é erro de freio e patinar é erro de
    acelerador, e um contador que somasse os dois num "perdeu aderência na
    curva 7" apagaria justamente a informação que diz o que fazer.

    Eventos sobrepostos são o mesmo incidente visto por rodas diferentes — a
    mesma regra de `driver._incident_count`, e pelo mesmo motivo: contar por
    roda dobraria o número toda vez que um eixo inteiro travasse junto.
    """
    zona = _braking_zone_for(corner, zones)
    pressao_cheia = zona is not None and zona.max_pressure_pct >= FULL_BRAKE_PCT

    entrada = (corner.entry_distance_m, corner.apex_distance_m)
    saida = (corner.apex_distance_m, corner.exit_distance_m)

    if _has_incident(events, "travamento", entrada):
        tally.lockups += 1
        if pressao_cheia:
            tally.lockups_full_pressure += 1
    if _has_incident(events, "patinagem", saida):
        tally.wheelspins += 1


def _has_incident(
    events: list[TyreEvent], kind: str, span: tuple[float, float]
) -> bool:
    inicio, fim = span
    return any(
        event.kind == kind
        and event.end_distance_m >= inicio
        and event.start_distance_m <= fim
        for event in events
    )


def _tally_throttle(
    tally: _CornerTally, corner: Corner, applications: list[ThrottleApplication]
) -> None:
    for aplicacao in applications:
        if abs(aplicacao.apex_distance_m - corner.apex_distance_m) <= CORNER_TOLERANCE_M:
            if aplicacao.lift_count > 0:
                tally.lifts += 1
            return


# ---------- leitura ----------


def _report_for(tally: _CornerTally) -> CornerReport | None:
    if tally.laps_seen < MIN_OCCURRENCES:
        return None

    issues: list[CornerIssue] = []
    for construir in (
        _issue_lockup,
        _issue_wheelspin,
        _issue_lift,
        _issue_braking_reference,
        _issue_apex_speed,
        _issue_dry_brake,
    ):
        issue = construir(tally)
        if issue is not None:
            issues.append(issue)

    if not issues:
        return None
    issues.sort(key=lambda i: i.severity, reverse=True)
    return CornerReport(
        number=tally.number,
        apex_distance_m=sum(tally.apexes) / len(tally.apexes),
        issues=issues,
    )


def _recurrent(count: int, laps_seen: int) -> bool:
    return count >= MIN_OCCURRENCES and count >= laps_seen * MIN_RECURRENCE


def _issue_lockup(tally: _CornerTally) -> CornerIssue | None:
    if not _recurrent(tally.lockups, tally.laps_seen):
        return None

    # O conselho depende de **por que** travou, e isso está na pressão. Travar
    # com o pedal no fundo é excesso de pressão; travar com pressão média é
    # frenagem começando cedo demais, com o carro ainda leve na dianteira.
    cheia = tally.lockups_full_pressure >= tally.lockups / 2
    if cheia:
        conselho = (
            "usar menos pressão de freio no primeiro toque e freiar mais dentro"
        )
        medida = "trava com o pedal no fundo"
    else:
        conselho = (
            "atrasar o início da freada e carregar o pedal mais rápido, "
            "com a dianteira já apoiada"
        )
        medida = "trava sem pressão cheia"
    return CornerIssue(
        kind="travamento",
        occurrences=tally.lockups,
        laps_seen=tally.laps_seen,
        measure=medida,
        advice=conselho,
        severity=3.0 * tally.lockups / tally.laps_seen,
    )


def _issue_wheelspin(tally: _CornerTally) -> CornerIssue | None:
    if not _recurrent(tally.wheelspins, tally.laps_seen):
        return None
    return CornerIssue(
        kind="patinagem",
        occurrences=tally.wheelspins,
        laps_seen=tally.laps_seen,
        measure="a traseira gira mais que o carro na saída",
        advice="na saída, atrasar um pouco e abrir o acelerador com mais progressividade",
        severity=3.0 * tally.wheelspins / tally.laps_seen,
    )


def _issue_lift(tally: _CornerTally) -> CornerIssue | None:
    if not _recurrent(tally.lifts, tally.laps_seen):
        return None
    return CornerIssue(
        kind="alivio",
        occurrences=tally.lifts,
        laps_seen=tally.laps_seen,
        measure="alívio depois de já ter acelerado",
        advice=(
            "esperar o carro apontar antes de abrir — aliviar no meio custa mais "
            "do que ter aberto meio segundo depois"
        ),
        severity=2.0 * tally.lifts / tally.laps_seen,
    )


def _issue_braking_reference(tally: _CornerTally) -> CornerIssue | None:
    if len(tally.braking_starts) < MIN_OCCURRENCES:
        return None
    desvio = _stddev(tally.braking_starts)
    if desvio <= UNSTABLE_BRAKING_M:
        return None
    return CornerIssue(
        kind="referencia_instavel",
        occurrences=len(tally.braking_starts),
        laps_seen=tally.laps_seen,
        measure=f"ponto de freada varia ±{desvio:.0f} m",
        advice=(
            "escolher uma referência fixa na beira da pista para começar a "
            "frear sempre no mesmo lugar"
        ),
        severity=1.5 * min(desvio / UNSTABLE_BRAKING_M, 3.0),
    )


def _issue_apex_speed(tally: _CornerTally) -> CornerIssue | None:
    if len(tally.apex_speeds) < MIN_OCCURRENCES:
        return None
    desvio = _stddev(tally.apex_speeds)
    if desvio <= UNSTABLE_APEX_SPEED_KMH:
        return None
    return CornerIssue(
        kind="entrada_irregular",
        occurrences=len(tally.apex_speeds),
        laps_seen=tally.laps_seen,
        measure=f"velocidade no ápice varia ±{desvio:.1f} km/h",
        advice=(
            "entrar sempre com a mesma velocidade antes de tentar entrar mais "
            "rápido — a curva ainda não está repetível"
        ),
        severity=1.0 * min(desvio / UNSTABLE_APEX_SPEED_KMH, 3.0),
    )


def _issue_dry_brake(tally: _CornerTally) -> CornerIssue | None:
    """Freio solto de uma vez, curva a curva.

    Só vale onde a curva é freada de verdade: numa curva de acelerador o trail
    braking é zero porque não houve freio, e apontar isso seria apontar a
    ausência de um erro.
    """
    if len(tally.trail_ratios) < MIN_OCCURRENCES:
        return None
    media = sum(tally.trail_ratios) / len(tally.trail_ratios)
    if media >= LOW_TRAIL:
        return None
    return CornerIssue(
        kind="freio_seco",
        occurrences=len(tally.trail_ratios),
        laps_seen=tally.laps_seen,
        measure=f"freio solto de uma vez (trail {media:.0%})",
        advice=(
            "soltar o freio aos poucos até o ápice — o freio ainda apoiado gira "
            "o carro e tira subesterço da entrada"
        ),
        severity=1.0,
    )


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    media = sum(values) / len(values)
    variancia = sum((v - media) ** 2 for v in values) / (len(values) - 1)
    return variancia ** 0.5
