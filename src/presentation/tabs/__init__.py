"""Abas da janela principal. Cada uma recebe seu ViewModel por construtor."""

from .comparison_tab import ComparisonTab
from .history_tab import HistoryTab
from .live_tab import LiveDashboardTab
from .telemetry_tab import TelemetryTab

__all__ = ["ComparisonTab", "HistoryTab", "LiveDashboardTab", "TelemetryTab"]
