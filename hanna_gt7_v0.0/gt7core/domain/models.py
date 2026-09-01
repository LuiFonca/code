"""
Modelos de domínio — dataclasses puras, sem Qt, sem SQL, sem rede.

Portados de `src/domain/models/` sem mudança de semântica. As propriedades
derivadas e a política de carga preguiçosa (`points` vazio nas listagens)
estavam corretas e foram preservadas.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from .validity import LapValidity, classify_lap, lap_coverage


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

    position_y: float | None = None
    """Altitude, em metros. **Y é a vertical no GT7** — X e Z são o plano.

    `None` em voltas gravadas antes deste campo existir. O pacote sempre trouxe
    esta coordenada; o programa é que a lia e a descartava aqui, na fronteira do
    domínio, e com ela ia embora toda a noção de subida e descida.
    """

    road_plane_x: float | None = None
    road_plane_y: float | None = None
    road_plane_z: float | None = None
    """Normal do asfalto sob o carro — inclinação e sobrelevação **medidas**.

    Guardada crua, e não já convertida em "rampa de 4%", por uma razão de
    fidelidade: a conversão depende da direção em que o carro anda, e essa é
    uma interpretação. Interpretação errada se corrige relendo o dado; dado
    convertido na gravação se perde para sempre.

    `None` junto, os três, ou nenhum: vêm do mesmo vetor e a validação é do
    vetor inteiro (ver `TelemetryFrame.road_plane_is_valid`). Meio vetor não
    tem significado.
    """

    @property
    def elapsed_s(self) -> float:
        return self.elapsed_ms / 1000.0

    @property
    def has_road_normal(self) -> bool:
        return (
            self.road_plane_x is not None
            and self.road_plane_y is not None
            and self.road_plane_z is not None
        )

    @property
    def road_gradient_pct(self) -> float | None:
        """Declividade máxima do asfalto no ponto, em %, **sem sinal**.

        É a rampa da ladeira mais íngreme que passa por ali, e não a que o
        carro está subindo: esta não depende da direção de marcha, e por isso
        cabe no ponto isolado. A inclinação com sinal — subindo ou descendo —
        precisa da trajetória e mora em `analytics.elevation`.
        """
        if not self.has_road_normal:
            return None
        horizontal = math.hypot(self.road_plane_x, self.road_plane_z)
        return 100.0 * horizontal / self.road_plane_y

    @property
    def boost_bar(self) -> float:
        """Pressão de turbo em bar, com zero na atmosférica.

        O pacote manda `turbo_boost` com **1,0 como pressão atmosférica**, não
        como zero — é pressão absoluta, e é por isso que um carro aspirado
        transmite 1,0 parado em vez de 0,0. Subtrair 1 devolve a pressão de
        sobrealimentação, que é o número que aparece no manômetro do carro e o
        que o piloto chama de "1,2 bar".

        Valores negativos são reais e não são erro: fora do acelerador o motor
        aspira contra a borboleta fechada e a pressão cai abaixo da atmosférica.
        Cortá-los em zero esconderia justamente as trocas de marcha e os alívios.
        """
        return self.turbo_boost - 1.0

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

    distance_m: float | None = None
    """Distância percorrida, do hodômetro. `None` em voltas antigas.

    Vive na volta, e não só nas amostras, porque comparar com o comprimento
    oficial da pista é o que diz se a volta deu a volta — e essa comparação
    precisa acontecer na consulta que escolhe o recorde.
    """

    track_length_m: float | None = None
    """Comprimento oficial da pista, quando conhecido. Preenchido na leitura."""

    @property
    def has_points(self) -> bool:
        return len(self.points) > 0

    @property
    def measured_distance_m(self) -> float | None:
        """A distância desta volta: a gravada, ou a das amostras.

        A queda para as amostras é o que faz a validade funcionar na volta que
        acabou de fechar e ainda não foi para o banco.
        """
        if self.distance_m is not None:
            return self.distance_m
        return self.points[-1].distance_m if self.points else None

    @property
    def coverage(self) -> float | None:
        """Fração da pista que esta volta percorreu. `None` sem comparação."""
        return lap_coverage(self.measured_distance_m, self.track_length_m)

    @property
    def validity(self) -> LapValidity:
        """A volta cobriu a pista? `UNKNOWN` quando não há com o que comparar."""
        return classify_lap(self.measured_distance_m, self.track_length_m)

    @property
    def duration_ms(self) -> int:
        """Prefere o tempo oficial; cai para o decorrido da última amostra
        enquanto a volta ainda está em andamento."""
        if self.lap_time_ms > 0:
            return self.lap_time_ms
        return self.points[-1].elapsed_ms if self.points else 0

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
