"""
Análise de acelerador — §14 do briefing.

A saída de curva é onde mais se ganha tempo, porque o erro se propaga por toda a
reta seguinte: meio km/h a menos no fim da curva é meio km/h a menos até a
próxima frenagem. Por isso este módulo é ancorado nas **curvas** detectadas, não
na volta inteira — "throttle médio da volta" é um número que não sugere nenhuma
ação.

O que se mede em cada saída:

- **onde** o piloto voltou a acelerar, em relação ao ápice;
- **quão rápido** foi de zero a fundo;
- **quão limpo** foi o movimento — reaplicações e alívios contam;
- **patinou?** — acelerar cedo demais aparece como escorregamento na traseira.

Um detalhe que muda a leitura: aplicar tarde e aplicar devagar são erros
diferentes com causas diferentes. Tarde costuma ser falta de confiança na
entrada; devagar costuma ser o carro não aceitar o acelerador (traseira solta,
marcha errada). Os dois números ficam separados de propósito.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.models import TelemetryPoint
from .corners import Corner
from .matching import match_by_distance
from .tyres import SlipConvention, TyreEvent, detect_tyre_events, infer_slip_convention

# Acima disto considera-se que o piloto voltou a acelerar de verdade — abaixo é
# manutenção de velocidade no ápice.
THROTTLE_APPLICATION_PCT = 15.0

# "A fundo" na prática: exigir 100% exato faria a métrica depender da resolução
# do pedal e de um único quadro.
THROTTLE_FULL_PCT = 95.0
THROTTLE_HALF_PCT = 50.0

# Queda de pedal considerada um alívio deliberado, não ruído de mão.
LIFT_DROP_PCT = 10.0

#: Freio acima disto significa que o piloto ainda está pedindo desaceleração.
#:
#: Não é zero: o gatilho analógico do controle repousa em 1–2%, e exigir zero
#: exato faria toda saída de curva parecer sobreposição de pedais.
BRAKE_ACTIVE_PCT = 5.0

#: Duração mínima, em ms, para uma subida de acelerador ser retomada e não
#: **autoblip**.
#:
#: O GT7 acelera sozinho na redução de marcha, para casar a rotação. Isso
#: produz um pico de pedal que sobe e volta em 150–250 ms, e que o detector
#: lia como "o piloto voltou a acelerar aqui". Uma retomada de verdade dura
#: segundos — vai até a próxima frenagem. 300 ms separa as duas com folga dos
#: dois lados.
MIN_APPLICATION_MS = 300


@dataclass(frozen=True, slots=True)
class ThrottleApplication:
    """A retomada de acelerador na saída de uma curva."""

    corner_index: int
    apex_distance_m: float

    application_distance_m: float
    """Onde o acelerador cruzou o limiar de retomada."""

    application_time_ms: int

    time_to_half_ms: int | None
    """Da retomada até 50% de pedal. None se nunca chegou lá."""

    time_to_full_ms: int | None
    """Da retomada até pedal cheio. None se nunca chegou lá."""

    lift_count: int
    """Alívios depois de já ter começado a acelerar.

    Zero é o ideal. Um ou mais indica que o piloto abriu o acelerador antes do
    carro aceitar e teve que corrigir — que custa mais tempo do que ter esperado.
    """

    exit_speed_kmh: float
    wheelspin_events: int

    @property
    def delay_from_apex_m(self) -> float:
        """Distância entre o ápice e a retomada. Menor é mais agressivo."""
        return self.application_distance_m - self.apex_distance_m

    @property
    def is_clean(self) -> bool:
        return self.lift_count == 0 and self.wheelspin_events == 0

    def describe(self) -> str:
        parts = [f"acelera {self.delay_from_apex_m:+.0f} m do ápice"]
        if self.time_to_full_ms is not None:
            parts.append(f"pedal cheio em {self.time_to_full_ms / 1000:.2f} s")
        else:
            parts.append("não chegou a pedal cheio")
        if self.lift_count:
            parts.append(f"{self.lift_count} alívio(s)")
        if self.wheelspin_events:
            parts.append(f"{self.wheelspin_events} patinagem(ns)")
        return ", ".join(parts)


def analyse_throttle(
    points: list[TelemetryPoint],
    corners: list[Corner],
    *,
    application_pct: float = THROTTLE_APPLICATION_PCT,
    convention: SlipConvention | None = None,
) -> list[ThrottleApplication]:
    """Uma análise de saída por curva detectada, em ordem de distância.

    Curvas em que o piloto nunca soltou o acelerador não produzem saída — não há
    retomada a medir — e são simplesmente omitidas.
    """
    if not points or not corners:
        return []

    resolved = convention if convention is not None else infer_slip_convention(points)
    spins = [e for e in detect_tyre_events(points, convention=resolved)
             if e.kind == "patinagem"]

    results: list[ThrottleApplication] = []
    for corner in corners:
        application = _analyse_exit(points, corner, application_pct, spins)
        if application is not None:
            results.append(application)
    return results


def _analyse_exit(
    points: list[TelemetryPoint],
    corner: Corner,
    application_pct: float,
    spins: list[TyreEvent],
) -> ThrottleApplication | None:
    apex_index = _index_at_distance(points, corner.apex_distance_m)
    exit_index = _index_at_distance(points, corner.exit_distance_m)
    if apex_index is None or exit_index is None or exit_index <= apex_index:
        return None

    window = points[apex_index : exit_index + 1]

    start = _find_application(window, application_pct)
    if start is None:
        return None

    origin = window[start]
    time_to_half = _time_to_reach(window, start, THROTTLE_HALF_PCT)
    time_to_full = _time_to_reach(window, start, THROTTLE_FULL_PCT)

    lifts = _count_lifts(window, start)

    in_window = sum(
        1
        for spin in spins
        if origin.distance_m <= spin.start_distance_m <= corner.exit_distance_m
    )

    return ThrottleApplication(
        corner_index=corner.index,
        apex_distance_m=corner.apex_distance_m,
        application_distance_m=origin.distance_m,
        application_time_ms=origin.elapsed_ms,
        time_to_half_ms=time_to_half,
        time_to_full_ms=time_to_full,
        lift_count=lifts,
        exit_speed_kmh=window[-1].speed_kmh,
        wheelspin_events=in_window,
    )


def _find_application(
    window: list[TelemetryPoint], application_pct: float
) -> int | None:
    """Onde o **piloto** voltou a acelerar, ignorando o que o carro fez sozinho.

    Antes isto era a primeira amostra com pedal acima do limiar, e era por isso
    que o autoblip contaminava a medida: o GT7 acelera sozinho na redução de
    marcha, e esse pico de 150–250 ms virava "a retomada". Duas consequências,
    as duas silenciosas — a distância até a retomada saía muito menor do que
    foi, e a volta do pedal a zero logo depois entrava como alívio.

    Duas travas, porque cada uma pega um caso que a outra deixa passar:

    - **Freio ainda apoiado.** É onde a maioria das reduções acontece, e pedido
      de desaceleração e retomada de acelerador não coexistem por vontade do
      piloto.
    - **Pico curto demais.** Pega a redução feita já fora do freio, entrando de
      inércia no ápice — ali o freio está solto e só a duração denuncia.
    """
    total = len(window)
    i = 0
    while i < total:
        ponto = window[i]
        if ponto.throttle < application_pct or ponto.brake >= BRAKE_ACTIVE_PCT:
            i += 1
            continue

        fim = i
        while fim < total and window[fim].throttle >= application_pct:
            fim += 1

        duracao = window[fim - 1].elapsed_ms - ponto.elapsed_ms
        # Trecho que chega até o fim da janela é retomada por definição: a curva
        # acabou com o pedal ainda em pé, então não houve pico nenhum.
        if duracao >= MIN_APPLICATION_MS or fim >= total:
            return i
        i = fim
    return None


def _count_lifts(window: list[TelemetryPoint], start: int) -> int:
    """Quantos alívios **distintos** houve depois da retomada.

    Um alívio é soltar e voltar a acelerar. A versão anterior contava a cada
    queda de 10 pontos abaixo do pico corrente e rebaixava o pico junto, de modo
    que uma única soltada contínua de 100% a 0% saía como **oito** alívios — e
    esse número ia para a tela, para o perfil do piloto e para o prompt do
    engenheiro, que foi instruído a nunca inventar grandeza e repetia a inflação
    fielmente.

    Agora o contador só rearma quando o pedal **volta a subir**, que é o que
    fecha o ciclo de soltar-e-reaplicar descrito por `lift_count`.
    """
    origem = window[start].throttle
    pico = origem
    vale = origem
    aliviando = False
    alivios = 0

    for ponto in window[start + 1 :]:
        if aliviando:
            vale = min(vale, ponto.throttle)
            if ponto.throttle > vale + LIFT_DROP_PCT:
                aliviando = False
                pico = ponto.throttle
        elif ponto.throttle < pico - LIFT_DROP_PCT:
            alivios += 1
            aliviando = True
            vale = ponto.throttle
        else:
            pico = max(pico, ponto.throttle)

    return alivios


def _time_to_reach(
    window: list[TelemetryPoint], start: int, target_pct: float
) -> int | None:
    origin = window[start]
    for point in window[start:]:
        if point.throttle >= target_pct:
            return point.elapsed_ms - origin.elapsed_ms
    return None


def _index_at_distance(points: list[TelemetryPoint], distance_m: float) -> int | None:
    """Índice da amostra mais próxima da distância informada.

    Busca linear de propósito: roda uma vez por curva na análise pós-volta, não
    no caminho quente de 60 Hz, e uma busca binária aqui exigiria manter uma
    lista paralela de distâncias por um ganho que ninguém mediria.
    """
    if not points:
        return None
    best_index = 0
    best_gap = abs(points[0].distance_m - distance_m)
    for index, point in enumerate(points[1:], start=1):
        gap = abs(point.distance_m - distance_m)
        if gap < best_gap:
            best_index, best_gap = index, gap
    return best_index


@dataclass(frozen=True, slots=True)
class ThrottleComparison:
    """Diferença de saída de curva entre a volta analisada e a referência."""

    reference: ThrottleApplication
    analysed: ThrottleApplication | None

    @property
    def application_delta_m(self) -> float | None:
        """Positivo = acelerou **depois** da referência."""
        if self.analysed is None:
            return None
        return self.analysed.delay_from_apex_m - self.reference.delay_from_apex_m

    @property
    def exit_speed_delta_kmh(self) -> float | None:
        if self.analysed is None:
            return None
        return self.analysed.exit_speed_kmh - self.reference.exit_speed_kmh

    def describe(self) -> str:
        if self.analysed is None:
            return "sem saída correspondente nesta volta"

        notes: list[str] = []
        delta = self.application_delta_m or 0.0
        if delta > 5:
            notes.append(f"acelerou {delta:.0f} m mais tarde")
        elif delta < -5:
            notes.append(f"acelerou {abs(delta):.0f} m mais cedo")

        reference_full = self.reference.time_to_full_ms
        analysed_full = self.analysed.time_to_full_ms
        if reference_full is not None and analysed_full is not None:
            gap = analysed_full - reference_full
            if gap > 150:
                notes.append(f"{gap / 1000:.2f} s mais lento até pedal cheio")
        elif analysed_full is None and reference_full is not None:
            notes.append("não chegou a pedal cheio")

        extra_lifts = self.analysed.lift_count - self.reference.lift_count
        if extra_lifts > 0:
            notes.append(f"{extra_lifts} alívio(s) a mais")

        if self.analysed.wheelspin_events > self.reference.wheelspin_events:
            notes.append("patinou na saída")

        speed = self.exit_speed_delta_kmh or 0.0
        if speed < -2:
            notes.append(f"{abs(speed):.0f} km/h a menos saindo")

        return "; ".join(notes) if notes else "saída equivalente à referência"


def compare_throttle(
    reference: list[ThrottleApplication],
    analysed: list[ThrottleApplication],
    *,
    tolerance_m: float = 150.0,
) -> list[ThrottleComparison]:
    """Casa as saídas de duas voltas pela posição do ápice."""
    pairs = match_by_distance(
        reference,
        analysed,
        reference_key=lambda a: a.apex_distance_m,
        candidate_key=lambda a: a.apex_distance_m,
        tolerance_m=tolerance_m,
    )
    return [
        ThrottleComparison(reference=item, analysed=matched) for item, matched in pairs
    ]
