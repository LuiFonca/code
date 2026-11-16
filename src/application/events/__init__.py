"""Barramento de eventos e os fatos que trafegam nele."""

from .event_bus import EventBus, Handler
from .events import (
    CarDetected,
    ConnectionStateChanged,
    DeltaUpdated,
    LapCompleted,
    LapDeleted,
    LapDiscarded,
    LapSaveFailed,
    LapsPurged,
    SessionEnded,
    TelemetryReceived,
    TrackCandidatesDetected,
)

__all__ = [
    "EventBus",
    "Handler",
    "CarDetected",
    "ConnectionStateChanged",
    "DeltaUpdated",
    "LapCompleted",
    "LapDeleted",
    "LapDiscarded",
    "LapSaveFailed",
    "LapsPurged",
    "SessionEnded",
    "TelemetryReceived",
    "TrackCandidatesDetected",
]
