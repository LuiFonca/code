"""
Do domínio para o texto do Discord.

Separado da política de notificação de propósito: o que **dizer** e *quando*
dizer são perguntas diferentes, e misturá-las produz aquela função de 200 linhas
com um `if` para cada caso que ninguém consegue mudar sem quebrar outro.

Uma decisão que atravessa o módulo: **nada de mensagens grandes.** O Discord é
lido no celular, quase sempre entre voltas, com o capacete ainda na cabeça. O
que não couber em três linhas não será lido, e o limite de 2000 caracteres da
plataforma corta o resto sem avisar — então o corte acontece aqui, onde dá para
escolher o que sobra.
"""

from __future__ import annotations

from gt7core.domain.formatting import format_lap_time
from gt7core.domain.models import Lap

# Teto da plataforma. Cortar aqui, com reticências, é melhor que a mensagem
# chegar truncada no meio de uma palavra.
DISCORD_LIMIT = 2000

# Margem para o texto que envolve o corte.
SAFE_LIMIT = 1900


#: O nome curto que as mensagens do bot já usam, apontando para a única
#: implementação. Renomear os chamadores não traria nada.
lap_time = format_lap_time


def delta(ms: float) -> str:
    return f"{ms / 1000:+.3f} s"


def clamp(text: str, limit: int = SAFE_LIMIT) -> str:
    """Corta preservando a última linha inteira que couber.

    Cortar no meio de uma frase produz uma mensagem que parece defeito. Cortar
    numa quebra de linha parece resumo.
    """
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = head.rfind("\n")
    if cut > limit // 2:
        head = head[:cut]
    return head.rstrip() + "\n…"


def lap_saved(lap: Lap, *, is_best: bool, best_ms: int | None = None) -> str:
    """Uma volta gravada, em uma linha.

    O ✓ e o ★ carregam a informação principal antes de qualquer número: o
    piloto olhando de relance precisa saber se melhorou, e só depois quanto.
    """
    mark = "★" if is_best else "•"
    parts = [f"{mark} **{lap_time(lap.lap_time_ms)}**"]

    if is_best:
        parts.append("melhor da sessão")
    elif best_ms and best_ms > 0:
        parts.append(delta(lap.lap_time_ms - best_ms))

    return "  ".join(parts)


def advice(item: object, *, title: str = "") -> str:
    """Um `Advice` do engenheiro em formato de Discord.

    Recebe `object` e lê por `getattr` porque este pacote **não importa
    `gt7ai`**: o bot precisa funcionar com o plugin de IA ausente, e um import
    no topo do módulo faria a formatação de uma volta comum falhar por causa de
    uma dependência que só o debrief usa.
    """
    headline = str(getattr(item, "headline", "")).strip()
    if not headline:
        return ""

    lines: list[str] = []
    if title:
        lines.append(f"**{title}**")
    lines.append(headline)

    detail = str(getattr(item, "detail", "")).strip()
    if detail:
        lines.append(f"_{detail}_")

    for action in getattr(item, "actions", []) or []:
        describe = getattr(action, "describe", None)
        lines.append(f"• {describe() if callable(describe) else action}")

    source = "análise local" if getattr(item, "is_local", False) else (
        str(getattr(item, "model", "")) or "IA"
    )
    lines.append(f"-# {source}")
    return clamp("\n".join(lines))


def session_summary(*, track: str, lap_count: int, best_ms: int | None) -> str:
    parts = [f"**Sessão encerrada** — {track or 'pista não informada'}"]
    parts.append(f"{lap_count} volta(s)")
    if best_ms:
        parts.append(f"melhor {lap_time(best_ms)}")
    return "  ·  ".join(parts)


def status(
    *,
    connected: bool,
    track: str,
    car: str,
    lap_count: int,
    best_ms: int | None,
) -> str:
    lines = [
        f"**Captura:** {'recebendo' if connected else 'parada'}",
        f"**Pista:** {track or '—'}",
        f"**Carro:** {car or '—'}",
        f"**Voltas na sessão:** {lap_count}",
    ]
    if best_ms:
        lines.append(f"**Melhor:** {lap_time(best_ms)}")
    return "\n".join(lines)
