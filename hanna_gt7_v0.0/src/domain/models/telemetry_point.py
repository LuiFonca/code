"""
Amostra única de telemetria, já normalizada para o domínio.
"""

from dataclasses import dataclass


@dataclass(slots=True)
class TelemetryPoint:
    """Uma amostra de telemetria pronta para análise e persistência.

    Distinta do `TelemetryFrame` da camada de infraestrutura: aquele é o DTO
    do formato de fio do GT7 (41 campos, offsets de bytes, bitfield de flags).
    Este é o que o domínio entende — só o que interessa para análise, com dois
    campos que **não vêm no pacote** e são derivados na entrada:

    - `distance_m`: distância acumulada na volta, integrada a partir da
      velocidade (o GT7 não transmite hodômetro por volta).
    - `g_lateral` / `g_longitudinal`: forças G obtidas derivando o vetor de
      velocidade e projetando nos eixos do carro.

    `slots=True` não é preciosismo: uma volta guarda alguns milhares destes,
    e a comparação carrega duas voltas ao mesmo tempo.
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

    @property
    def elapsed_s(self) -> float:
        """Tempo decorrido em segundos — usado no eixo temporal dos gráficos."""
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
