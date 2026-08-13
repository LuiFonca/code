"""
ViewModels — estado de tela e regra de apresentação.

Cada um traduz eventos e repositórios em sinais que a View consome sem saber de
onde vieram. Nenhum importa Qt Widgets: dependem apenas de `QtCore` (QObject,
Signal, QTimer), o que permite exercitá-los sem abrir janela.
"""

from .comparison_viewmodel import ComparisonResult, ComparisonViewModel, SectorComparison
from .history_viewmodel import HistoryViewModel, LapRow
from .live_viewmodel import LiveViewModel
from .telemetry_viewmodel import (
    AXIS_DISTANCE,
    AXIS_TIME,
    MAX_PLOT_POINTS,
    LapDetail,
    TelemetryViewModel,
    resample,
    slip_index_pct,
    slip_level_label,
)

__all__ = [
    "AXIS_DISTANCE",
    "AXIS_TIME",
    "ComparisonResult",
    "ComparisonViewModel",
    "HistoryViewModel",
    "LapDetail",
    "LapRow",
    "LiveViewModel",
    "MAX_PLOT_POINTS",
    "SectorComparison",
    "TelemetryViewModel",
    "resample",
    "slip_index_pct",
    "slip_level_label",
]
