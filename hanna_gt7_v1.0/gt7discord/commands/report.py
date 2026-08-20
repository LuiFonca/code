"""`report` — o relatório do engenheiro sobre as voltas da pista atual.

É o único comando que pode demorar: consulta o modelo. Quem chama roda isto
fora da thread que recebe a mensagem — ver `bot.py`.
"""

from __future__ import annotations

from gt7core.analytics.driver import build_profile

from .. import formatting
from . import Command, Context

PROFILE_LAP_LIMIT = 20


def run(context: Context, _args: list[str]) -> str:
    if context.engineer is None:
        return "O engenheiro não está instalado nesta máquina."

    track = None
    for candidate in context.tracks.get_all():
        if candidate.name == context.track_name:
            track = candidate
            break
    if track is None:
        return "Nenhuma pista selecionada ainda."

    laps = context.laps.get_by_track(track.id, limit=PROFILE_LAP_LIMIT)
    # Ordem cronológica: a tendência de ritmo depende disso, e o repositório
    # devolve da mais recente para a mais antiga.
    laps = list(reversed(laps))

    point_lists = []
    for lap in laps:
        if lap.id is None:
            continue
        points = context.laps.load_points(lap.id)
        if len(points) >= 2:
            point_lists.append(points)

    profile = build_profile(point_lists)
    if profile is None:
        return f"Voltas insuficientes em {track.name} para um relatório."

    advice = context.engineer.session_report(
        profile,
        track=track.name,
        car=context.car_name,
        lap_times_ms=[lap.lap_time_ms for lap in laps if lap.lap_time_ms > 0],
    )
    return formatting.advice(advice, title=f"Relatório — {track.name}")


COMMAND = Command(
    name="report", help="Relatório do engenheiro sobre a pista atual", run=run
)
