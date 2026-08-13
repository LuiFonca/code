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
    LapDetail,
    TelemetryViewModel,
    estimate_slip_angle_deg,
    normalize_slip_pct,
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
    "SectorComparison",
    "TelemetryViewModel",
    "estimate_slip_angle_deg",
    "normalize_slip_pct",
]
