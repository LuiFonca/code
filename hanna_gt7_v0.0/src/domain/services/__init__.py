"""
Regras de análise de volta — lógica pura, sem Qt, SQL ou rede.

Adição à estrutura original do projeto: `LapComparator` e `LapSeries` são
regra de domínio (interpolação por distância, corte de setores, volta teórica
ideal), não orquestração de aplicação nem detalhe de infraestrutura. Deixá-los
aqui é o que permite testá-los sem subir Qt nem banco.
"""

from .lap_analysis import (
    CHANNELS,
    LapSeries,
    best_combined_sectors,
    compute_delta_series,
    sector_boundaries_m,
    sector_times_from_series,
)
from .lap_comparator import LapComparator

__all__ = [
    "CHANNELS",
    "LapSeries",
    "LapComparator",
    "best_combined_sectors",
    "compute_delta_series",
    "sector_boundaries_m",
    "sector_times_from_series",
]
