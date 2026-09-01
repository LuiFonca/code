"""
Onde termina o setor 1.

O GT7 não transmite os pontos oficiais de setor, então o programa divide a volta
em três trechos de distância igual. A pergunta que sobra — **igual a quê?** —
tinha três respostas diferentes rodando ao mesmo tempo:

- o que era gravado no banco ancorava na mediana das 10 últimas voltas;
- a tabela do Histórico ancorava na melhor volta da pista;
- as marcas no mapa da Análise e da Comparação ancoravam na própria volta.

Numa base de cinco voltas de Interlagos as três divisas caíam a 5 m uma da
outra, e o setor 1 da mesma volta valia 31.786 ms no banco e 31.622 ms na tela.
Pior que a diferença era a instabilidade: com a âncora na melhor volta, **bater
um recorde reescrevia o passado** — medi 52 ms de mudança em voltas antigas que
o piloto não tocou. Um setor que muda sozinho não serve para treinar.

A âncora
--------
O comprimento oficial da pista, do catálogo do jogo (`track_list.csv`, 105 de
105 pistas). Suzuka tem 5.807 m hoje e vai ter 5.807 m daqui a um ano, então a
divisa cai sempre no mesmo ponto **físico** do asfalto. É o que torna o setor 2
de hoje comparável com o de três meses atrás — e é a única das quatro âncoras
que não depende do que o piloto fez.

Por que a última divisa é exceção
---------------------------------
A linha de chegada é um fato, não uma estimativa: ela fica onde a volta terminou.
O comprimento medido pelo hodômetro difere do comprimento oficial porque a linha
percorrida não é o eixo da pista — numa volta 17 m mais longa que o catálogo,
ancorar também a última divisa deixava os últimos 17 m sem dono e os setores
somavam **369 ms a menos** que o tempo da volta.

Então: divisas interiores no catálogo, última divisa no fim da própria volta. Os
setores voltam a somar o tempo da volta, e os pontos de corte continuam fixos.

Quando não há catálogo
----------------------
Pista fora do catálogo, ou renomeada para algo que não casa com nenhuma: cai
para a mediana das últimas voltas e, na falta dela, para a própria volta. Nos
dois casos a âncora se move, e é por isso que ela é **gravada junto** com os
setores — quem lê um valor guardado precisa saber se ele ainda vale.
"""

from __future__ import annotations

NUM_SECTORS = 3

#: Abaixo disto a volta não rendeu setor nenhum que signifique algo — é uma saída
#: de boxes, um abandono ou lixo de captura.
MIN_LAP_DISTANCE_M = 50.0


def sector_boundaries(
    lap_distance_m: float,
    anchor_m: float | None = None,
    num_sectors: int = NUM_SECTORS,
) -> list[float]:
    """Distâncias (m) onde cada setor termina, para uma volta desta pista.

    `anchor_m` é o comprimento canônico da pista — o do catálogo, quando
    conhecido. As divisas interiores saem dele; a última é sempre o fim da volta
    informada, para que os setores somem o tempo da volta.

    Sem âncora, tudo sai da própria volta — o comportamento antigo, mantido como
    último recurso para pista desconhecida.
    """
    if lap_distance_m <= 0 or num_sectors < 1:
        return []

    base = anchor_m if anchor_m and anchor_m > 0 else lap_distance_m

    # Âncora absurdamente distante da volta significa pista trocada ou catálogo
    # errado; a própria volta é um palpite pior mas honesto.
    if not 0.5 <= lap_distance_m / base <= 2.0:
        base = lap_distance_m

    interiores = [base * (i / num_sectors) for i in range(1, num_sectors)]

    # Uma divisa interior que caiu além do fim da volta não divide nada. Isso
    # acontece numa volta abortada; encostá-la no fim mantém a contagem de
    # setores e deixa os últimos vazios, que é o que a tela sabe mostrar.
    interiores = [min(d, lap_distance_m) for d in interiores]

    return [*interiores, lap_distance_m]


def resolve_anchor(
    track_length_m: float | None,
    fallback_m: float | None = None,
) -> float | None:
    """A âncora que vale para esta pista, na ordem de preferência.

    Catálogo primeiro, porque é fixo. Depois a mediana das voltas recentes, que
    é estável no curto prazo. `None` quando não há nenhuma das duas — aí
    `sector_boundaries` usa a própria volta.
    """
    if track_length_m and track_length_m > 0:
        return float(track_length_m)
    if fallback_m and fallback_m > 0:
        return float(fallback_m)
    return None
