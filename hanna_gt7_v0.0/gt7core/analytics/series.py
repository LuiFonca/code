"""
Consulta e comparação de canais de telemetria de uma volta.

Permite perguntar "qual era a velocidade aos 800 m?" mesmo que nenhuma amostra
tenha caído exatamente nessa distância, interpolando entre as vizinhas. É o que
sincroniza vários gráficos pelo mesmo eixo: ao passar o mouse num ponto, todos
os outros gráficos e o painel de valores consultam a mesma distância.

Mudança em relação à versão antiga (`analysis/telemetry_series.py`)
-------------------------------------------------------------------
Antes, os canais eram acessados por índice de tupla (`CHANNELS = {"speed_kmh": 2,
...}`), o que fazia a ordem das colunas do SQLite vazar até a camada de
gráficos: inserir uma coluna no meio da tabela quebrava os gráficos em silêncio.
Agora o acesso é por atributo do `TelemetryPoint`, e a conversão linha↔objeto
fica confinada ao repositório.
"""

import bisect
from dataclasses import fields

from ..domain.models import TelemetryPoint

# Nomes de canal válidos, derivados do próprio modelo. Um erro de digitação em
# nome de canal falha na hora, com mensagem clara, em vez de virar AttributeError
# no meio da renderização.
CHANNELS: frozenset[str] = frozenset(f.name for f in fields(TelemetryPoint))


class LapSeries:
    """Wrapper de consulta sobre as amostras de uma volta.

    Tolera lacunas: voltas gravadas por versões antigas do schema podem ter
    `None` em combustível, pneus ou posição. Amostras nulas são tratadas como
    "ausentes" em vez de propagarem para os gráficos (que quebrariam ao tentar
    desenhar ou fazer aritmética com None).
    """

    def __init__(self, points: list[TelemetryPoint]):
        self._points = points
        self._distances = [p.distance_m for p in points]
        self._channel_cache: dict[str, list[tuple[float, float]]] = {}

    @property
    def is_empty(self) -> bool:
        return len(self._points) < 2

    @property
    def points_raw(self) -> list[TelemetryPoint]:
        return self._points

    @property
    def max_distance(self) -> float:
        return self._distances[-1] if self._distances else 0.0

    @property
    def max_time(self) -> float:
        """Duração da volta em segundos, pelo eixo temporal."""
        if not self._points:
            return 0.0
        last = self._points[-1].elapsed_ms
        return (last / 1000.0) if last is not None else 0.0

    def has_channel(self, channel: str) -> bool:
        """True se o canal tiver ao menos dois valores não-nulos nesta volta."""
        return len(self._valid_points(channel)) >= 2

    def _valid_points(self, channel: str) -> list[tuple[float, float]]:
        """Pares (distância, valor) do canal, pulando amostras nulas.

        Cacheado por canal: as amostras não mudam depois de carregadas, e a
        comparação consulta o mesmo canal repetidamente ao mover o mouse.
        """
        cached = self._channel_cache.get(channel)
        if cached is not None:
            return cached

        if channel not in CHANNELS:
            raise KeyError(
                f"Canal desconhecido: {channel!r}. "
                f"Válidos: {', '.join(sorted(CHANNELS))}"
            )

        pairs = [
            (p.distance_m, getattr(p, channel))
            for p in self._points
            if getattr(p, channel) is not None
        ]
        self._channel_cache[channel] = pairs
        return pairs

    def points(self, channel: str) -> list[tuple[float, float]]:
        """Pares (distância_m, valor) prontos para plotar — um por amostra,
        sem interpolação nem reamostragem."""
        return self._valid_points(channel)

    def points_by_time(self, channel: str) -> list[tuple[float, float]]:
        """Pares (segundos, valor) para o eixo temporal."""
        if channel not in CHANNELS:
            raise KeyError(f"Canal desconhecido: {channel!r}")
        return [
            (p.elapsed_ms / 1000.0, getattr(p, channel))
            for p in self._points
            if getattr(p, channel) is not None and p.elapsed_ms is not None
        ]

    def value_at(self, distance_m: float, channel: str) -> float | None:
        """Valor do canal na distância informada, interpolado entre as amostras
        vizinhas. None fora do trecho coberto pela volta ou se o canal não tiver
        dados suficientes."""
        distances, values = self._channel_arrays(channel)
        if not distances:
            return None
        if distance_m < distances[0] or distance_m > distances[-1]:
            return None

        index = bisect.bisect_left(distances, distance_m)
        if index == 0:
            return values[0]

        d0, d1 = distances[index - 1], distances[index]
        v0, v1 = values[index - 1], values[index]
        if d1 == d0:
            return v0
        ratio = (distance_m - d0) / (d1 - d0)
        return v0 + ratio * (v1 - v0)

    def _channel_arrays(self, channel: str) -> tuple[list[float], list[float]]:
        pairs = self._valid_points(channel)
        if not pairs:
            return [], []
        # strict=False é o correto aqui: `pairs` é uma lista de tuplas (dist,
        # valor) de tamanho fixo 2, então não há sequências de comprimentos
        # diferentes para o strict=True proteger.
        distances, values = zip(*pairs, strict=False)
        return list(distances), list(values)

    def elapsed_ms_at(self, distance_m: float) -> float | None:
        return self.value_at(distance_m, "elapsed_ms")

    def position_points(self) -> list[tuple[float, float]]:
        """Pares (x, z) para desenhar o traçado da volta.

        As duas coordenadas são sempre gravadas juntas, então uma amostra só
        aparece aqui quando ambas existem. Voltas anteriores ao schema v4 não
        têm traçado."""
        return [
            (p.position_x, p.position_z)
            for p in self._points
            if p.position_x is not None and p.position_z is not None
        ]


def compute_delta_series(
    series_a: LapSeries, series_b: LapSeries, num_points: int = 200
) -> list[tuple[float, float]]:
    """Delta (tempo de B menos tempo de A) ao longo da distância, amostrado em
    pontos igualmente espaçados no trecho em que as duas voltas se sobrepõem.

    Positivo = B mais devagar que A naquele ponto; negativo = B mais rápido.
    """
    if series_a.is_empty or series_b.is_empty:
        return []

    max_distance = min(series_a.max_distance, series_b.max_distance)
    if max_distance <= 0:
        return []

    step = max_distance / num_points
    result: list[tuple[float, float]] = []
    for i in range(num_points + 1):
        d = i * step
        ta = series_a.elapsed_ms_at(d)
        tb = series_b.elapsed_ms_at(d)
        if ta is None or tb is None:
            continue
        result.append((d, (tb - ta) / 1000))
    return result

