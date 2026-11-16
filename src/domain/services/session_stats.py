"""
Números de sessão: volta ideal, consistência e autonomia de combustível.

São contas pequenas, mas que antes não existiam em lugar nenhum — o piloto
tinha os tempos na tabela e fazia de cabeça.
"""

import math
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class IdealLap:
    """A volta que resultaria de juntar o melhor de cada setor."""

    total_ms: int | None
    sector_ms: list[int | None] = field(default_factory=list)
    # Id da volta que forneceu cada setor — permite dizer de onde veio.
    source_lap_ids: list[int | None] = field(default_factory=list)


def ideal_lap(sector_times_by_lap: dict[int, list[int | None]]) -> IdealLap:
    """Volta ideal a partir dos tempos de setor de várias voltas.

    Diferente da "volta teórica" da Comparação, que junta duas voltas: aqui
    entram todas, e é o número que responde "quanto eu faria se acertasse
    tudo de uma vez". Só é total quando todos os setores têm algum tempo.
    """
    if not sector_times_by_lap:
        return IdealLap(total_ms=None)

    setores = max(len(t) for t in sector_times_by_lap.values())
    melhores: list[int | None] = []
    origem: list[int | None] = []
    for i in range(setores):
        candidato, dono = None, None
        for lap_id, tempos in sector_times_by_lap.items():
            if i < len(tempos) and tempos[i] is not None and tempos[i] > 0:
                if candidato is None or tempos[i] < candidato:
                    candidato, dono = tempos[i], lap_id
        melhores.append(candidato)
        origem.append(dono)

    total = sum(melhores) if melhores and all(m is not None for m in melhores) else None
    return IdealLap(total_ms=total, sector_ms=melhores, source_lap_ids=origem)


def consistency_ms(lap_times_ms: list[int]) -> float | None:
    """Desvio-padrão dos tempos de volta, em ms. None com menos de duas.

    Quanto menor, mais repetível o ritmo. A volta de saída e as muito lentas
    (rodada, tráfego) ficam por conta de quem chama filtrar.
    """
    if len(lap_times_ms) < 2:
        return None
    media = sum(lap_times_ms) / len(lap_times_ms)
    variancia = sum((t - media) ** 2 for t in lap_times_ms) / (len(lap_times_ms) - 1)
    return math.sqrt(variancia)


@dataclass(frozen=True, slots=True)
class FuelEstimate:
    per_lap: float | None          # consumo médio por volta, na unidade do jogo
    laps_remaining: float | None   # voltas que o tanque atual ainda dá


def fuel_estimate(recent_usage: list[float], current_level: float | None) -> FuelEstimate:
    """Consumo médio e autonomia a partir das últimas voltas.

    Ignora consumo não positivo: volta com reabastecimento ou com combustível
    infinito ligado daria média zero e autonomia infinita, que é pior que não
    mostrar nada.
    """
    validos = [u for u in recent_usage if u is not None and u > 0]
    if not validos:
        return FuelEstimate(per_lap=None, laps_remaining=None)
    media = sum(validos) / len(validos)
    if current_level is None or current_level < 0:
        return FuelEstimate(per_lap=media, laps_remaining=None)
    return FuelEstimate(per_lap=media, laps_remaining=current_level / media)
