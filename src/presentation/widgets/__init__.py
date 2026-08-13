"""
Widgets reutilizáveis, sem conhecimento de domínio.

Recebem números e cores prontos e desenham. Nenhum consulta repositório ou
ViewModel — é o que permite reaproveitá-los em qualquer aba.
"""

from .widgets import BarCard, DeltaCard, MetricCard, format_ms
from .widgets_chart import (
    LiveDualStripChart,
    LiveStripChart,
    SyncedMiniChart,
    TrackMapWidget,
)
from .widgets_tire import TireTempPanel, TireTempWidget

__all__ = [
    "BarCard",
    "DeltaCard",
    "LiveDualStripChart",
    "LiveStripChart",
    "MetricCard",
    "SyncedMiniChart",
    "TireTempPanel",
    "TireTempWidget",
    "TrackMapWidget",
    "format_ms",
]
