"""`last` — a última volta gravada."""

from __future__ import annotations

from .. import formatting
from . import Command, Context


def run(context: Context, _args: list[str]) -> str:
    laps = context.laps.get_all(limit=1)
    if not laps:
        return "Nenhuma volta gravada ainda."

    lap = laps[0]
    best = context.laps.get_best(lap.track_id) if lap.track_id else None
    is_best = best is not None and best.id == lap.id
    return formatting.lap_saved(
        lap, is_best=is_best, best_ms=best.lap_time_ms if best else None
    )


COMMAND = Command(name="last", help="A última volta gravada", run=run)
