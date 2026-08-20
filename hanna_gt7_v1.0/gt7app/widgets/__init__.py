"""
Widgets reutilizáveis, todos alimentados pelos tokens do design system.

Nenhum deles conhece telemetria: recebem texto, números e cores já decididos.
É o que permite usar o mesmo cartão na página ao vivo e na de comparação sem
arrastar regra de negócio junto.
"""

from .cards import Badge, Card, MetricCard, MetricGrid, PageHeader, StatRow
from .charts import DistanceChart, Series
from .palette import CommandPalette
from .selectors import TrackLapSelector, describe_lap, format_delta, format_lap_time
from .trackmap import TrackMap, TrackMarker, TrackPath

__all__ = [
    "Badge",
    "Card",
    "CommandPalette",
    "DistanceChart",
    "MetricCard",
    "MetricGrid",
    "PageHeader",
    "Series",
    "StatRow",
    "TrackLapSelector",
    "TrackMap",
    "TrackMarker",
    "TrackPath",
    "describe_lap",
    "format_delta",
    "format_lap_time",
]
