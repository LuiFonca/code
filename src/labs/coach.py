"""
Coach: a comparação curva a curva, dita em frases.

A tabela da aba Comparação já tem os números. O que falta para quem acabou de
sair da pista é a leitura: *onde* estão as maiores perdas e *o que* mudou
nelas. Este módulo ordena as curvas pelo tempo perdido e descreve as piores.

É deliberadamente simples — regras, não modelo. Cada frase sai de um número
que está na tabela, então dá para conferir o que o coach disse.
"""

from ..domain.services.corner_analysis import CornerDelta

# Abaixo disto a diferença numa curva é ruído de linha de pilotagem.
PERDA_MINIMA_MS = 30.0
VELOCIDADE_RELEVANTE_KMH = 2.0
FRENAGEM_RELEVANTE_M = 5.0


def _motivo(delta: CornerDelta) -> str:
    """O que mudou na curva, em palavras."""
    partes = []
    vel = delta.min_speed_delta_kmh
    if vel is not None and vel <= -VELOCIDADE_RELEVANTE_KMH:
        partes.append(f"passou {abs(vel):.0f} km/h mais devagar no ponto mais lento")
    freio = delta.brake_point_delta_m
    if freio is not None and freio <= -FRENAGEM_RELEVANTE_M:
        partes.append(f"freou {abs(freio):.0f} m antes")
    elif freio is not None and freio >= FRENAGEM_RELEVANTE_M and vel is not None and vel < 0:
        # Frear tarde e sair lento é o erro clássico de entrada.
        partes.append(f"freou {freio:.0f} m depois e perdeu a saída")

    equilibrio = delta.corner.balance
    if equilibrio in ("subesterço", "sobresterço"):
        partes.append(f"a referência já mostrava {equilibrio} ali")

    if not partes:
        return "sem diferença clara de velocidade mínima ou de frenagem — olhe a saída"
    return ", ".join(partes)


def top_losses(deltas: list[CornerDelta], limit: int = 3) -> list[CornerDelta]:
    """As curvas onde mais se perdeu tempo, da pior para a menos pior."""
    perdas = [
        d for d in deltas
        if d.time_delta_ms is not None and d.time_delta_ms >= PERDA_MINIMA_MS
    ]
    perdas.sort(key=lambda d: d.time_delta_ms, reverse=True)
    return perdas[:limit]


def advice(deltas: list[CornerDelta], limit: int = 3) -> list[str]:
    """Frases prontas para exibir. Lista vazia quando não há o que apontar."""
    if not deltas:
        return []

    perdas = top_losses(deltas, limit)
    if not perdas:
        return ["Nenhuma curva com perda relevante contra a referência."]

    frases = []
    for d in perdas:
        frases.append(
            f"Curva {d.corner.index} ({d.corner.direction}): "
            f"{d.time_delta_ms / 1000:.2f} s perdidos — {_motivo(d)}."
        )
    total = sum(d.time_delta_ms for d in perdas) / 1000
    frases.append(f"Essas {len(perdas)} curva(s) somam {total:.2f} s.")
    return frases
