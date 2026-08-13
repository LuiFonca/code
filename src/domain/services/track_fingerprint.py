"""
Reconhecimento de pista pelo traçado.

Antes, uma volta rodada sem pista definida era descartada. Agora ela é salva de
qualquer jeito e o app tenta descobrir de que pista se trata comparando o
desenho do traçado com o das pistas que já estão no banco.

Por que isto é mais simples do que parece
------------------------------------------
Reconhecer forma costuma exigir invariância a rotação e translação. Aqui não:
o GT7 transmite posição em **coordenadas absolutas do circuito**, e elas são as
mesmas em toda volta na mesma pista. Duas voltas em Interlagos caem praticamente
uma sobre a outra; uma volta em Interlagos e outra em Suzuka nem se aproximam.

O que sobra é normalizar o que de fato varia entre voltas: o número de amostras
e a velocidade. Reamostrar por **fração da distância percorrida** resolve os
dois — o ponto 30% do traçado é o ponto 30% em qualquer volta, rápida ou lenta.

Limites conhecidos
------------------
- Layouts diferentes do mesmo circuito (traçado curto vs completo) compartilham
  boa parte do desenho; por isso a decisão exige margem sobre o segundo lugar,
  e não só estar abaixo do limiar.
- Uma volta parcial não é usada: metade do traçado casaria com meia dúzia de
  pistas.
"""

import math

# Quantos pontos a assinatura guarda. 64 descreve bem o formato de um circuito
# (as curvas de um autódromo são muito maiores que 1/64 da volta) e mantém a
# comparação barata: identificar uma volta contra 20 pistas são 1.280 distâncias.
FINGERPRINT_POINTS = 64

# Distância média máxima, em metros, entre traçados considerados a mesma pista.
# A pista tem ~15 m de largura e linhas de pilotagem diferentes se afastam
# alguns metros; 40 m cobre isso com folga e continua muito abaixo da separação
# entre circuitos distintos, que é de centenas a milhares de metros.
MAX_DESVIO_MEDIO_M = 40.0

# O segundo colocado precisa estar pelo menos este tanto pior. Sem a margem,
# dois layouts do mesmo circuito (ou duas pistas parecidas) seriam decididos no
# ruído — e errar a pista é pior que não saber, porque contamina o histórico.
MARGEM_MINIMA = 1.8

# Abaixo disto a volta é curta demais para descrever uma pista.
MIN_DISTANCIA_M = 500.0


def build_fingerprint(points, n: int = FINGERPRINT_POINTS):
    """Assinatura do traçado: `n` pontos (x, z) igualmente espaçados em distância.

    Devolve None quando a volta não serve para identificar: sem posição
    gravada (voltas anteriores ao schema v4) ou curta demais.

    Espaçamento por distância, e não por tempo ou por índice de amostra: uma
    volta rápida e uma lenta têm contagens de amostra diferentes e concentram
    amostras em lugares diferentes da pista. Por distância, o ponto k descreve
    o mesmo lugar do circuito nas duas.
    """
    validos = [
        p for p in points
        if getattr(p, "position_x", None) is not None
        and getattr(p, "position_z", None) is not None
        and getattr(p, "distance_m", None) is not None
    ]
    if len(validos) < n:
        return None

    total = validos[-1].distance_m
    if total < MIN_DISTANCIA_M:
        return None

    assinatura = []
    indice = 0
    for k in range(n):
        alvo = total * k / n
        # As distâncias são crescentes, então basta avançar — sem busca binária,
        # o laço inteiro é uma passada só sobre as amostras.
        while indice + 1 < len(validos) and validos[indice + 1].distance_m < alvo:
            indice += 1
        assinatura.append((validos[indice].position_x, validos[indice].position_z))
    return assinatura


def desvio_medio(a, b) -> float | None:
    """Distância média entre pontos correspondentes de duas assinaturas, em metros.

    None quando as assinaturas não são comparáveis (tamanhos diferentes).
    """
    if not a or not b or len(a) != len(b):
        return None
    soma = 0.0
    for (ax, az), (bx, bz) in zip(a, b):
        soma += math.hypot(ax - bx, az - bz)
    return soma / len(a)


def identify_track(assinatura, candidatas: dict) -> tuple[int, float] | None:
    """Escolhe a pista cujo traçado casa com a assinatura.

    `candidatas` é `{track_id: assinatura}`. Devolve `(track_id, desvio)` ou
    None quando não há decisão confiável.

    Duas condições, e as duas precisam valer:

    1. o desvio médio fica abaixo do limiar — é a mesma pista, não só a mais
       parecida entre as disponíveis;
    2. o segundo colocado está bem pior — se dois traçados disputam de perto,
       a resposta honesta é "não sei".

    Não decidir é um resultado legítimo: a volta fica sem pista e o usuário
    escolhe. Chutar contaminaria o histórico de uma pista com voltas de outra,
    e isso estraga recordes e comparações de forma difícil de perceber.
    """
    if not assinatura or not candidatas:
        return None

    pontuadas = []
    for track_id, outra in candidatas.items():
        d = desvio_medio(assinatura, outra)
        if d is not None:
            pontuadas.append((d, track_id))
    if not pontuadas:
        return None

    pontuadas.sort()
    melhor_desvio, melhor_id = pontuadas[0]
    if melhor_desvio > MAX_DESVIO_MEDIO_M:
        return None

    if len(pontuadas) > 1:
        segundo = pontuadas[1][0]
        # Desvio praticamente zero (mesma volta) não precisa de margem — a
        # divisão explodiria e a comparação perderia o sentido.
        if melhor_desvio > 1e-6 and segundo / melhor_desvio < MARGEM_MINIMA:
            return None

    return melhor_id, melhor_desvio
