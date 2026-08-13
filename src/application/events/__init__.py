"""Barramento de eventos e os fatos que trafegam nele."""

from .event_bus import EventBus, Handler
from .events import (
    CarChanged,
    CarDetected,
    ConnectionStateChanged,
    DeltaUpdated,
    LapCompleted,
    LapDiscarded,
    LapSaveFailed,
    SessionEnded,
    SessionStarted,
    TelemetryReceived,
    TrackCandidatesDetected,
    TrackChanged,
)

__all__ = [
    "EventBus",
    "Handler",
    "CarChanged",
    "CarDetected",
    "ConnectionStateChanged",
    "DeltaUpdated",
    "LapCompleted",
    "LapDiscarded",
    "LapSaveFailed",
    "SessionEnded",
    "SessionStarted",
    "TelemetryReceived",
    "TrackCandidatesDetected",
    "TrackChanged",
]
