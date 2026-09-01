"""
Como um valor do domínio vira texto.

Existe porque `format_lap_time` estava escrita **cinco vezes** — na interface, no
prompt da IA, no bot do Discord, no perfil do piloto e na demonstração de
terminal —, e as cinco já discordavam:

    entrada     UI        IA        demo       perfil     Discord
         0      —         —         0:00.000   0:00.000   —
        -1      —         —        -1:59.999  -1:59.999   —

Um tempo de volta negativo não existe; ele aparece quando o dado chega
corrompido ou quando o motor ainda não fechou a primeira volta. Três lugares
diziam "não sei" e dois imprimiam `-1:59.999` com toda a confiança — e um deles,
`analytics.driver`, alimenta o resumo que vai para o **prompt do engenheiro**,
que foi instruído a nunca inventar grandeza e portanto repetiria o absurdo
fielmente.

A regra é a de sempre neste projeto: o que não foi medido aparece como ausência,
e ausência é um travessão. Aqui, uma vez só.

Mora em `domain` porque formata **valores do domínio** — `lap_time_ms` é campo de
`Lap`. Não conhece Qt, SQL nem rede, e por isso a interface, a IA, o Discord e a
voz podem depender dela sem arrastar nada junto.
"""

from __future__ import annotations

#: Texto de ausência. Um travessão, e não "0:00.000" nem string vazia: zero é um
#: número e afirmaria uma medição; vazio some no meio de uma tabela e vira
#: suspeita de layout quebrado.
UNKNOWN = "—"


def format_lap_time(total_ms: int | float | None) -> str:
    """`92345` vira `1:32.345`. O formato que o jogo mostra no painel.

    Devolve `UNKNOWN` para nada, zero ou negativo — os três significam a mesma
    coisa aqui, que é "não houve volta medida".

    Aceita `float` porque o tempo derivado do motor nem sempre chega inteiro;
    a conversão fica aqui, e não espalhada em `int(...)` nos chamadores.
    """
    if total_ms is None:
        return UNKNOWN
    total = int(total_ms)
    if total <= 0:
        return UNKNOWN

    minutes, remainder = divmod(total, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{minutes}:{seconds:02d}.{millis:03d}"
