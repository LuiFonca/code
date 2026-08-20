"""`status` — o que está acontecendo agora."""

from __future__ import annotations

from .. import formatting
from . import Command, Context


def run(context: Context, _args: list[str]) -> str:
    laps = getattr(context.session, "laps", []) or []
    times = [lap.lap_time_ms for lap in laps if lap.lap_time_ms > 0]
    return formatting.status(
        connected=bool(times) or bool(context.track_name),
        track=context.track_name,
        car=context.car_name,
        lap_count=len(laps),
        best_ms=min(times) if times else None,
    )


COMMAND = Command(name="status", help="Pista, carro e voltas da sessão atual", run=run)
