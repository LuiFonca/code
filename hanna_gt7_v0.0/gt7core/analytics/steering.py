"""
Guinada — o quanto o carro gira, derivado do traçado.

Por que não é "ângulo de volante"
---------------------------------
**O pacote de 296 bytes do GT7 não transmite o esterço.** Os 296 bytes estão
inteiramente mapeados em `protocol.py` — posição, velocidade, pedais, marcha,
pneus, suspensão, motor — e não há um campo de volante entre eles. Inventar um
offset para preencher a lacuna é exatamente o que produziu o defeito de 0x70 /
0x78, que gravou distância 0,0 m em toda volta capturada de PS5 real.

O que dá para saber com certeza é **quanto o carro girou**, porque isso está no
traçado: a direção em que o carro aponta é a direção em que ele anda, e a
derivada dela no tempo é a taxa de guinada. Não é o ângulo do volante — é o
resultado dele, que é a pergunta que o piloto realmente faz ao olhar um canal de
esterço: *onde eu girei o carro, quanto, e com que suavidade?*

A diferença aparece em dois lugares, e nos dois a guinada é a informação melhor:
com subesterço o volante gira e o carro não, e o canal de volante mostraria uma
entrada que não virou nada; numa correção de traseira o volante contra-esterça
enquanto o carro segue girando.

Convenção de sinal
------------------
Positivo é **curva à direita**. Isso não é escolha arbitrária: derivando
`θ = atan2(vz, vx)` chega-se a `dθ/dt = (vx·az − vz·ax)/|v|²`, que é o mesmo
numerador de `g_lateral` no motor, dividido por outra coisa positiva. Os dois
canais compartilham a orientação **por construção** — se um estiver espelhado o
outro está também, e há um lugar só para corrigir (o vetor `right` em
`engine._compute_g_forces`), em vez de dois que podem discordar em silêncio.
"""

from __future__ import annotations

import math

from ..domain.models import TelemetryPoint

#: Amostras de cada lado usadas para medir a direção e para derivá-la.
#:
#: Um por um, a conta é a diferença de duas posições consecutivas — a 60 Hz e
#: 200 km/h isso é menos de um metro, e o arredondamento do próprio pacote vira
#: uma serra de ±40°/s que enterra a curva de verdade. Sobre uma janela a
#: direção vem de um segmento com comprimento suficiente para o ruído virar
#: fração, e o traço passa a mostrar a curva em vez do erro de medida.
WINDOW = 3

#: Abaixo disto a direção não existe: parado, `atan2` de dois zeros é ruído puro
#: e produziria picos de guinada com o carro imóvel.
MIN_SPEED_KMH = 12.0

#: Deslocamento mínimo, em metros, entre as pontas da janela. Protege o mesmo
#: caso pelo outro lado — carro andando devagar em cima do próprio rastro.
MIN_SPAN_M = 0.5


def _wrap(angle: float) -> float:
    """Traz a diferença de ângulos para (−π, π].

    Sem isto, cruzar a descontinuidade de `atan2` (±π) num único quadro produz
    um salto de 2π, que vira um pico de ~20.000°/s numa reta — e o gráfico
    inteiro se reescala para caber nesse pico, achatando a volta real.
    """
    return math.remainder(angle, math.tau)


def yaw_rate_series(
    points: list[TelemetryPoint], *, window: int = WINDOW
) -> list[tuple[float, float]]:
    """Taxa de guinada ao longo da volta: pares (distância em m, °/s).

    Positivo é curva à direita. Amostras sem direção definida — carro parado, ou
    janela curta demais — são **omitidas** em vez de virarem zero: um zero ali
    afirmaria "seguiu reto", que é uma informação que não foi medida.
    """
    if window < 1 or len(points) < 2 * window + 1:
        return []

    headings = _headings(points, window)

    series: list[tuple[float, float]] = []
    for index in range(window, len(points) - window):
        before, after = headings[index - window], headings[index + window]
        if before is None or after is None:
            continue

        dt_s = (points[index + window].elapsed_ms - points[index - window].elapsed_ms) / 1000.0
        if dt_s <= 0:
            continue

        degrees_per_s = math.degrees(_wrap(after - before)) / dt_s
        series.append((points[index].distance_m, degrees_per_s))

    return series


def _headings(
    points: list[TelemetryPoint], window: int
) -> list[float | None]:
    """Direção do carro em cada amostra, medida sobre a janela.

    `None` onde a direção não é mensurável, para que quem deriva saiba a
    diferença entre "andou reto" e "não deu para saber".
    """
    headings: list[float | None] = [None] * len(points)

    for index in range(window, len(points) - window):
        start, end = points[index - window], points[index + window]
        dx = end.position_x - start.position_x
        dz = end.position_z - start.position_z

        if points[index].speed_kmh < MIN_SPEED_KMH:
            continue
        if math.hypot(dx, dz) < MIN_SPAN_M:
            continue

        headings[index] = math.atan2(dz, dx)

    return headings


def peak_yaw_rate(series: list[tuple[float, float]]) -> float:
    """Maior guinada em módulo, em °/s. Zero para série vazia."""
    return max((abs(value) for _, value in series), default=0.0)
