"""
Wrapper em torno dos frames de uma volta que permite consultar o valor de
qualquer canal de telemetria (velocidade, marcha, freio, etc.) numa
distância arbitrária, interpolando entre os pontos salvos.

Usado pela aba de Comparação para sincronizar múltiplos gráficos pelo mesmo
eixo de distância: ao passar o mouse num ponto de dado gráfico, os outros
gráficos e o painel de valores usam esta classe para descobrir o valor de
cada canal naquele exato ponto da pista, mesmo que os frames originais das
duas voltas não tenham sido amostrados nas mesmas distâncias.

Voltas salvas por versões antigas do schema (antes de fuel/pneus/posição
existirem) podem ter NULL nessas colunas — este módulo trata None como
"amostra ausente" em vez de deixar propagar para os gráficos (que quebrariam
ao tentar desenhar ou fazer aritmética com None), igual a qualquer outro
buraco de amostragem entre canais com frequências diferentes.
"""

import bisect

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
    "position_x": 12,
    "position_z": 13,
    "g_lateral": 14,
    "g_longitudinal": 15,
    "suspension_fl": 16,
    "suspension_fr": 17,
    "suspension_rl": 18,
    "suspension_rr": 19,
    "tire_slip_fl": 20,
    "tire_slip_fr": 21,
    "tire_slip_rl": 22,
    "tire_slip_rr": 23,
    "turbo_boost": 24,
    "oil_temp": 25,
    "water_temp": 26,
}


class LapSeries:
    def __init__(self, frames: list):
        """frames: lista de tuplas de lap_storage.get_lap_frames()."""
        self._frames = frames
        self._distances = [row[1] for row in frames]
        self._channel_cache = {}

    @property
    def is_empty(self) -> bool:
        return len(self._frames) < 2

    @property
    def max_distance(self) -> float:
        return self._distances[-1] if self._distances else 0.0

    def has_channel(self, channel: str) -> bool:
        """True se o canal tiver pelo menos um valor não-nulo nesta volta
        (voltas antigas migradas de schemas anteriores podem não ter
        combustível/pneus/posição gravados)."""
        return len(self._valid_points(channel)) >= 2

    def _valid_points(self, channel: str):
        """Pares (distance_m, valor) do canal, na ordem original, pulando
        amostras None. Resultado cacheado por canal (os frames não mudam
        depois de carregados)."""
        cached = self._channel_cache.get(channel)
        if cached is not None:
            return cached
        idx = CHANNELS[channel]
        pairs = [(row[1], row[idx]) for row in self._frames if row[idx] is not None]
        self._channel_cache[channel] = pairs
        return pairs

    def points(self, channel: str):
        """Retorna a lista de pares (distance_m, valor) prontos para um
        gráfico — sem interpolação, um ponto por frame salvo. Amostras
        None (canal ausente naquele frame, ex: volta antiga sem essa
        coluna) são simplesmente omitidas em vez de quebrar o gráfico."""
        return self._valid_points(channel)

    def points_by_time(self, channel: str):
        """Retorna pares (elapsed_seconds, valor) para plotar no eixo temporal."""
        idx_ch = CHANNELS[channel]
        idx_t = CHANNELS["elapsed_ms"]
        return [
            (row[idx_t] / 1000.0, row[idx_ch])
            for row in self._frames
            if row[idx_ch] is not None and row[idx_t] is not None
        ]

    @property
    def max_time(self) -> float:
        idx_t = CHANNELS["elapsed_ms"]
        if not self._frames:
            return 0.0
        last = self._frames[-1][idx_t]
        return (last / 1000.0) if last is not None else 0.0

    def value_at(self, distance_m: float, channel: str):
        """Valor interpolado do canal na distância informada. Retorna None
        se a distância estiver fora do intervalo coberto pela volta ou se o
        canal não tiver dados suficientes (ex: volta antiga sem essa coluna)."""
        distances, values = self._channel_arrays(channel)
        if len(distances) < 1:
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

    def _channel_arrays(self, channel: str):
        pairs = self._valid_points(channel)
        if not pairs:
            return [], []
        distances, values = zip(*pairs)
        return list(distances), list(values)

    def elapsed_ms_at(self, distance_m: float):
        return self.value_at(distance_m, "elapsed_ms")

    def position_points(self):
        """Pares (x, z) de posição de mundo, na ordem original — usados
        para desenhar o traçado da volta (item 13). position_x/position_z
        são sempre gravados juntos (mesmo frame), então uma amostra só
        aparece aqui quando as duas existem; voltas antigas de antes da
        v4 do schema (sem essas colunas) simplesmente não têm traçado."""
        idx_x, idx_z = CHANNELS["position_x"], CHANNELS["position_z"]
        return [
            (row[idx_x], row[idx_z])
            for row in self._frames
            if row[idx_x] is not None and row[idx_z] is not None
        ]


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


def sector_boundaries_m(reference_distance_m: float, num_sectors: int = 3):
    """Distâncias (m) onde cada setor termina, para um total de referência.
    Mesma lógica de divisão usada em lap_storage._compute_sector_times,
    reaproveitada aqui para desenhar linhas de setor e comparar setor a
    setor na tela sem duplicar a fórmula."""
    if reference_distance_m <= 0:
        return []
    return [reference_distance_m * (i / num_sectors) for i in range(1, num_sectors + 1)]


def sector_times_from_series(series: LapSeries, boundaries_m: list):
    """Tempo (ms) gasto em cada setor de uma volta, usando os MESMOS
    limites de distância (boundaries_m) para todas as voltas comparadas —
    ao contrário de lap_storage.get_sector_times (que usa os limites já
    fixados no momento em que aquela volta específica foi salva), isto
    permite comparar 'Setor 2 da volta A' com 'Setor 2 da volta B' usando
    exatamente o mesmo corte de distância, mesmo que uma delas tenha sido
    salva antes deste corte estar em uso.
    Retorna None num setor se a volta não cobre aquele trecho de distância."""
    if series.is_empty or not boundaries_m:
        return [None] * len(boundaries_m)

    times = []
    previous_ms = series.elapsed_ms_at(0.0)
    for boundary in boundaries_m:
        if boundary > series.max_distance * 1.05:
            times.append(None)
            continue
        d = min(boundary, series.max_distance)
        current_ms = series.elapsed_ms_at(d)
        if previous_ms is None or current_ms is None:
            times.append(None)
        else:
            sector_ms = current_ms - previous_ms
            times.append(sector_ms if sector_ms > 0 else None)
        previous_ms = current_ms
    return times


def best_combined_sectors(sector_times_a: list, sector_times_b: list):
    """Soma do melhor tempo de cada setor entre as duas voltas — a 'volta
    teórica ideal' combinando o melhor trecho de cada uma. Retorna
    (melhor_soma_ms, escolhas) onde escolhas é uma lista de 'A'/'B'/None
    por setor (None quando nenhuma das duas tem aquele setor)."""
    choices = []
    total_ms = 0
    any_valid = False
    for ta, tb in zip(sector_times_a, sector_times_b):
        if ta is None and tb is None:
            choices.append(None)
            continue
        if tb is None or (ta is not None and ta <= tb):
            choices.append("A")
            total_ms += ta
        else:
            choices.append("B")
            total_ms += tb
        any_valid = True
    return (total_ms if any_valid else None), choices
