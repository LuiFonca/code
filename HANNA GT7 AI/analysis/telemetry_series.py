"""
Wrapper em torno dos frames de uma volta que permite consultar o valor de
qualquer canal de telemetria (velocidade, marcha, freio, etc.) numa
distância arbitrária, interpolando entre os pontos salvos.

Usado pela aba de Comparação para sincronizar múltiplos gráficos pelo mesmo
eixo de distância: ao passar o mouse num ponto de dado gráfico, os outros
gráficos e o painel de valores usam esta classe para descobrir o valor de
cada canal naquele exato ponto da pista, mesmo que os frames originais das
duas voltas não tenham sido amostrados nas mesmas distâncias.
"""

import bisect

# Índices das colunas retornadas por lap_storage.get_lap_frames():
# (elapsed_ms, distance_m, speed_kmh, rpm, gear, throttle, brake,
#  fuel_level, tire_temp_fl, tire_temp_fr, tire_temp_rl, tire_temp_rr)
CHANNELS = {
    "elapsed_ms": 0,
    "distance_m": 1,
    "speed_kmh": 2,
    "rpm": 3,
    "gear": 4,
    "throttle": 5,
    "brake": 6,
    "fuel_level": 7,
    "tire_temp_fl": 8,
    "tire_temp_fr": 9,
    "tire_temp_rl": 10,
    "tire_temp_rr": 11,
}


class LapSeries:
    def __init__(self, frames: list):
        """frames: lista de tuplas de lap_storage.get_lap_frames()."""
        self._frames = frames
        self._distances = [row[1] for row in frames]

    @property
    def is_empty(self) -> bool:
        return len(self._frames) < 2

    @property
    def max_distance(self) -> float:
        return self._distances[-1] if self._distances else 0.0

    def points(self, channel: str):
        """Retorna a lista de pares (distance_m, valor) prontos para um
        gráfico — sem interpolação, um ponto por frame salvo."""
        idx = CHANNELS[channel]
        return [(row[1], row[idx]) for row in self._frames]

    def value_at(self, distance_m: float, channel: str):
        """Valor interpolado do canal na distância informada. Retorna None
        se a distância estiver fora do intervalo coberto pela volta."""
        if self.is_empty:
            return None
        if distance_m < self._distances[0] or distance_m > self._distances[-1]:
            return None

        idx = CHANNELS[channel]
        index = bisect.bisect_left(self._distances, distance_m)
        if index == 0:
            return self._frames[0][idx]

        d0, d1 = self._distances[index - 1], self._distances[index]
        v0, v1 = self._frames[index - 1][idx], self._frames[index][idx]

        if d1 == d0:
            return v0
        ratio = (distance_m - d0) / (d1 - d0)
        return v0 + ratio * (v1 - v0)

    def elapsed_ms_at(self, distance_m: float):
        return self.value_at(distance_m, "elapsed_ms")


def compute_delta_series(series_a: LapSeries, series_b: LapSeries, num_points: int = 200):
    """Calcula o delta (tempo de B menos tempo de A) ao longo da distância,
    amostrado em pontos igualmente espaçados dentro do trecho em que as
    duas voltas se sobrepõem. Retorna uma lista de (distance_m, delta_seconds).

    delta positivo = B está mais devagar que A naquele ponto;
    delta negativo = B está mais rápido."""
    if series_a.is_empty or series_b.is_empty:
        return []

    max_distance = min(series_a.max_distance, series_b.max_distance)
    if max_distance <= 0:
        return []

    step = max_distance / num_points
    result = []
    for i in range(num_points + 1):
        d = i * step
        ta = series_a.elapsed_ms_at(d)
        tb = series_b.elapsed_ms_at(d)
        if ta is None or tb is None:
            continue
        result.append((d, (tb - ta) / 1000))
    return result
