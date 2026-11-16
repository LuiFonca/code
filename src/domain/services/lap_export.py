"""
Volta em CSV, para abrir em planilha.

O JSON de exportação serve para o próprio app ler de volta; ninguém abre
aquilo no Excel. O CSV é o contrário: uma linha por amostra, cabeçalho com os
nomes dos canais, pronto para gráfico e fórmula.
"""

import csv
import io
from dataclasses import fields

from ..models.telemetry_point import TelemetryPoint

CSV_COLUMNS: tuple[str, ...] = tuple(f.name for f in fields(TelemetryPoint))


def lap_to_csv(points) -> str:
    """Texto CSV das amostras, com cabeçalho.

    Ponto como separador decimal e vírgula entre colunas — o formato que
    qualquer planilha importa sem perguntar. Valores ausentes ficam vazios, e
    não zero: zero seria uma medida.
    """
    saida = io.StringIO()
    escritor = csv.writer(saida, lineterminator="\n")
    escritor.writerow(CSV_COLUMNS)
    for p in points:
        escritor.writerow(
            ["" if (v := getattr(p, c)) is None else v for c in CSV_COLUMNS]
        )
    return saida.getvalue()
