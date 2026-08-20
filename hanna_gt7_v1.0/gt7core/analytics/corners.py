"""
Detecção automática de curvas — §12 do briefing.

É a peça que ancora todo o resto do analytics. Sem curvas identificadas,
"frenagem" e "throttle" são séries soltas; com elas, viram "você freou 8 m mais
cedo na curva 4" — que é a frase que o briefing pede no §20.

Como a detecção funciona
------------------------
Um piloto reconhece uma curva pelo que ela obriga a fazer: desacelerar, atingir
uma velocidade mínima, e voltar a acelerar. O detector usa exatamente isso — os
**mínimos locais do perfil de velocidade** — em vez de tentar inferir geometria
a partir das coordenadas.

A alternativa (calcular curvatura de x/z) é atraente mas frágil na prática: o
traçado tem ruído de amostragem, uma reta com correção de direção produz
curvatura falsa, e um chicane rápido pode ter raio pequeno sem ser um ponto de
frenagem. A velocidade é o sinal que o piloto de fato usa, e é robusto.

A curvatura entra depois, como **atributo** da curva já detectada: serve para
estimar o raio e distinguir uma curva lenta de uma chicane rápida.

Limitação conhecida: curvas de raio constante tomadas a fundo (uma parabólica
onde não se levanta o pé) não produzem mínimo local e não são detectadas. Elas
também não são onde se ganha ou perde tempo, então a omissão é aceitável para o
propósito — mas é uma omissão, não um acaso.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..domain.models import TelemetryPoint
from .matching import match_by_distance

# Uma queda de velocidade só conta como curva se for relevante. 8% filtra o
# ruído de amostragem e as correções de direção em reta sem perder curvas
# rápidas de verdade.
MIN_SPEED_DROP_RATIO = 0.08

# Duas curvas mais próximas que isto são a mesma (entrada e saída de uma
# chicane, tipicamente). Em metros.
MIN_CORNER_SEPARATION_M = 60.0

# Janela de suavização do perfil de velocidade, em amostras. A 60 Hz, 15
# amostras são 0,25 s — o suficiente para matar o ruído sem apagar um ápice.
SMOOTHING_WINDOW = 15


@dataclass(frozen=True, slots=True)
class Corner:
    """Uma curva identificada numa volta.

    As distâncias são acumuladas na volta, o que permite comparar a mesma curva
    entre voltas diferentes — é o alinhamento por distância que o resto do
    sistema já usa.
    """

    index: int
    """Número da curva na volta, começando em 1."""

    entry_distance_m: float
    """Onde a desaceleração começa."""

    apex_distance_m: float
    """Ponto de velocidade mínima."""

    exit_distance_m: float
    """Onde a velocidade volta ao patamar de reta."""

    entry_speed_kmh: float
    minimum_speed_kmh: float
    exit_speed_kmh: float

    entry_time_ms: int
    apex_time_ms: int
    exit_time_ms: int

    radius_m: float | None
    """Raio estimado no ápice, pela curvatura do traçado. None sem posição."""

    @property
    def length_m(self) -> float:
        return self.exit_distance_m - self.entry_distance_m

    @property
    def duration_ms(self) -> int:
        return self.exit_time_ms - self.entry_time_ms

    @property
    def speed_drop_kmh(self) -> float:
        return self.entry_speed_kmh - self.minimum_speed_kmh

    @property
    def severity(self) -> str:
        """Classificação grosseira, para rótulo de interface.

        Os limiares são de carro de rua em circuito; não pretendem ser uma
        taxonomia de engenharia, só um rótulo legível.
        """
        if self.minimum_speed_kmh < 80:
            return "lenta"
        if self.minimum_speed_kmh < 140:
            return "média"
        return "rápida"

    def contains(self, distance_m: float) -> bool:
        return self.entry_distance_m <= distance_m <= self.exit_distance_m


def _smooth(values: list[float], window: int) -> list[float]:
    """Média móvel centrada. Preserva o comprimento da série.

    Sem suavização, o ruído de amostragem cria dezenas de mínimos locais falsos
    e o detector encontraria "curvas" no meio da reta.
    """
    if window <= 1 or len(values) < window:
        return list(values)

    half = window // 2
    smoothed: list[float] = []
    for i in range(len(values)):
        start = max(0, i - half)
        end = min(len(values), i + half + 1)
        smoothed.append(sum(values[start:end]) / (end - start))
    return smoothed


def _curvature_radius(
    points: list[TelemetryPoint], index: int, span: int = 10
) -> float | None:
    """Raio do círculo que passa por três pontos do traçado.

    Usa pontos espaçados por `span` em vez de vizinhos imediatos: a 60 Hz,
    amostras consecutivas ficam a centímetros e o ruído domina o cálculo.
    """
    if index - span < 0 or index + span >= len(points):
        return None

    a, b, c = points[index - span], points[index], points[index + span]
    ax, az = a.position_x, a.position_z
    bx, bz = b.position_x, b.position_z
    cx, cz = c.position_x, c.position_z

    # Área do triângulo pelo produto vetorial. Área ~zero = três pontos
    # colineares = reta, raio infinito.
    area = abs((bx - ax) * (cz - az) - (cx - ax) * (bz - az)) / 2.0
    if area < 1e-6:
        return None

    side_ab = math.dist((ax, az), (bx, bz))
    side_bc = math.dist((bx, bz), (cx, cz))
    side_ca = math.dist((cx, cz), (ax, az))

    radius = (side_ab * side_bc * side_ca) / (4.0 * area)
    # Raios absurdos são artefato numérico de trecho quase reto.
    return radius if radius < 5000 else None


def detect_corners(
    points: list[TelemetryPoint],
    *,
    min_speed_drop_ratio: float = MIN_SPEED_DROP_RATIO,
    min_separation_m: float = MIN_CORNER_SEPARATION_M,
) -> list[Corner]:
    """Identifica as curvas de uma volta pelo perfil de velocidade.

    Devolve as curvas em ordem de distância. Volta sem amostras suficientes ou
    sem variação de velocidade devolve lista vazia — não é erro, é uma volta
    onde não há curva a encontrar (um teste de aceleração em reta, por exemplo).
    """
    if len(points) < SMOOTHING_WINDOW * 3:
        return []

    speeds = _smooth([p.speed_kmh for p in points], SMOOTHING_WINDOW)
    max_speed = max(speeds)
    if max_speed <= 0:
        return []

    # Um mínimo local só é candidato se a queda em relação ao pico da volta for
    # relevante. Compara-se com o máximo da volta, não com o vizinho imediato:
    # o vizinho de um mínimo é quase igual a ele por construção.
    threshold = max_speed * (1.0 - min_speed_drop_ratio)

    apex_indices: list[int] = []
    for i in range(1, len(speeds) - 1):
        if speeds[i] > threshold:
            continue
        if speeds[i] <= speeds[i - 1] and speeds[i] < speeds[i + 1]:
            apex_indices.append(i)

    if not apex_indices:
        return []

    # Colapsa mínimos vizinhos: um platô de velocidade mínima produz vários
    # índices seguidos, e todos são o mesmo ápice.
    merged: list[int] = [apex_indices[0]]
    for index in apex_indices[1:]:
        if points[index].distance_m - points[merged[-1]].distance_m < min_separation_m:
            # Fica o mais lento dos dois — é o ápice de verdade.
            if speeds[index] < speeds[merged[-1]]:
                merged[-1] = index
        else:
            merged.append(index)

    corners: list[Corner] = []
    for number, apex_index in enumerate(merged, start=1):
        entry_index = _find_entry(speeds, apex_index)
        exit_index = _find_exit(speeds, apex_index)

        corners.append(
            Corner(
                index=number,
                entry_distance_m=points[entry_index].distance_m,
                apex_distance_m=points[apex_index].distance_m,
                exit_distance_m=points[exit_index].distance_m,
                entry_speed_kmh=points[entry_index].speed_kmh,
                minimum_speed_kmh=points[apex_index].speed_kmh,
                exit_speed_kmh=points[exit_index].speed_kmh,
                entry_time_ms=points[entry_index].elapsed_ms,
                apex_time_ms=points[apex_index].elapsed_ms,
                exit_time_ms=points[exit_index].elapsed_ms,
                radius_m=_curvature_radius(points, apex_index),
            )
        )

    return corners


def _find_entry(speeds: list[float], apex_index: int) -> int:
    """Anda para trás até a velocidade parar de subir — o topo da reta."""
    index = apex_index
    while index > 0 and speeds[index - 1] >= speeds[index]:
        index -= 1
    return index


def _find_exit(speeds: list[float], apex_index: int) -> int:
    """Anda para frente até a velocidade parar de subir."""
    index = apex_index
    last = len(speeds) - 1
    while index < last and speeds[index + 1] >= speeds[index]:
        index += 1
    return index


def corner_at(corners: list[Corner], distance_m: float) -> Corner | None:
    """A curva que contém a distância informada, se houver."""
    for corner in corners:
        if corner.contains(distance_m):
            return corner
    return None


def match_corners(
    reference: list[Corner], other: list[Corner], *, tolerance_m: float = 120.0
) -> list[tuple[Corner, Corner | None]]:
    """Casa as curvas de duas voltas pela posição do ápice.

    Não basta comparar por índice: uma volta onde o detector perdeu uma curva
    (ou achou uma a mais, por um levantar de pé fora de hora) desalinharia todo
    o resto da comparação a partir dali — exatamente o problema que o
    alinhamento por distância resolve no delta.
    """
    return match_by_distance(
        reference,
        other,
        reference_key=lambda c: c.apex_distance_m,
        candidate_key=lambda c: c.apex_distance_m,
        tolerance_m=tolerance_m,
    )
