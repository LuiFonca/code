"""
Fixtures compartilhadas pela suíte.

Três decisões que valem explicar, porque moldam todos os testes:

1. **Banco em memória.** Nenhum teste toca o banco real do usuário. Cada caso
   ganha um SQLite novo e descartável.
2. **Fonte de telemetria falsa.** `TelemetrySource` é ABC, então dá para injetar
   um gerador que emite pacotes sob demanda — sem rede, sem espera, sem PS5.
3. **Uma única QApplication.** Qt não permite duas no mesmo processo; a fixture
   é de escopo de sessão e reutilizada.
"""

import os
import sys
from pathlib import Path

import pytest

# Qt sem tela — precisa vir antes de qualquer import do PySide6.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.application.events.event_bus import EventBus  # noqa: E402
from src.application.services.session_manager import SessionManager  # noqa: E402
from src.application.services.telemetry_service import TelemetryService  # noqa: E402
from src.domain.interfaces.telemetry_source import TelemetrySource  # noqa: E402
from src.domain.models.car import Car  # noqa: E402
from src.domain.models.lap import Lap  # noqa: E402
from src.domain.models.telemetry_point import TelemetryPoint  # noqa: E402
from src.domain.models.track import Track  # noqa: E402
from src.infrastructure.repositories.sqlite_car_repository import (  # noqa: E402
    SqliteCarRepository,
)
from src.infrastructure.repositories.sqlite_database import SqliteDatabase  # noqa: E402
from src.infrastructure.repositories.sqlite_lap_repository import (  # noqa: E402
    SqliteLapRepository,
)
from src.infrastructure.repositories.sqlite_track_repository import (  # noqa: E402
    SqliteTrackRepository,
)


@pytest.fixture(scope="session")
def qapp():
    """QApplication única para toda a sessão de testes."""
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def database():
    """Banco novo em memória por teste."""
    db = SqliteDatabase(":memory:")
    yield db
    db.close()


@pytest.fixture
def laps(database):
    return SqliteLapRepository(database)


@pytest.fixture
def tracks(database):
    return SqliteTrackRepository(database)


@pytest.fixture
def cars(database):
    return SqliteCarRepository(database)


@pytest.fixture
def bus(qapp):
    """Barramento limpo. Depende de `qapp` porque EventBus é QObject."""
    b = EventBus()
    yield b
    b.clear()


# ---------------------------------------------------------------- geradores


def make_point(index: int, total: int, lap_ms: int, distance_m: float, **overrides):
    """Uma amostra sintética, com valores plausíveis em todos os 27 campos."""
    fraction = index / total if total else 0.0
    values = dict(
        elapsed_ms=int(fraction * lap_ms),
        distance_m=fraction * distance_m,
        speed_kmh=100.0 + (index % 80),
        rpm=6000.0,
        gear=4,
        throttle=90.0,
        brake=0.0,
        fuel_level=60.0 - fraction * 3.0,
        tire_temp_fl=80.0, tire_temp_fr=81.0,
        tire_temp_rl=82.0, tire_temp_rr=83.0,
        position_x=index * 0.5, position_z=index * 0.3,
        g_lateral=0.3, g_longitudinal=0.2,
        suspension_fl=1.0, suspension_fr=1.05,
        suspension_rl=1.1, suspension_rr=1.15,
        tire_slip_fl=0.05, tire_slip_fr=0.05,
        tire_slip_rl=0.08, tire_slip_rr=0.08,
        turbo_boost=1.2, oil_temp=95.0, water_temp=88.0,
    )
    values.update(overrides)
    return TelemetryPoint(**values)


@pytest.fixture
def make_lap():
    """Fábrica de voltas sintéticas prontas para gravar."""

    def _make(
        track_id=None, car_id=None, lap_time_ms=90000, samples=200,
        distance_m=3600.0, is_complete=True, is_player=True, **point_overrides,
    ):
        points = [
            make_point(i, samples, lap_time_ms, distance_m, **point_overrides)
            for i in range(samples + 1)
        ]
        return Lap(
            track_id=track_id, car_id=car_id, lap_time_ms=lap_time_ms,
            is_complete=is_complete, is_player=is_player, points=points,
        )

    return _make


class FakeFrame:
    """DTO de fio sintético, com os mesmos atributos do TelemetryFrame real."""

    __slots__ = (
        "speed_kmh", "rpm", "gear", "suggested_gear", "throttle", "brake",
        "fuel", "fuel_capacity", "lap_count", "total_laps",
        "position_x", "position_y", "position_z",
        "velocity_x", "velocity_y", "velocity_z",
        "rotation_i", "rotation_j", "rotation_k", "rotation_w",
        "angular_velocity_x", "angular_velocity_y", "angular_velocity_z",
        "body_height",
        "best_lap_ms", "last_lap_ms", "current_lap_ms",
        "tire_temp_fl", "tire_temp_fr", "tire_temp_rl", "tire_temp_rr",
        "suspension_fl", "suspension_fr", "suspension_rl", "suspension_rr",
        "tire_slip_fl", "tire_slip_fr", "tire_slip_rl", "tire_slip_rr",
        "turbo_boost", "oil_pressure", "oil_temp", "water_temp",
        "rpm_flashing_min", "rpm_flashing_max", "max_speed_kmh", "flags", "car_id",
        "is_paused", "is_loading", "is_on_track",
        "tcs_active", "asm_active", "rev_limiter_active",
    )

    def __init__(self, **kwargs):
        defaults = dict(
            speed_kmh=150.0, rpm=6500.0, gear=5, suggested_gear=0,
            throttle=90.0, brake=0.0, fuel=50.0, fuel_capacity=60.0,
            lap_count=1, total_laps=10,
            position_x=0.0, position_y=0.0, position_z=0.0,
            velocity_x=41.0, velocity_y=0.0, velocity_z=0.0,
            # Quaternion identidade: carro apontando para frente, sem deriva.
            rotation_i=0.0, rotation_j=0.0, rotation_k=0.0, rotation_w=1.0,
            angular_velocity_x=0.0, angular_velocity_y=0.0, angular_velocity_z=0.0,
            body_height=0.1,
            best_lap_ms=0, last_lap_ms=0, current_lap_ms=0,
            tire_temp_fl=80.0, tire_temp_fr=81.0,
            tire_temp_rl=82.0, tire_temp_rr=83.0,
            suspension_fl=1.0, suspension_fr=1.05,
            suspension_rl=1.1, suspension_rr=1.15,
            tire_slip_fl=0.05, tire_slip_fr=0.05,
            tire_slip_rl=0.08, tire_slip_rr=0.08,
            turbo_boost=1.2, oil_pressure=5.0, oil_temp=95.0, water_temp=88.0,
            rpm_flashing_min=7500, rpm_flashing_max=8000, max_speed_kmh=320,
            flags=1, car_id=0,
            is_paused=False, is_loading=False, is_on_track=True,
            tcs_active=False, asm_active=False, rev_limiter_active=False,
        )
        defaults.update(kwargs)
        for key, value in defaults.items():
            setattr(self, key, value)


@pytest.fixture
def frame():
    return FakeFrame


class FakeSource(TelemetrySource):
    """Fonte controlada pelo teste: emite exatamente o que for pedido."""

    def __init__(self):
        super().__init__()
        self._running = False

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def feed(self, frame) -> None:
        self.telemetry_stream.emit(frame)

    def feed_lap(self, lap_no: int, lap_ms: int, samples: int = 120, **overrides):
        """Emite uma volta inteira e a virada que a fecha."""
        for i in range(samples + 1):
            self.feed(
                FakeFrame(
                    lap_count=lap_no,
                    current_lap_ms=int(i * lap_ms / samples),
                    **overrides,
                )
            )
        self.feed(
            FakeFrame(lap_count=lap_no + 1, current_lap_ms=0, last_lap_ms=lap_ms)
        )

    def emit_status(self, state: str) -> None:
        self.status_changed.emit(state)


@pytest.fixture
def source(qapp):
    return FakeSource()


@pytest.fixture
def session(bus):
    return SessionManager(bus)


@pytest.fixture
def service(source, laps, session, bus):
    """Serviço de telemetria montado com dublês, pronto para receber pacotes."""
    svc = TelemetryService(
        telemetry_source=source,
        lap_repository=laps,
        session_manager=session,
        event_bus=bus,
    )
    yield svc
    svc.stop()


@pytest.fixture
def on_track(tracks, cars, session):
    """Sessão com pista e carro definidos — estado em que voltas são gravadas."""
    track_id = tracks.get_or_create("Pista de Teste")
    car_id = cars.get_or_create("Carro de Teste")
    session.set_track(Track(id=track_id, name="Pista de Teste"))
    session.set_car(Car(id=car_id, name="Carro de Teste"))
    return track_id, car_id


@pytest.fixture
def collect(bus):
    """Coletor de eventos por tipo, para asserção depois."""

    def _collect(*event_types):
        captured = []
        for event_type in event_types:
            bus.subscribe(event_type, lambda e: captured.append(e))
        return captured

    return _collect


def drain(qapp, seconds: float = 0.35):
    """Escoa a fila de eventos do Qt e a fila de gravação.

    A gravação roda em thread própria e devolve o resultado por sinal, então
    um simples `processEvents` não basta: é preciso dar tempo à thread e
    processar o que ela publicar.
    """
    import time

    deadline = time.time() + seconds
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


@pytest.fixture
def flush(qapp):
    def _flush(seconds: float = 0.35):
        drain(qapp, seconds)

    return _flush
