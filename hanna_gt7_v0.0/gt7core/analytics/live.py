"""
Detecção de eventos numa volta **em andamento**.

A análise da Fase 4 olha uma volta inteira depois que ela termina: ordena,
varre, compara. Aqui não existe "depois" — o piloto está na pista, e a única
informação disponível é a amostra que acabou de chegar. É outra forma de
resolver o mesmo problema, e por isso um módulo separado em vez de um parâmetro
nos detectores existentes.

Três exigências moldam tudo o que está aqui
-------------------------------------------
**Custo constante por amostra.** Isto roda a 60 Hz na thread de captura, ao lado
da gravação. Revarrer o buffer da volta a cada quadro seria O(n²) ao longo da
volta — o mesmo defeito que a Fase 8 encontrou no gráfico, só que num lugar onde
travaria a captura em vez da tela.

**Concordar com o debrief.** Se o rádio anuncia um travamento na Curva 3 e o
debrief da mesma volta não lista nenhum, o piloto deixa de confiar nos dois. Os
limiares e a função de razão de escorregamento são **importados** de `tyres` e
`throttle`, nunca recopiados: mudar um limiar muda os dois lados juntos.

**Silêncio é resposta válida.** Um detector ao vivo que dispara demais é pior
que nenhum: o rádio vira ruído e o piloto aprende a ignorá-lo. Cada tipo de
evento tem tempo de rearme, e a convenção de unidade só é decidida depois de
amostras suficientes — antes disso a detecção fica calada em vez de chutar.

A convenção de unidade, ao vivo
-------------------------------
`infer_slip_convention` compara médias da volta inteira, o que não existe aqui.
A versão incremental acumula as mesmas duas médias amostra a amostra e decide
quando tiver base suficiente. Até lá não emite nada — alguns segundos de
silêncio no começo da sessão custam menos que uma volta inteira de eventos
falsos, que é o que sai ao ler uma razão como se fosse m/s.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.models import TelemetryPoint
from .throttle import LIFT_DROP_PCT, THROTTLE_APPLICATION_PCT
from .tyres import (
    FRONT_WHEELS,
    LOCKUP_RATIO,
    MIN_EVENT_DURATION_MS,
    MIN_SPEED_FOR_SLIP_KMH,
    REAR_WHEELS,
    WHEELSPIN_RATIO,
    SlipConvention,
    slip_ratio,
)

# Amostras necessárias antes de decidir a convenção. A 60 Hz são ~1,7 s: o
# bastante para uma média estável e pouco o suficiente para o rádio não parecer
# morto no começo da sessão.
CONVENTION_WARMUP_SAMPLES = 100

# Rearme por tipo de evento. Um travamento longo é um travamento, não trinta.
REARM_MS = 1500

# Um alívio só conta depois que o acelerador chegou a este patamar: soltar o pé
# vindo de 20% é transição normal de curva, não erro de saída.
LIFT_FROM_PCT = 60.0


@dataclass(frozen=True, slots=True)
class RaceEvent:
    """Algo que acabou de acontecer e que vale dizer no rádio."""

    kind: str
    """`travamento` | `patinagem` | `alivio` | `perdendo`."""

    distance_m: float
    elapsed_ms: int

    severity: float = 0.0
    """Quanto o valor se afastou do limiar. Serve para priorizar, não para exibir."""

    detail: str = ""
    """Frase curta, já em português, pronta para entrar no contexto do prompt."""

    def describe(self) -> str:
        return self.detail or self.kind


@dataclass(frozen=True, slots=True)
class RaceEventDetected:
    """Evento de barramento: algo aconteceu na volta em andamento.

    Publicado pelo núcleo, consumido por quem quiser — a interface pede uma nota
    de rádio ao engenheiro, o bot do Discord manda no celular, a voz fala. O
    núcleo **não** decide o que fazer com isso, e é essa indiferença que faz o
    mesmo detector servir aos três sem nenhum `if`.
    """

    event: RaceEvent


@dataclass
class _Window:
    """Estado de um evento em andamento numa dupla de rodas."""

    started_ms: int | None = None
    extreme: float = 0.0
    last_emitted_ms: int = -REARM_MS

    def rearmed(self, now_ms: int) -> bool:
        return now_ms - self.last_emitted_ms >= REARM_MS


@dataclass
class LiveEventDetector:
    """Consome amostras uma a uma e devolve os eventos que fecharam.

    Uso:

        detector = LiveEventDetector()
        for point in stream:
            for event in detector.feed(point):
                ...

    `feed` devolve **lista** porque um mesmo quadro pode fechar mais de um
    evento — travar a dianteira e patinar a traseira ao mesmo tempo é raro, mas
    acontece numa saída mal feita de curva lenta.
    """

    _convention: SlipConvention | None = None
    _samples: int = 0
    _speed_sum_ms: float = 0.0
    _channel_sum: float = 0.0

    _front: _Window = field(default_factory=_Window)
    _rear: _Window = field(default_factory=_Window)

    _throttle_peak: float = 0.0
    _last_lift_ms: int = -REARM_MS

    _last_delta_ms: float | None = None
    _last_delta_event_ms: int = -REARM_MS

    # ------------------------------------------------------------------

    @property
    def convention(self) -> SlipConvention | None:
        """A convenção decidida, ou None enquanto ainda está aquecendo."""
        return self._convention

    @property
    def is_ready(self) -> bool:
        return self._convention is not None

    def new_lap(self) -> None:
        """Fecha o que estava aberto. A convenção **persiste**: é do carro, não
        da volta, e reaprender a cada volta traria de volta o silêncio inicial
        toda vez que o piloto cruzasse a linha."""
        self._front = _Window(last_emitted_ms=-REARM_MS)
        self._rear = _Window(last_emitted_ms=-REARM_MS)
        self._throttle_peak = 0.0
        self._last_delta_ms = None

    def reset(self) -> None:
        """Esquece tudo, inclusive a convenção. Para troca de carro ou sessão."""
        self._convention = None
        self._samples = 0
        self._speed_sum_ms = 0.0
        self._channel_sum = 0.0
        self.new_lap()

    # ------------------------------------------------------------------

    def feed(self, point: TelemetryPoint) -> list[RaceEvent]:
        """Processa uma amostra. Custo constante — nada aqui varre histórico."""
        self._learn_convention(point)
        if self._convention is None:
            return []

        events: list[RaceEvent] = []
        events.extend(self._check_grip(point))
        lift = self._check_lift(point)
        if lift is not None:
            events.append(lift)
        return events

    def feed_delta(self, delta_ms: float, point: TelemetryPoint) -> RaceEvent | None:
        """Delta contra a referência, quando houver uma.

        Vem por método separado porque o delta não é um canal da telemetria: é
        derivado de uma volta de referência que pode não existir. Um detector
        que exigisse delta ficaria mudo na primeira sessão numa pista nova,
        justamente quando o piloto mais precisa de ajuda.
        """
        previous = self._last_delta_ms
        self._last_delta_ms = delta_ms
        if previous is None:
            return None

        growth = delta_ms - previous
        if growth < DELTA_WARN_MS:
            return None
        if point.elapsed_ms - self._last_delta_event_ms < REARM_MS:
            return None

        self._last_delta_event_ms = point.elapsed_ms
        return RaceEvent(
            kind="perdendo",
            distance_m=point.distance_m,
            elapsed_ms=point.elapsed_ms,
            severity=growth,
            detail=f"perdeu {growth / 1000:.2f} s para a referência neste trecho",
        )

    # ------------------------------------------------------------------

    def _learn_convention(self, point: TelemetryPoint) -> None:
        if self._convention is not None or point.speed_kmh < MIN_SPEED_FOR_SLIP_KMH:
            return

        self._samples += 1
        self._speed_sum_ms += point.speed_kmh / 3.6
        self._channel_sum += (
            sum(getattr(point, f"tire_slip_{w}") for w in FRONT_WHEELS + REAR_WHEELS)
            / 4.0
        )

        if self._samples < CONVENTION_WARMUP_SAMPLES:
            return

        mean_speed = self._speed_sum_ms / self._samples
        mean_channel = self._channel_sum / self._samples
        # O mesmo critério de `infer_slip_convention`, sobre médias acumuladas.
        self._convention = (
            SlipConvention.SURFACE_SPEED_MS
            if mean_speed > 0 and mean_channel > mean_speed * 0.5
            else SlipConvention.RATIO
        )

    def _check_grip(self, point: TelemetryPoint) -> list[RaceEvent]:
        assert self._convention is not None
        events: list[RaceEvent] = []

        front = _axle_ratio(point, FRONT_WHEELS, self._convention, worst="min")
        rear = _axle_ratio(point, REAR_WHEELS, self._convention, worst="max")

        locked = front is not None and front < LOCKUP_RATIO and point.brake > 0
        event = self._track(self._front, point, locked, front or 1.0, "travamento")
        if event is not None:
            events.append(event)

        spinning = (
            rear is not None
            and rear > WHEELSPIN_RATIO
            and point.throttle > THROTTLE_APPLICATION_PCT
        )
        event = self._track(self._rear, point, spinning, rear or 1.0, "patinagem")
        if event is not None:
            events.append(event)

        return events

    def _track(
        self,
        window: _Window,
        point: TelemetryPoint,
        active: bool,
        value: float,
        kind: str,
    ) -> RaceEvent | None:
        """Máquina de estados de um eixo. O evento nasce ao **fechar**.

        Emitir na abertura seria mais rápido, e errado: sem saber a duração não
        dá para distinguir um travamento de um repique de leitura, e o limiar de
        duração é justamente o que a Fase 4 usa para não encher o relatório de
        ruído.
        """
        if active:
            if window.started_ms is None:
                window.started_ms = point.elapsed_ms
                window.extreme = value
            elif kind == "travamento":
                window.extreme = min(window.extreme, value)
            else:
                window.extreme = max(window.extreme, value)
            return None

        started = window.started_ms
        window.started_ms = None
        if started is None:
            return None

        duration = point.elapsed_ms - started
        if duration < MIN_EVENT_DURATION_MS or not window.rearmed(point.elapsed_ms):
            return None

        window.last_emitted_ms = point.elapsed_ms
        wheels = "dianteira" if kind == "travamento" else "traseira"
        return RaceEvent(
            kind=kind,
            distance_m=point.distance_m,
            elapsed_ms=point.elapsed_ms,
            severity=abs(1.0 - window.extreme),
            detail=f"{kind} na {wheels} por {duration} ms",
        )

    def _check_lift(self, point: TelemetryPoint) -> RaceEvent | None:
        """Alívio de acelerador: o pé saiu no meio da saída de curva."""
        throttle = point.throttle

        if point.brake > 0:
            # Frear zera o pico: soltar o acelerador para frear não é alívio.
            self._throttle_peak = 0.0
            return None

        if throttle >= self._throttle_peak:
            self._throttle_peak = throttle
            return None

        dropped = self._throttle_peak - throttle
        if self._throttle_peak < LIFT_FROM_PCT or dropped < LIFT_DROP_PCT:
            return None
        if point.elapsed_ms - self._last_lift_ms < REARM_MS:
            return None

        self._last_lift_ms = point.elapsed_ms
        peak = self._throttle_peak
        self._throttle_peak = throttle
        return RaceEvent(
            kind="alivio",
            distance_m=point.distance_m,
            elapsed_ms=point.elapsed_ms,
            severity=dropped,
            detail=f"aliviou o acelerador de {peak:.0f}% para {throttle:.0f}% na saída",
        )


# Crescimento de delta, dentro de uma janela, que merece aviso. Abaixo disto é
# variação normal entre voltas do mesmo piloto — o mesmo raciocínio do limiar de
# significância da Fase 4.
DELTA_WARN_MS = 150.0


def _axle_ratio(
    point: TelemetryPoint,
    wheels: tuple[str, ...],
    convention: SlipConvention,
    *,
    worst: str,
) -> float | None:
    """A razão mais extrema do eixo. `None` se o carro está devagar demais.

    Por eixo e não por roda porque o rádio fala de incidentes: travar as duas
    dianteiras é **um** travamento para quem está dirigindo. A detecção por roda
    continua existindo no debrief, onde a distinção entre uma e duas rodas é um
    diagnóstico diferente e há espaço para explicá-la.
    """
    ratios = [
        ratio
        for wheel in wheels
        if (ratio := slip_ratio(point, wheel, convention)) is not None
    ]
    if not ratios:
        return None
    return min(ratios) if worst == "min" else max(ratios)
