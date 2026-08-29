"""
Relevo da pista: subida, descida e sobrelevação.

O GT7 manda duas coisas que o programa passou muito tempo descartando: a
**altitude** (`position_y` — no GT7 a vertical é o Y, e X/Z são o plano) e a
**normal do asfalto** sob o carro. As duas juntas respondem a pergunta que
separa "freou mal" de "freou numa descida".

Por que a normal, e não a derivada da altitude: derivar altitude por distância
amplifica ruído — a 60 Hz, um carro a 200 km/h anda 90 cm por amostra, e um
centímetro de erro na altitude vira 1% de rampa fantasma. A normal é medida
diretamente pelo jogo e não precisa de derivada nenhuma.

Aqui mora a **interpretação**; o dado cru fica no `TelemetryPoint`. A separação
não é cerimônia: a conversão de normal em "rampa de 4%" depende da direção em
que o carro anda, e é justamente o tipo de conta que se descobre errada depois.
Errada aqui, corrige-se relendo a volta; errada na gravação, perde-se a volta.
"""

from __future__ import annotations

import math

from ..domain.models import TelemetryPoint

#: Deslocamento mínimo entre duas amostras, em metros, para elas definirem uma
#: direção de marcha. Abaixo disto o carro está praticamente parado e o vetor
#: entre os pontos é ruído de posição, não rumo.
MIN_HEADING_STEP_M = 0.05

#: Rampa acima disto, em %, é descartada como implausível. 50% são 26 graus —
#: mais íngreme que qualquer asfalto do jogo, inclusive as subidas de Bathurst
#: e a rampa de Eau Rouge.
MAX_PLAUSIBLE_SLOPE_PCT = 50.0


def _heading(points: list[TelemetryPoint], index: int) -> tuple[float, float] | None:
    """Direção de marcha no plano, pelo deslocamento em torno da amostra.

    Usa o vizinho de trás e o da frente em vez do par imediato: centrada, a
    diferença cancela o erro de posição de primeira ordem, e é a mesma técnica
    que `steering.yaw_rate_series` já usa para a guinada.
    """
    before = points[index - 1] if index > 0 else points[index]
    after = points[index + 1] if index + 1 < len(points) else points[index]

    dx = after.position_x - before.position_x
    dz = after.position_z - before.position_z
    passo = math.hypot(dx, dz)
    if passo < MIN_HEADING_STEP_M:
        return None
    return dx / passo, dz / passo


def slope_series(points: list[TelemetryPoint]) -> list[float | None]:
    """Inclinação da pista na direção da marcha, em %, com sinal.

    Positivo é subida. `None` onde não foi medido — volta gravada antes da
    normal existir, ou amostra em que o carro está parado demais para ter
    rumo. `None` e não zero, pelo motivo de sempre: zero é a afirmação "aqui é
    plano", e ninguém mediu isso.

    A conta: a normal `n` é perpendicular ao asfalto, então o gradiente da
    superfície é `−(nx, nz) / ny`, e a rampa que o carro enfrenta é a projeção
    desse gradiente no rumo.
    """
    saida: list[float | None] = []
    for i, ponto in enumerate(points):
        if not ponto.has_road_normal:
            saida.append(None)
            continue
        rumo = _heading(points, i)
        if rumo is None:
            saida.append(None)
            continue
        fx, fz = rumo
        gradiente = ponto.road_plane_x * fx + ponto.road_plane_z * fz
        rampa = -100.0 * gradiente / ponto.road_plane_y
        if abs(rampa) > MAX_PLAUSIBLE_SLOPE_PCT:
            saida.append(None)
            continue
        saida.append(rampa)
    return saida


def bank_series(points: list[TelemetryPoint]) -> list[float | None]:
    """Sobrelevação da pista, em %, com sinal.

    Positivo quando o asfalto cai para a esquerda do carro — a inclinação que
    ajuda numa curva à esquerda. É o mesmo cálculo da rampa, projetado na
    perpendicular do rumo em vez de nele.
    """
    saida: list[float | None] = []
    for i, ponto in enumerate(points):
        if not ponto.has_road_normal:
            saida.append(None)
            continue
        rumo = _heading(points, i)
        if rumo is None:
            saida.append(None)
            continue
        fx, fz = rumo
        # Perpendicular no plano, mesma convenção do motor: (−fz, fx).
        rx, rz = -fz, fx
        gradiente = ponto.road_plane_x * rx + ponto.road_plane_z * rz
        inclinacao = -100.0 * gradiente / ponto.road_plane_y
        if abs(inclinacao) > MAX_PLAUSIBLE_SLOPE_PCT:
            saida.append(None)
            continue
        saida.append(inclinacao)
    return saida


def elevation_series(points: list[TelemetryPoint]) -> list[float | None]:
    """Altitude relativa ao ponto mais baixo da volta, em metros.

    Relativa, e não absoluta: a origem do mundo do GT7 é arbitrária e muda de
    pista para pista, então "412 m" não diz nada. O que se lê num perfil de
    elevação é a diferença — quanto se sobe entre a largada e o alto do
    circuito —, e essa é a mesma com qualquer origem.
    """
    alturas = [p.position_y for p in points]
    medidas = [h for h in alturas if h is not None]
    if not medidas:
        return [None] * len(points)
    base = min(medidas)
    return [None if h is None else h - base for h in alturas]


def elevation_range_m(points: list[TelemetryPoint]) -> float | None:
    """Desnível total da volta, em metros. `None` se nada foi medido."""
    medidas = [p.position_y for p in points if p.position_y is not None]
    if len(medidas) < 2:
        return None
    return max(medidas) - min(medidas)
