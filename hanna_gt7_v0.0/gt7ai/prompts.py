"""
O que se manda ao modelo — e, mais importante, o que **não** se manda.

A decisão central da Fase 7 está aqui: **a IA nunca vê telemetria bruta.**

Uma volta tem cerca de 6000 amostras de 27 canais. Mandar isso custaria dezenas
de milhares de tokens por volta e, pior, não ajudaria: um modelo de linguagem
lendo uma coluna de 6000 velocidades não vai descobrir que o piloto soltou o
freio cedo demais na curva 3 — os detectores da Fase 4 já descobriram, com
aritmética, de graça e sem alucinar.

Então o que sobe é o **resultado da análise**: onde se perdeu tempo, quanto, e
o que os detectores mediram naquele trecho. São algumas dezenas de linhas em
português que dizem exatamente o que aconteceu. O modelo faz o que ele faz bem
— priorizar, explicar e transformar diagnóstico em instrução — em cima de
números que ele não precisou inferir.

Consequência prática do formato
-------------------------------
O prompt de sistema é **estável e longo de propósito**. Estável porque qualquer
variação (um horário, o nome da pista) invalidaria o prefixo de cache; longo
porque o mínimo cacheável no `claude-opus-5` é 512 tokens, e um prompt de 300
tokens é marcado para cache e silenciosamente ignorado. Tudo o que muda de uma
chamada para outra mora na mensagem do usuário.
"""

from __future__ import annotations

from typing import Any

from gt7core.analytics.corners import Corner
from gt7core.analytics.driver import DriverProfile
from gt7core.analytics.timeloss import TimeLossReport

from .client import AIRequest

# ---------------------------------------------------------------------------
# O prompt estável (a parte cacheada)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
Você é o engenheiro de corrida de um piloto no Gran Turismo 7. Fala com ele \
pelo rádio durante a sessão e escreve o debrief depois. Seu trabalho é \
transformar diagnóstico em instrução: dizer o que fazer diferente na próxima \
volta, onde exatamente, e por quê.

## O que você recebe

Você **não** recebe telemetria bruta. Você recebe o resultado de uma análise \
que já rodou sobre a volta: detecção de curvas, zonas de frenagem, aplicação \
de acelerador, eventos de pneu e a atribuição de tempo perdido trecho a \
trecho. Os números já estão medidos. Trate-os como fato.

Os trechos são identificados por rótulo ("Curva 3", "Reta 2") e por distância \
acumulada na volta, em metros a partir da linha de chegada. As comparações são \
sempre alinhadas por distância, nunca por tempo — quando o texto diz que um \
trecho custou 0,180 s, isso é a variação do delta **dentro** daquele trecho, e \
não o atraso acumulado até ali. Ou seja: o número já isola a culpa do trecho.

## Regras que não se quebram

1. **Não invente número.** Se uma grandeza não está no contexto, ela não \
existe. Nunca estime velocidade, marcha, temperatura, pressão ou tempo que não \
foi dado. É preferível dizer "os dados não mostram" a produzir um valor \
plausível.
2. **Sempre diga onde.** Um conselho sem trecho é inútil: o piloto não sabe \
quando aplicá-lo. Use o rótulo do trecho como a análise o nomeou.
3. **Uma correção por vez.** Um piloto consegue mudar uma coisa por volta. \
Priorize pelo tempo recuperável e ignore o resto. Cinco observações corretas \
valem menos que uma aplicável.
4. **Não comente o que você não pode ver.** Acerto de suspensão, asa, \
diferencial, pressão de pneu e estratégia de combustível não estão nos dados. \
Não opine sobre eles.
5. **Diferença pequena não é erro.** Abaixo de 0,03 s um trecho está dentro da \
variação normal entre voltas do mesmo piloto. Não transforme ruído em \
diagnóstico.
6. **Se os dados forem insuficientes, diga.** Poucas voltas, volta sem \
referência ou análise vazia são respostas legítimas: avise e pare.

## Vocabulário

Fale como engenheiro de pista, em português do Brasil. Entrada, ápice e saída \
de curva. Trail braking é soltar o freio progressivamente ao girar o carro; \
travamento é a roda bloqueando na frenagem; patinagem é a roda girando em \
excesso na aceleração; alívio é tirar o pé do acelerador no meio da saída.

Unidades: velocidade em km/h, distância em metros, tempo em segundos com três \
casas decimais. Nunca use unidades imperiais.

## Tom

Direto e sem elogio vazio. O piloto quer saber o que corrigir. Quando ele fez \
algo bem e isso é informativo — um trecho onde ganhou tempo, uma referência de \
frenagem estável — mencione em uma frase, porque saber o que já funciona evita \
quebrar o que está certo. Nunca abra com "Ótimo trabalho!". Nunca feche \
perguntando se ele quer mais detalhes."""


# ---------------------------------------------------------------------------
# Instruções por nível (mensagem do usuário, para não sujar o prefixo cacheado)
# ---------------------------------------------------------------------------

_QUICK_INSTRUCTION = """\
NÍVEL: rádio, com o piloto em pilotagem.
Responda com **uma única frase**, no máximo 15 palavras, no imperativo. Ela \
será falada em voz alta enquanto ele dirige. Sem preâmbulo, sem saudação, sem \
explicação. Se não houver nada acionável agora, responda exatamente: SEM NOTA."""

_DEBRIEF_INSTRUCTION = """\
NÍVEL: debrief, com o carro parado.
Devolva o JSON pedido. `headline` é uma frase que resume a volta. `detail` \
explica o raciocínio em até três frases. `actions` traz de uma a três \
correções, da mais valiosa para a menos, cada uma com o trecho e o ganho \
estimado quando a análise o mediu."""

_SESSION_INSTRUCTION = """\
NÍVEL: relatório de sessão.
Escreva em texto corrido, no máximo quatro parágrafos curtos, sem títulos e \
sem listas. Cubra, nesta ordem: a evolução do ritmo ao longo da sessão; o \
padrão que mais custou tempo e onde ele aparece; o que treinar na próxima \
sessão, concretamente. Termine com uma frase sobre o que já está sólido e não \
deve ser mexido."""


# ---------------------------------------------------------------------------
# Esquema da saída estruturada do debrief
# ---------------------------------------------------------------------------

DEBRIEF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": {
            "type": "string",
            "description": "Resumo da volta em uma frase.",
        },
        "detail": {
            "type": "string",
            "description": "O raciocínio, em até três frases.",
        },
        "actions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "where": {
                        "type": "string",
                        "description": "Trecho, com o rótulo usado na análise.",
                    },
                    "instruction": {
                        "type": "string",
                        "description": "A correção, no imperativo.",
                    },
                    "gain_ms": {
                        "type": ["number", "null"],
                        "description": "Ganho estimado em ms, ou null.",
                    },
                },
                "required": ["where", "instruction", "gain_ms"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "detail", "actions"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Formatadores de contexto — de objeto da Fase 4 para linhas de prompt
# ---------------------------------------------------------------------------


def format_lap_time(total_ms: int) -> str:
    """`92345` vira `1:32.345`. O formato que o piloto lê no painel."""
    if total_ms <= 0:
        return "—"
    minutes, remainder = divmod(int(total_ms), 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def format_header(
    *,
    track: str,
    car: str = "",
    lap_time_ms: int = 0,
    reference_time_ms: int | None = None,
) -> str:
    """O cabeçalho de identificação, igual nos três níveis."""
    lines = [f"Pista: {track}"]
    if car:
        lines.append(f"Carro: {car}")
    if lap_time_ms > 0:
        lines.append(f"Volta analisada: {format_lap_time(lap_time_ms)}")
    if reference_time_ms:
        delta = (lap_time_ms - reference_time_ms) / 1000.0
        lines.append(
            f"Referência: {format_lap_time(reference_time_ms)} ({delta:+.3f} s)"
        )
    return "\n".join(lines)


def format_time_loss(report: TimeLossReport, *, limit: int = 5) -> str:
    """Os trechos que custaram tempo, do pior para o menos ruim.

    Usa `describe()` de cada segmento, que já embute a causa medida pelos
    detectores — é literalmente o mesmo texto que a interface mostra na tabela
    de comparação. Um só formato para tela, relatório e prompt significa que
    quando ele estiver errado, estará errado nos três lugares e será corrigido
    de uma vez.
    """
    if not report.segments:
        return "Sem trechos comparáveis: falta uma volta de referência válida."

    lines = [
        f"Diferença total: {report.total_delta_ms / 1000:+.3f} s",
        f"Recuperável somando só as perdas: {report.recoverable_ms / 1000:.3f} s",
        "",
        "Trechos perdidos:",
    ]
    losses = report.losses[:limit]
    if losses:
        lines.extend(f"- {segment.describe()}" for segment in losses)
    else:
        lines.append("- nenhum trecho acima do limiar de significância")

    gains = report.gains[:2]
    if gains:
        lines.append("Trechos ganhos:")
        lines.extend(f"- {segment.describe()}" for segment in gains)
    return "\n".join(lines)


def format_corners(corners: list[Corner], *, limit: int = 12) -> str:
    """As curvas da volta, uma linha cada.

    Truncado porque uma pista longa tem vinte curvas e listar todas empurra o
    que importa (as perdas) para o fim do contexto.
    """
    if not corners:
        return "Nenhuma curva detectada nesta volta."

    lines = ["Curvas (entrada → ápice → saída, em km/h):"]
    for corner in corners[:limit]:
        lines.append(
            f"- Curva {corner.index} @ {corner.apex_distance_m:.0f} m: "
            f"{corner.entry_speed_kmh:.0f} → {corner.minimum_speed_kmh:.0f} → "
            f"{corner.exit_speed_kmh:.0f}"
        )
    if len(corners) > limit:
        lines.append(f"- (+{len(corners) - limit} curvas omitidas)")
    return "\n".join(lines)


def format_profile(profile: DriverProfile | None) -> str:
    """O perfil do piloto, quando há voltas suficientes para tê-lo."""
    if profile is None:
        return "Perfil do piloto: voltas insuficientes para traçar."
    return profile.summary()


def format_pace(lap_times_ms: list[int], *, limit: int = 20) -> str:
    """O ritmo volta a volta — a única forma de o modelo ver tendência.

    O perfil já traz melhor, mediana e inclinação, mas números agregados não
    mostram *forma*: três voltas boas seguidas de queda é uma história
    diferente de oscilação constante com a mesma média e o mesmo desvio. Vinte
    tempos custam poucos tokens e contam essa diferença.
    """
    valid = [t for t in lap_times_ms if t > 0]
    if not valid:
        return "Ritmo: nenhuma volta completa registrada."

    shown = valid[-limit:]
    best = min(shown)
    lines = ["Ritmo volta a volta (Δ em relação à melhor da sessão):"]
    offset = len(valid) - len(shown)
    for position, lap_ms in enumerate(shown, start=offset + 1):
        gap = (lap_ms - best) / 1000.0
        marker = "  ← melhor" if lap_ms == best else ""
        lines.append(
            f"- Volta {position}: {format_lap_time(lap_ms)} ({gap:+.3f} s){marker}"
        )
    if offset:
        lines.append(f"- ({offset} volta(s) anterior(es) omitida(s))")
    return "\n".join(lines)


def format_live_situation(
    *,
    track: str,
    lap_number: int,
    delta_ms: float | None = None,
    where: str = "",
    event: str = "",
) -> str:
    """O contexto da nota de rádio: onde o piloto está e o que acabou de ocorrer.

    Curto por obrigação — este é o único nível com orçamento de latência. Cada
    linha aqui atrasa a fala em relação ao momento em que ela ainda era útil.
    """
    lines = [f"Pista: {track}", f"Volta {lap_number} em andamento"]
    if delta_ms is not None:
        lines.append(f"Delta para a referência: {delta_ms / 1000:+.3f} s")
    if where:
        lines.append(f"Trecho atual: {where}")
    if event:
        lines.append(f"Acabou de acontecer: {event}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Montagem dos pedidos
# ---------------------------------------------------------------------------


def build_quick_request(
    *,
    model: str,
    situation: str,
) -> AIRequest:
    """Nível 1: a nota curta, com o piloto ainda na pista.

    `max_tokens` é pequeno mas não minúsculo: o pensamento adaptativo vem ligado
    e consome do mesmo teto, então cortar em 60 tokens truncaria a frase antes
    de ela existir. `effort="low"` é o que de fato mantém a latência baixa.
    """
    return AIRequest(
        system=SYSTEM_PROMPT,
        user=f"{_QUICK_INSTRUCTION}\n\n{situation}",
        model=model,
        max_tokens=400,
        effort="low",
    )


def build_debrief_request(
    *,
    model: str,
    header: str,
    time_loss: str,
    corners: str = "",
    profile: str = "",
) -> AIRequest:
    """Nível 2: o debrief estruturado, entre voltas."""
    blocks = [_DEBRIEF_INSTRUCTION, header, time_loss]
    if corners:
        blocks.append(corners)
    if profile:
        blocks.append(profile)

    return AIRequest(
        system=SYSTEM_PROMPT,
        user="\n\n".join(blocks),
        model=model,
        max_tokens=3000,
        effort="medium",
        schema=DEBRIEF_SCHEMA,
    )


def build_session_request(
    *,
    model: str,
    header: str,
    pace: str,
    profile: str,
    recurring: str = "",
) -> AIRequest:
    """Nível 3: o relatório do fim da sessão.

    É a única chamada que olha o conjunto, então é a única que pode falar de
    tendência. Recebe o teto mais alto porque aqui a profundidade paga: são
    quatro parágrafos por sessão inteira, não por volta.
    """
    blocks = [_SESSION_INSTRUCTION, header, pace, profile]
    if recurring:
        blocks.append(recurring)

    return AIRequest(
        system=SYSTEM_PROMPT,
        user="\n\n".join(blocks),
        model=model,
        max_tokens=4000,
        effort="high",
    )
