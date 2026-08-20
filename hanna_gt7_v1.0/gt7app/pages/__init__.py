"""
Páginas da aplicação.

Uma página por pergunta que o piloto faz: o que está acontecendo agora (`live`),
o que aconteceu nesta volta (`analysis`), por que a outra foi mais rápida
(`compare`), o que já foi feito nesta pista (`history`) e como eu piloto
(`driver`).
"""

from .analysis import AnalysisPage
from .base import Page
from .compare import ComparePage
from .driver import DriverPage
from .history import HistoryPage
from .live import LivePage

__all__ = [
    "AnalysisPage",
    "ComparePage",
    "DriverPage",
    "HistoryPage",
    "LivePage",
    "Page",
]
