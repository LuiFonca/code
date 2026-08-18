"""
Modelos de domínio — dataclasses puras, sem Qt, sem SQL, sem rede.

Portados de `src/domain/models/` sem mudança de semântica. As propriedades
derivadas e a política de carga preguiçosa (`points` vazio nas listagens)
estavam corretas e foram preservadas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class TelemetryPoint:
    """Uma amostra de telemetria pronta para análise e persistência.

    Distinta do `TelemetryFrame` (o DTO do formato de fio do GT7): este é o que
    o domínio entende. Dois campos **não vêm no pacote** e são derivados na
    entrada pelo `TelemetryEngine`:

    - `distance_m`: distância acumulada na volta (o GT7 não transmite hodômetro);
    - `g_lateral` / `g_longitudinal`: derivados do vetor velocidade.

    `slots=True` não é preciosismo: uma volta guarda alguns milhares destes, e a
    comparação carrega duas voltas ao mesmo tempo.
    """

    elapsed_ms: int
    distance_m: float
    speed_kmh: float
    rpm: float
    gear: int
    throttle: float
    brake: float
    fuel_level: float
    tire_temp_fl: float
    tire_temp_fr: float
    tire_temp_rl: float
    tire_temp_rr: float
    position_x: float
    position_z: float
    g_lateral: float
    g_longitudinal: float
    suspension_fl: float
    suspension_fr: float
    suspension_rl: float
    suspension_rr: float
    tire_slip_fl: float
    tire_slip_fr: float
    tire_slip_rl: float
    tire_slip_rr: float
    turbo_boost: float
    oil_temp: float
    water_temp: float

    flags: int | None = None
    """Bitfield de estado do pacote (0x8E) — inclui TCS e ASM atuando.

    `None`, e não 0, em voltas gravadas antes deste campo existir: 0 afirmaria
    "nenhum auxílio atuou", e isso não foi medido. A tela precisa distinguir
    "não atuou" de "não foi gravado", senão uma volta antiga passa a alegar
    pilotagem limpa que ninguém verificou.

    Guardado como inteiro em vez de dois booleanos de propósito: o bit do ABS
    ainda não está identificado na engenharia reversa, e quando estiver, ele já
    terá sido gravado — sem outra migração e sem perder as voltas de agora.
    """

    @property
    def elapsed_s(self) -> float:
        return self.elapsed_ms / 1000.0

    @property
    def tire_temp_avg(self) -> float:
        return (
            self.tire_temp_fl + self.tire_temp_fr
            + self.tire_temp_rl + self.tire_temp_rr
        ) / 4.0

    @property
    def tire_slip_avg(self) -> float:
        return (
            abs(self.tire_slip_fl) + abs(self.tire_slip_fr)
            + abs(self.tire_slip_rl) + abs(self.tire_slip_rr)
        ) / 4.0


@dataclass(slots=True)
class Car:
    id: int | None = None
    name: str = ""
    maker: str | None = None


@dataclass(slots=True)
class Track:
    id: int | None = None
    name: str = ""
    length_m: float | None = None


@dataclass(slots=True)
class Lap:
    """Uma volta gravada.

    `points` é opcional de propósito: listar o histórico não deve carregar
    milhares de amostras por volta só para mostrar uma tabela de tempos. Os
    repositórios devolvem a volta com `points` vazio nas listagens e preenchem
    sob demanda. Use `has_points` antes de calcular métrica derivada.

    `lap_time_ms` é o tempo **oficial** do jogo, não a soma dos intervalos das
    amostras — os dois divergem alguns milissegundos e o do jogo é a fonte de
    verdade para recordes.
    """

    id: int | None = None
    session_id: int | None = None
    car_id: int | None = None
    track_id: int | None = None
    lap_time_ms: int = 0
    start_time: datetime | None = None
    end_time: datetime | None = None
    is_player: bool = True
    points: list[TelemetryPoint] = field(default_factory=list)

    @property
    def has_points(self) -> bool:
        return len(self.points) > 0

    @property
    def duration_ms(self) -> int:
        """Prefere o tempo oficial; cai para o decorrido da última amostra
        enquanto a volta ainda está em andamento."""
        if self.lap_time_ms > 0:
            return self.lap_time_ms
        return self.points[-1].elapsed_ms if self.points else 0

    @property
    def distance_m(self) -> float:
        return self.points[-1].distance_m if self.points else 0.0

    @property
    def avg_speed(self) -> float:
        if not self.points:
            return 0.0
        return sum(p.speed_kmh for p in self.points) / len(self.points)

    @property
    def max_speed(self) -> float:
        return max((p.speed_kmh for p in self.points), default=0.0)

    @property
    def fuel_used(self) -> float | None:
        """None quando a volta não tem amostras ou veio de schema antigo."""
        if not self.points:
            return None
        used = self.points[0].fuel_level - self.points[-1].fuel_level
        return used if used >= 0 else None


@dataclass(slots=True)
class Session:
    """Uma sessão: carro + pista + janela de tempo + voltas rodadas.

    `end` nulo significa sessão em andamento.
    """

    id: int | None = None
    car: Car | None = None
    track: Track | None = None
    start: datetime | None = None
    end: datetime | None = None
    laps: list[Lap] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        return self.end is None

    @property
    def lap_count(self) -> int:
        return len(self.laps)

    @property
    def best_lap(self) -> Lap | None:
        """Volta mais rápida da sessão, ignorando voltas sem tempo válido."""
        timed = [lap for lap in self.laps if lap.lap_time_ms > 0]
        return min(timed, key=lambda lap: lap.lap_time_ms) if timed else None

    @property
    def last_lap(self) -> Lap | None:
        return self.laps[-1] if self.laps else None

    @property
    def duration_s(self) -> float | None:
        if self.start is None or self.end is None:
            return None
        return (self.end - self.start).total_seconds()

    def add_lap(self, lap: Lap) -> None:
        self.laps.append(lap)
