"""
Achar a amostra que corresponde a uma distância da volta.

É a pergunta mais repetida do projeto — "o que estava acontecendo no metro
1.240?" — e estava respondida em três lugares, com três implementações:

- `pages/analysis.py`, busca binária;
- `pages/compare.py`, varredura linear;
- `analytics/throttle.py`, varredura linear devolvendo índice.

As duas primeiras eram a **mesma função copiada**, e divergiram: quando a da
Análise virou binária para o cursor deixar de arrastar, a da Comparação ficou
como estava. O resultado é que o cursor de uma tela custa 0,03 ms e o da tela
vizinha custa ~5 ms, sem nada na interface dizendo por quê.

Por que binária pode ser a única versão
---------------------------------------
A varredura linear tinha uma justificativa escrita em `throttle.py`: ali a busca
roda uma vez por curva na análise pós-volta, não a 60 Hz, e manter uma lista
paralela de distâncias para uma busca binária não pagaria. A justificativa era
boa e deixou de valer: **a distância acumulada já é monotônica** — o hodômetro
só anda para a frente —, então a busca binária não precisa de estrutura
nenhuma ao lado. Sem custo de manutenção, não há motivo para duas.

O contrato é "a mais próxima", e não "a primeira maior ou igual": o alvo quase
sempre cai entre duas amostras, e a de trás pode estar mais perto.
"""

from __future__ import annotations

from ..domain.models import TelemetryPoint


def index_at_distance(
    points: list[TelemetryPoint], distance_m: float
) -> int | None:
    """Índice da amostra mais próxima de `distance_m`. `None` sem amostras.

    Fora da faixa coberta pela volta devolve a ponta correspondente — quem
    pergunta por 50 km numa volta de 4 km está com o cursor além do fim, e a
    última amostra é a resposta honesta.
    """
    if not points:
        return None

    baixo, alto = 0, len(points) - 1
    while baixo < alto:
        meio = (baixo + alto) // 2
        if points[meio].distance_m < distance_m:
            baixo = meio + 1
        else:
            alto = meio

    # A busca para na primeira amostra **maior ou igual**; a anterior pode
    # estar mais perto do alvo.
    if baixo > 0:
        anterior = abs(points[baixo - 1].distance_m - distance_m)
        if anterior <= abs(points[baixo].distance_m - distance_m):
            return baixo - 1
    return baixo


def point_at_distance(
    points: list[TelemetryPoint], distance_m: float
) -> TelemetryPoint | None:
    """A amostra mais próxima de `distance_m`. `None` sem amostras."""
    indice = index_at_distance(points, distance_m)
    return None if indice is None else points[indice]
