"""Modelos do domínio — dataclasses puras, sem dependência de Qt, SQL ou rede."""

from .car import Car
from .lap import Lap
from .maker import Maker
from .session import Session
from .telemetry_point import TelemetryPoint
from .track import Track

__all__ = ["Car", "Lap", "Maker", "Session", "TelemetryPoint", "Track"]
