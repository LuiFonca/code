"""
Casamento de eventos entre duas voltas, por distância.

Todo módulo de analytics precisa da mesma operação: "esta curva/frenagem/
aceleração da volta A corresponde a qual da volta B?". Casar por índice quebra
assim que uma volta tem um evento a mais ou a menos — e é justamente a volta
atípica que mais interessa analisar. Por isso o casamento é sempre por
**distância percorrida**, o mesmo alinhamento que o delta usa.

Por que não o vizinho mais próximo, simplesmente
------------------------------------------------
A versão ingênua — "para cada evento da referência, pegue o mais próximo na
outra volta" — tem um defeito real: dois eventos da referência podem reclamar o
**mesmo** evento da outra volta. Numa chicane, onde duas frenagens ficam a 60 m
uma da outra e a tolerância é 150 m, isso acontece de verdade, e o resultado
seria relatar "freou 8 m mais cedo" duas vezes para a mesma freada — enquanto o
evento que de fato sumiu passaria despercebido.

A solução é atribuição gulosa por proximidade global: os pares mais próximos
escolhem primeiro, e cada evento só é consumido uma vez. Não é o ótimo global
(seria o algoritmo húngaro), mas com poucos eventos por volta e um limiar de
tolerância, a diferença é nula e o custo é uma ordenação.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

R = TypeVar("R")
C = TypeVar("C")


def match_by_distance(
    reference: list[R],
    candidates: list[C],
    *,
    reference_key: Callable[[R], float],
    candidate_key: Callable[[C], float],
    tolerance_m: float,
) -> list[tuple[R, C | None]]:
    """Casa cada item da referência com no máximo um candidato.

    Devolve uma tupla por item da referência, na ordem original, com `None`
    quando nada ficou dentro da tolerância. Nenhum candidato aparece em duas
    tuplas.
    """
    scored: list[tuple[float, int, int]] = []
    for reference_index, reference_item in enumerate(reference):
        anchor = reference_key(reference_item)
        for candidate_index, candidate in enumerate(candidates):
            gap = abs(anchor - candidate_key(candidate))
            if gap <= tolerance_m:
                scored.append((gap, reference_index, candidate_index))

    # Ordena por distância; os índices no final da tupla desempatam de forma
    # determinística (importa para que o resultado não dependa da ordem de
    # iteração quando dois pares empatam exatamente).
    scored.sort()

    assigned: dict[int, int] = {}
    used_candidates: set[int] = set()
    for _, reference_index, candidate_index in scored:
        if reference_index in assigned or candidate_index in used_candidates:
            continue
        assigned[reference_index] = candidate_index
        used_candidates.add(candidate_index)

    return [
        (
            item,
            candidates[assigned[index]] if index in assigned else None,
        )
        for index, item in enumerate(reference)
    ]
