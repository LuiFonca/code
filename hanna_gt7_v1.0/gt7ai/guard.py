"""
O guarda de números — a regra que um modelo pequeno não segue sozinho.

O prompt de sistema manda nunca escrever um número que não esteja no contexto.
Um modelo grande obedece. Um de 4B obedece **quase sempre**, e "quase" é o
problema: a frase inventada não vem marcada, chega ao piloto com a mesma
confiança das verdadeiras, e o piloto não tem como conferir — ele estava
dirigindo.

O ponto deste módulo é que essa regra não precisa depender de obediência. Todo
número que a resposta cita deveria estar no contexto que subiu; verificar isso é
aritmética. Regra verificável mecanicamente é regra que não se quebra.

Por que a tolerância existe
---------------------------
Arredondar não é inventar. "0,652 s" virando "0,65 s" é o engenheiro falando
como gente, e recusar isso tornaria o guarda inútil na prática — ele rejeitaria
justamente as respostas bem escritas. O que se quer barrar é o número que não
tem origem: "170 km/h" quando o contexto diz 165.

O que fica de fora de propósito
-------------------------------
Inteiros pequenos passam sem conferência. "Curva 3", "as duas primeiras", "três
trechos" — são referências e contagens, não medições, e exigi-las no contexto
produziria falso positivo em quase toda frase bem construída.

Unidade também não é invenção. A primeira versão deste módulo recusou uma
resposta **correta**: o contexto dizia "0,652 s perdidos" e o modelo devolveu
`gain_ms: 652`, que é o que o esquema pede pelo próprio nome do campo. Como o
guarda comparava número cru, 652 não tinha origem — e o efeito prático seria a
IA local cair no conselho da Fase 4 em toda volta, desligada por um verificador
que ninguém suspeitaria. Milissegundo e segundo passaram a contar como a mesma
grandeza.

O custo de um falso positivo aqui é maior que o de um falso negativo: um número
inventado que escapa é uma frase errada; um verificador zeloso demais desliga o
recurso inteiro em silêncio.
"""

from __future__ import annotations

import re

_log_free_pattern = re.compile(r"[-+]?\d+(?:[.,]\d+)?")

# Abaixo disto, um inteiro é referência ou contagem, não medição.
SMALL_INTEGER_LIMIT = 10

# Arredondamento aceitável: 2% do valor, com um piso absoluto para que números
# pequenos (0,65 s) não fiquem com uma janela estreita demais para arredondar.
RELATIVE_TOLERANCE = 0.02
ABSOLUTE_TOLERANCE = 0.051


def numbers_in(text: str) -> list[float]:
    """Todos os números de um texto, aceitando vírgula ou ponto decimal."""
    found: list[float] = []
    for token in _log_free_pattern.findall(text):
        try:
            found.append(float(token.replace(",", ".")))
        except ValueError:  # pragma: no cover - o padrão já garante o formato
            continue
    return found


# Milissegundo e segundo são a mesma grandeza em unidades diferentes, e as duas
# circulam neste sistema de propósito: o contexto fala em segundos porque é como
# o piloto lê, e o esquema pede `gain_ms` porque é como o `Action` guarda.
UNIT_SCALES = (1.0, 1000.0, 0.001)


def _matches(value: float, source: float, *, allow_rounding: bool) -> bool:
    """Compara com folga para arredondamento **só na mesma unidade**.

    A distinção não é preciosismo — é o que impede a tolerância de unidade de
    virar uma porteira aberta. O piso absoluto existe para aceitar "0,652 s"
    escrito como "0,65 s"; aplicado também às comparações em escala, ele fazia
    qualquer número de três dígitos dividido por mil cair na faixa onde moram os
    tempos perdidos por trecho. Um "342 km/h" inventado passava por ficar a
    0,045 de "0,297 s" — grandezas sem relação nenhuma.

    Em escala diferente, então, só casamento proporcional: converter unidade não
    inventa dígito, e quem converte devolve o valor exato.
    """
    tolerance = abs(source) * RELATIVE_TOLERANCE
    if allow_rounding:
        tolerance = max(ABSOLUTE_TOLERANCE, tolerance)
    return abs(value - source) <= tolerance


def _is_supported(value: float, sources: list[float]) -> bool:
    if value == int(value) and abs(value) <= SMALL_INTEGER_LIMIT:
        return True
    return any(
        _matches(value / scale, source, allow_rounding=scale == 1.0)
        for source in sources
        for scale in UNIT_SCALES
    )


def unsupported_numbers(answer: str, context: str) -> list[float]:
    """Os números da resposta que não têm origem no contexto.

    Lista vazia significa que tudo o que a resposta afirma numericamente veio
    dos dados. Quem chama decide o que fazer com o resto — aqui não se corrige
    texto, só se aponta.
    """
    sources = numbers_in(context)
    return [value for value in numbers_in(answer) if not _is_supported(value, sources)]


def is_grounded(answer: str, context: str) -> bool:
    return not unsupported_numbers(answer, context)
