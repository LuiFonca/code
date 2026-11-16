"""
Resumo de fim de sessão.

Ao desconectar, o app sabia tudo o que tinha acontecido e não dizia nada. O
resumo é o que o piloto anotaria num caderno: quantas voltas, a melhor, o
ritmo médio e o quão repetível ele foi.
"""

from ...domain.services.session_stats import consistency_ms


def _fmt(ms) -> str:
    if ms is None:
        return "--:--.---"
    ms = int(round(ms))
    minutos, resto = divmod(ms, 60000)
    return f"{minutos}:{resto / 1000:06.3f}"


def build_session_summary(session, ideal_ms: int | None = None) -> str:
    """Texto do resumo. Vazio quando a sessão não teve volta gravada.

    Só voltas completas e válidas entram nas médias: a volta de saída e as
    marcadas como inválidas distorceriam o ritmo.
    """
    voltas = list(getattr(session, "laps", []) or [])
    if not voltas:
        return ""

    contam = [
        v for v in voltas
        if v.is_complete and v.is_valid and v.lap_time_ms > 0
    ]
    linhas = ["RESUMO DA SESSÃO", "=" * 44]

    pista = getattr(getattr(session, "track", None), "name", None)
    carro = getattr(getattr(session, "car", None), "name", None)
    if pista:
        linhas.append(f"  pista            : {pista}")
    if carro:
        linhas.append(f"  carro            : {carro}")
    inicio, fim = getattr(session, "start", None), getattr(session, "end", None)
    if inicio and fim:
        linhas.append(f"  duração          : {(fim - inicio).total_seconds() / 60:.0f} min")

    linhas.append(f"  voltas gravadas  : {len(voltas)}")
    linhas.append(f"  voltas que contam: {len(contam)}")

    if contam:
        tempos = [v.lap_time_ms for v in contam]
        linhas.append(f"  melhor volta     : {_fmt(min(tempos))}")
        linhas.append(f"  média            : {_fmt(sum(tempos) / len(tempos))}")
        desvio = consistency_ms(tempos)
        if desvio is not None:
            linhas.append(f"  consistência     : ±{desvio / 1000:.3f}s")
        if ideal_ms is not None:
            linhas.append(f"  volta ideal      : {_fmt(ideal_ms)}")
            linhas.append(
                f"  margem até ela   : {(min(tempos) - ideal_ms) / 1000:+.3f}s"
            )
    else:
        linhas.append("")
        linhas.append("  Nenhuma volta completa e válida nesta sessão.")
    return "\n".join(linhas)
