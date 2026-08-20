"""
Análise de pneus — §15 do briefing.

Cobre três perguntas distintas que costumam ser confundidas:

1. **Temperatura** — o pneu está na janela? O desequilíbrio entre eixos e entre
   lados diz mais sobre o acerto do carro do que o valor absoluto.
2. **Aderência instantânea** — travou na freada? patinou na saída? São eventos,
   não médias, e é por isso que este módulo devolve uma lista de ocorrências
   com distância e duração, não um número por volta.
3. **Degradação ao longo do stint** — o que muda entre a volta 3 e a volta 18.

Uma ambiguidade que este módulo resolve num lugar só
----------------------------------------------------
O campo `tire_slip_*` do pacote GT7 **não tem especificação oficial**. Há duas
leituras plausíveis na engenharia reversa da comunidade:

- é a **velocidade da superfície do pneu em m/s** — e então a razão contra a
  velocidade do carro é o escorregamento de verdade;
- é um valor **adimensional** já normalizado.

A aplicação anterior (`src/application/viewmodels/telemetry_viewmodel.py`)
assumiu a segunda leitura e multiplicou por uma constante para virar graus,
admitindo no comentário que era aproximação. Aqui a escolha deixa de ser
implícita: `SlipConvention` nomeia as duas, `infer_slip_convention()` decide
olhando uma volta inteira, e **todo o resto do sistema deriva daqui**. Se algum
dia um pacote real resolver a questão, há um lugar só para corrigir.

A inferência é segura porque as duas hipóteses estão a ordens de grandeza de
distância: a 200 km/h a superfície do pneu anda a ~55 m/s, enquanto uma razão
fica perto de 1. Não é um palpite por amostra — é uma decisão por volta, com
separação enorme entre os casos.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum

from ..domain.models import TelemetryPoint

# Critério de "esta amostra está em perda de aderência", dado o ponto e a razão
# de escorregamento já calculada.
SlipPredicate = Callable[[TelemetryPoint, float], bool]

# Como escolher a razão mais extrema do evento: `min` para travamento (a menor),
# `max` para patinagem (a maior).
Extreme = Callable[[Iterable[float]], float]

WHEELS: tuple[str, ...] = ("fl", "fr", "rl", "rr")
FRONT_WHEELS: tuple[str, ...] = ("fl", "fr")
REAR_WHEELS: tuple[str, ...] = ("rl", "rr")

# Abaixo desta velocidade a razão de escorregamento é numericamente instável
# (divisão por um número pequeno) e fisicamente irrelevante.
MIN_SPEED_FOR_SLIP_KMH = 30.0

# Roda girando a menos de 92% da velocidade do carro sob freio = travamento.
LOCKUP_RATIO = 0.92

# Roda de tração a mais de 108% sob acelerador = patinagem.
WHEELSPIN_RATIO = 1.08

# Eventos mais curtos que isto são ruído de amostragem, não perda de aderência.
MIN_EVENT_DURATION_MS = 80


class SlipConvention(StrEnum):
    """Como interpretar o campo `tire_slip_*` do pacote."""

    SURFACE_SPEED_MS = "surface_speed_ms"
    """Velocidade da superfície do pneu, em m/s. A razão é derivada."""

    RATIO = "ratio"
    """Já é uma razão adimensional (1.0 = sem escorregamento)."""


def infer_slip_convention(points: list[TelemetryPoint]) -> SlipConvention:
    """Decide a convenção olhando a magnitude do canal numa volta inteira.

    Compara a média do canal com a média da velocidade em m/s. Se as duas são
    da mesma ordem, o campo é velocidade de superfície; se o canal fica perto da
    unidade enquanto o carro anda a dezenas de m/s, é razão.

    Volta sem amostras utilizáveis devolve `RATIO` — a leitura conservadora,
    porque tratar uma razão como m/s produziria escorregamentos absurdos e
    encheria o relatório de eventos falsos, enquanto o inverso apenas silencia a
    detecção.
    """
    usable = [p for p in points if p.speed_kmh >= MIN_SPEED_FOR_SLIP_KMH]
    if not usable:
        return SlipConvention.RATIO

    mean_speed_ms = sum(p.speed_kmh for p in usable) / len(usable) / 3.6
    mean_channel = sum(
        sum(getattr(p, f"tire_slip_{wheel}") for wheel in WHEELS) / len(WHEELS)
        for p in usable
    ) / len(usable)

    if mean_speed_ms <= 0:
        return SlipConvention.RATIO

    # Metade da velocidade do carro já é longe demais de "perto de 1" para ser
    # razão: um pneu que escorregasse tanto assim a volta inteira não andaria.
    return (
        SlipConvention.SURFACE_SPEED_MS
        if mean_channel > mean_speed_ms * 0.5
        else SlipConvention.RATIO
    )


def slip_ratio(
    point: TelemetryPoint, wheel: str, convention: SlipConvention
) -> float | None:
    """Razão de escorregamento da roda: 1.0 = rodando limpo.

    `None` quando o carro está devagar demais para o número significar algo.
    """
    if point.speed_kmh < MIN_SPEED_FOR_SLIP_KMH:
        return None

    raw = float(getattr(point, f"tire_slip_{wheel}"))
    if convention is SlipConvention.RATIO:
        return raw
    return raw / (point.speed_kmh / 3.6)


@dataclass(frozen=True, slots=True)
class TyreEvent:
    """Uma perda de aderência localizada."""

    kind: str
    """`"travamento"` ou `"patinagem"`."""

    wheel: str
    start_distance_m: float
    end_distance_m: float
    start_time_ms: int
    end_time_ms: int
    peak_ratio: float
    """Razão mais extrema no evento: < 1 em travamento, > 1 em patinagem."""

    @property
    def duration_ms(self) -> int:
        return self.end_time_ms - self.start_time_ms

    @property
    def severity_pct(self) -> float:
        """Quanto a roda se afastou da rodagem limpa, em pontos percentuais."""
        return abs(self.peak_ratio - 1.0) * 100.0

    def describe(self) -> str:
        return (
            f"{self.kind} em {self.wheel.upper()} aos {self.start_distance_m:.0f} m "
            f"({self.severity_pct:.0f}%, {self.duration_ms} ms)"
        )


def detect_tyre_events(
    points: list[TelemetryPoint],
    *,
    convention: SlipConvention | None = None,
    lockup_ratio: float = LOCKUP_RATIO,
    wheelspin_ratio: float = WHEELSPIN_RATIO,
    min_duration_ms: int = MIN_EVENT_DURATION_MS,
) -> list[TyreEvent]:
    """Travamentos e patinagens de uma volta, em ordem de distância.

    Travamento só é procurado com freio aplicado e patinagem só com acelerador:
    sem esse condicionamento, ruído do canal em trecho neutro viraria evento.
    """
    if len(points) < 3:
        return []

    resolved = convention if convention is not None else infer_slip_convention(points)

    lockup = _Criterion(
        kind="travamento",
        matches=lambda p, r: p.brake > 5.0 and r < lockup_ratio,
        extreme=min,
    )
    wheelspin = _Criterion(
        kind="patinagem",
        matches=lambda p, r: p.throttle > 20.0 and r > wheelspin_ratio,
        extreme=max,
    )

    events: list[TyreEvent] = []
    for wheel in WHEELS:
        # Travamento pode ocorrer em qualquer roda; patinagem só faz sentido nas
        # de tração. Assumir tração traseira é uma simplificação: o pacote GT7
        # não informa o layout do carro, e procurar patinagem nas quatro rodas
        # geraria falso positivo em todo carro dianteiro ou integral.
        events.extend(_scan_wheel(points, wheel, resolved, lockup, min_duration_ms))
        if wheel in REAR_WHEELS:
            events.extend(
                _scan_wheel(points, wheel, resolved, wheelspin, min_duration_ms)
            )

    events.sort(key=lambda e: (e.start_distance_m, e.wheel))
    return events


@dataclass(frozen=True, slots=True)
class _Criterion:
    """O que caracteriza um tipo de evento de aderência."""

    kind: str
    matches: SlipPredicate
    extreme: Extreme


def _scan_wheel(
    points: list[TelemetryPoint],
    wheel: str,
    convention: SlipConvention,
    criterion: _Criterion,
    min_duration_ms: int,
) -> list[TyreEvent]:
    """Varre uma roda procurando trechos contínuos que satisfaçam o critério."""
    found: list[TyreEvent] = []
    start: int | None = None
    ratios: list[float] = []

    def close(end: int) -> None:
        if start is None or end <= start or not ratios:
            return
        duration = points[end].elapsed_ms - points[start].elapsed_ms
        if duration < min_duration_ms:
            return
        found.append(
            TyreEvent(
                kind=criterion.kind,
                wheel=wheel,
                start_distance_m=points[start].distance_m,
                end_distance_m=points[end].distance_m,
                start_time_ms=points[start].elapsed_ms,
                end_time_ms=points[end].elapsed_ms,
                peak_ratio=criterion.extreme(ratios),
            )
        )

    for index, point in enumerate(points):
        ratio = slip_ratio(point, wheel, convention)
        if ratio is not None and criterion.matches(point, ratio):
            if start is None:
                start = index
                ratios = []
            ratios.append(ratio)
        elif start is not None:
            close(index - 1)
            start = None

    close(len(points) - 1)
    return found


@dataclass(frozen=True, slots=True)
class TyreBalance:
    """Desequilíbrio térmico dos pneus numa volta.

    Os deltas são o que um engenheiro lê primeiro; as médias por roda ficam
    disponíveis para o gráfico.
    """

    average_by_wheel: dict[str, float]
    front_rear_delta_c: float
    """Positivo = dianteiros mais quentes que traseiros."""

    left_right_delta_c: float
    """Positivo = lado esquerdo mais quente que o direito."""

    hottest_wheel: str
    peak_temp_c: float

    def describe(self) -> str:
        """Leitura do desequilíbrio, com o cuidado de não afirmar demais.

        Temperatura é indício, não diagnóstico: dianteiros quentes são
        *compatíveis* com subesterço, mas também com pressão baixa ou com um
        circuito de curvas lentas. O texto reflete essa incerteza de propósito —
        um relatório que afirma "seu carro tem subesterço" a partir de dois
        graus de diferença perde a confiança do piloto na primeira vez que erra.
        """
        notes: list[str] = []

        if self.front_rear_delta_c > 8:
            notes.append(
                f"dianteiros {self.front_rear_delta_c:.0f} °C mais quentes — "
                "compatível com subesterço ou pressão baixa na frente"
            )
        elif self.front_rear_delta_c < -8:
            notes.append(
                f"traseiros {abs(self.front_rear_delta_c):.0f} °C mais quentes — "
                "compatível com sobre-esterço ou tração escorregando na saída"
            )

        if abs(self.left_right_delta_c) > 10:
            side = "esquerdo" if self.left_right_delta_c > 0 else "direito"
            notes.append(
                f"lado {side} {abs(self.left_right_delta_c):.0f} °C mais quente — "
                "esperado num circuito com curvas predominantes num sentido"
            )

        if not notes:
            return "temperaturas equilibradas entre eixos e lados"
        return "; ".join(notes)


def temperature_balance(points: list[TelemetryPoint]) -> TyreBalance | None:
    """Médias e desequilíbrios térmicos de uma volta. None sem amostras."""
    if not points:
        return None

    averages = {
        wheel: sum(getattr(p, f"tire_temp_{wheel}") for p in points) / len(points)
        for wheel in WHEELS
    }
    front = (averages["fl"] + averages["fr"]) / 2.0
    rear = (averages["rl"] + averages["rr"]) / 2.0
    left = (averages["fl"] + averages["rl"]) / 2.0
    right = (averages["fr"] + averages["rr"]) / 2.0

    hottest = max(averages, key=lambda w: averages[w])
    peak = max(
        getattr(p, f"tire_temp_{wheel}") for p in points for wheel in WHEELS
    )

    return TyreBalance(
        average_by_wheel=averages,
        front_rear_delta_c=front - rear,
        left_right_delta_c=left - right,
        hottest_wheel=hottest,
        peak_temp_c=peak,
    )


@dataclass(frozen=True, slots=True)
class StintDegradation:
    """Tendência ao longo de um stint de várias voltas."""

    lap_count: int
    temperature_trend_c_per_lap: float
    """Inclinação da temperatura média por volta. Positiva = aquecendo."""

    pace_trend_ms_per_lap: float
    """Inclinação do tempo de volta. Positiva = perdendo ritmo."""

    first_lap_time_ms: int
    last_lap_time_ms: int

    def describe(self) -> str:
        if self.lap_count < 3:
            return "stint curto demais para tendência confiável"

        notes = [
            f"ritmo {'caindo' if self.pace_trend_ms_per_lap > 0 else 'melhorando'} "
            f"{abs(self.pace_trend_ms_per_lap) / 1000:.3f} s por volta"
        ]
        if abs(self.temperature_trend_c_per_lap) > 0.5:
            direction = "subindo" if self.temperature_trend_c_per_lap > 0 else "caindo"
            notes.append(
                f"temperatura {direction} {abs(self.temperature_trend_c_per_lap):.1f} "
                "°C por volta"
            )
        return "; ".join(notes)


def stint_degradation(laps: list[list[TelemetryPoint]]) -> StintDegradation | None:
    """Tendência de temperatura e de ritmo ao longo das voltas informadas.

    As voltas devem estar em ordem cronológica. Menos de duas devolve None —
    tendência de um ponto só não existe.

    A inclinação vem de regressão linear simples. Com 20 voltas (o tamanho da
    janela de retenção configurada por pista) isso é suficiente e é interpretável;
    um ajuste mais sofisticado daria um número que ninguém saberia ler.
    """
    usable = [lap for lap in laps if lap]
    if len(usable) < 2:
        return None

    temperatures = [
        sum(
            sum(getattr(p, f"tire_temp_{w}") for w in WHEELS) / len(WHEELS)
            for p in lap
        )
        / len(lap)
        for lap in usable
    ]
    lap_times = [lap[-1].elapsed_ms for lap in usable]

    return StintDegradation(
        lap_count=len(usable),
        temperature_trend_c_per_lap=_slope(temperatures),
        pace_trend_ms_per_lap=_slope([float(t) for t in lap_times]),
        first_lap_time_ms=lap_times[0],
        last_lap_time_ms=lap_times[-1],
    )


def _slope(values: list[float]) -> float:
    """Inclinação da reta de mínimos quadrados contra o índice (0, 1, 2, ...)."""
    count = len(values)
    if count < 2:
        return 0.0

    mean_x = (count - 1) / 2.0
    mean_y = sum(values) / count
    numerator = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    denominator = sum((i - mean_x) ** 2 for i in range(count))
    return numerator / denominator if denominator else 0.0
