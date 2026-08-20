"""`best` — o recorde na pista atual."""

from __future__ import annotations

from typing import Any

from .. import formatting
from . import Command, Context


def run(context: Context, _args: list[str]) -> str:
    track = _current_track(context)
    if track is None:
        return "Nenhuma pista selecionada ainda."

    best = context.laps.get_best(track.id)
    if best is None:
        return f"Nenhuma volta gravada em {track.name}."
    return f"★ **{formatting.lap_time(best.lap_time_ms)}** em {track.name}"


def _current_track(context: Context) -> Any:
    name = context.track_name
    for track in context.tracks.get_all():
        if track.name == name:
            return track
    return None


COMMAND = Command(name="best", help="Melhor volta gravada na pista atual", run=run)
