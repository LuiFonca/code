"""
Detecta início/fim de volta a partir do stream de telemetria e persiste
cada volta completa no banco de dados, associada a uma pista e carro
específicos.

Roda na thread principal do Qt (junto com a interface) — o trabalho aqui
é leve (só acumular listas em memória), então não precisa de thread própria,
diferente da captura de rede.
"""

import math
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from . import lap_storage
from .gt7_catalog import guess_track_by_length, get_car_full_name
from .lap_comparator import LapComparator

GRAVITY = 9.81
CAR_REEMIT_INTERVAL = 180


@dataclass
class RecordedFrame:
    elapsed_ms: int
    distance_m: float
    speed_kmh: float
    rpm: float
    gear: int
    throttle: float
    brake: float
    fuel_level: float
    tire_temp_fl: float
    tire_temp_fr: float
    tire_temp_rl: float
    tire_temp_rr: float
    position_x: float
    position_z: float
    g_lateral: float
    g_longitudinal: float
    suspension_fl: float
    suspension_fr: float
    suspension_rl: float
    suspension_rr: float
    tire_slip_fl: float
    tire_slip_fr: float
    tire_slip_rl: float
    tire_slip_rr: float
    turbo_boost: float
    oil_temp: float
    water_temp: float


def _frame_to_tuple(f: RecordedFrame) -> tuple:
    return (
        f.elapsed_ms, f.distance_m, f.speed_kmh, f.rpm, f.gear,
        f.throttle, f.brake, f.fuel_level,
        f.tire_temp_fl, f.tire_temp_fr, f.tire_temp_rl, f.tire_temp_rr,
        f.position_x, f.position_z,
        f.g_lateral, f.g_longitudinal,
        f.suspension_fl, f.suspension_fr, f.suspension_rl, f.suspension_rr,
        f.tire_slip_fl, f.tire_slip_fr, f.tire_slip_rl, f.tire_slip_rr,
        f.turbo_boost, f.oil_temp, f.water_temp,
    )


class LapRecorder(QObject):
    lap_saved = Signal(int, int, bool)
    lap_discarded = Signal(int)
    lap_save_error = Signal(str)
    delta_changed = Signal(object)
    delta_previous_changed = Signal(object)

    track_candidates_detected = Signal(list)
    car_detected = Signal(str)

    def __init__(self, track_id, car_id=None, parent=None):
        """track_id/car_id podem ser None: nesse caso a volta é acumulada
        normalmente (para o delta ao vivo continuar funcionando), mas
        NUNCA é persistida."""
        super().__init__(parent)
        lap_storage.init_db()

        self.track_id = track_id
        self.car_id = car_id
        self.is_player_mode = True
        self._buffer: list[RecordedFrame] = []
        self._cumulative_distance = 0.0
        self._last_lap_count = None
        self._last_current_lap_ms = None
        self._detected_car_id = None

        self._prev_velocity_x = None
        self._prev_velocity_z = None
        self._prev_velocity_ms = None
        self._car_reemit_counter = 0

        self._comparator = LapComparator([])
        self._comparator_prev = LapComparator([])
        self._load_reference()

    def set_track(self, track_id):
        if track_id == self.track_id:
            return
        self.track_id = track_id
        self._reset_lap_state()
        self._comparator_prev = LapComparator([])
        self._load_reference()

    def set_car(self, car_id):
        self.car_id = car_id

    def set_player_mode(self, is_player: bool):
        self.is_player_mode = is_player

    @property
    def can_persist(self) -> bool:
        return self.track_id is not None and self.is_player_mode

    def _reset_lap_state(self):
        self._buffer = []
        self._cumulative_distance = 0.0
        self._last_lap_count = None
        self._last_current_lap_ms = None
        self._prev_velocity_x = None
        self._prev_velocity_z = None
        self._prev_velocity_ms = None

    def _load_reference(self):
        if self.track_id is None:
            self._comparator = LapComparator([])
            return
        best = lap_storage.get_best_lap_time(self.track_id)
        if best is None:
            self._comparator = LapComparator([])
            return
        best_lap_id, _ = best
        frames = lap_storage.get_lap_frames(best_lap_id)
        self._comparator = LapComparator(frames)

    def on_frame(self, frame):
        """Chamado a cada frame recebido (~60/s)."""

        if self._last_lap_count is not None and frame.lap_count != self._last_lap_count:
            self._finalize_lap(frame.last_lap_ms)

        if hasattr(frame, 'is_paused') and (frame.is_paused or frame.is_loading):
            self._last_lap_count = frame.lap_count
            self._last_current_lap_ms = frame.current_lap_ms
            return

        g_lateral = 0.0
        g_longitudinal = 0.0

        if (
            self._prev_velocity_x is not None
            and self._prev_velocity_ms is not None
            and frame.current_lap_ms > self._prev_velocity_ms
            and frame.speed_kmh > 5.0
        ):
            dt = (frame.current_lap_ms - self._prev_velocity_ms) / 1000.0
            if dt > 0.001:
                ax = (frame.velocity_x - self._prev_velocity_x) / dt
                az = (frame.velocity_z - self._prev_velocity_z) / dt

                speed_xz = math.sqrt(frame.velocity_x ** 2 + frame.velocity_z ** 2)
                if speed_xz > 0.5:
                    fwd_x = frame.velocity_x / speed_xz
                    fwd_z = frame.velocity_z / speed_xz
                    right_x = -fwd_z
                    right_z = fwd_x

                    g_longitudinal = (ax * fwd_x + az * fwd_z) / GRAVITY
                    g_lateral = (ax * right_x + az * right_z) / GRAVITY

        self._prev_velocity_x = frame.velocity_x
        self._prev_velocity_z = frame.velocity_z
        self._prev_velocity_ms = frame.current_lap_ms

        if (
            self._last_current_lap_ms is not None
            and frame.current_lap_ms >= self._last_current_lap_ms
        ):
            dt_seconds = (frame.current_lap_ms - self._last_current_lap_ms) / 1000
            self._cumulative_distance += (frame.speed_kmh / 3.6) * dt_seconds

        self._buffer.append(RecordedFrame(
            elapsed_ms=frame.current_lap_ms,
            distance_m=self._cumulative_distance,
            speed_kmh=frame.speed_kmh,
            rpm=frame.rpm,
            gear=frame.gear,
            throttle=frame.throttle,
            brake=frame.brake,
            fuel_level=frame.fuel,
            tire_temp_fl=frame.tire_temp_fl,
            tire_temp_fr=frame.tire_temp_fr,
            tire_temp_rl=frame.tire_temp_rl,
            tire_temp_rr=frame.tire_temp_rr,
            position_x=frame.position_x,
            position_z=frame.position_z,
            g_lateral=g_lateral,
            g_longitudinal=g_longitudinal,
            suspension_fl=frame.suspension_fl,
            suspension_fr=frame.suspension_fr,
            suspension_rl=frame.suspension_rl,
            suspension_rr=frame.suspension_rr,
            tire_slip_fl=frame.tire_slip_fl,
            tire_slip_fr=frame.tire_slip_fr,
            tire_slip_rl=frame.tire_slip_rl,
            tire_slip_rr=frame.tire_slip_rr,
            turbo_boost=frame.turbo_boost,
            oil_temp=frame.oil_temp,
            water_temp=frame.water_temp,
        ))

        self._last_lap_count = frame.lap_count
        self._last_current_lap_ms = frame.current_lap_ms

        proto_car_id = getattr(frame, 'car_id', None)
        if proto_car_id is not None and proto_car_id > 0:
            if proto_car_id != self._detected_car_id:
                self._detected_car_id = proto_car_id
                full_name = get_car_full_name(proto_car_id) or ""
                self.car_detected.emit(full_name)
                self._car_reemit_counter = 0
            else:
                self._car_reemit_counter += 1
                if self._car_reemit_counter >= CAR_REEMIT_INTERVAL:
                    self._car_reemit_counter = 0
                    full_name = get_car_full_name(proto_car_id) or ""
                    if full_name:
                        self.car_detected.emit(full_name)

        if self._comparator.has_reference:
            delta_ms = self._comparator.delta_ms_at(self._cumulative_distance, frame.current_lap_ms)
            self.delta_changed.emit(None if delta_ms is None else delta_ms / 1000)
        else:
            self.delta_changed.emit(None)

        if self._comparator_prev.has_reference:
            delta_prev_ms = self._comparator_prev.delta_ms_at(self._cumulative_distance, frame.current_lap_ms)
            self.delta_previous_changed.emit(None if delta_prev_ms is None else delta_prev_ms / 1000)
        else:
            self.delta_previous_changed.emit(None)

    def _finalize_lap(self, lap_time_ms: int):
        if not self._buffer or not lap_time_ms or lap_time_ms <= 0:
            self._reset_lap_state()
            return

        if self._cumulative_distance > 100:
            candidates = guess_track_by_length(self._cumulative_distance)
            names = [t.name for t in candidates[:5]]
            self.track_candidates_detected.emit(names)

        if not self.can_persist:
            if self._buffer:
                self._comparator_prev = LapComparator([
                    _frame_to_tuple(f) for f in self._buffer
                ])
            self.lap_discarded.emit(lap_time_ms)
            self._reset_lap_state()
            return

        try:
            previous_best = lap_storage.get_best_lap_time(self.track_id)
            lap_id = lap_storage.save_lap(self.track_id, self.car_id, lap_time_ms, self._buffer)
            is_best = previous_best is None or lap_time_ms < previous_best[1]

            self.lap_saved.emit(lap_id, lap_time_ms, is_best)

            buffer_as_tuples = [_frame_to_tuple(f) for f in self._buffer]
            self._comparator_prev = LapComparator(buffer_as_tuples)

            if is_best:
                self._comparator = LapComparator(buffer_as_tuples)
        except Exception as e:
            self.lap_save_error.emit(f"Falha ao salvar volta: {e}")
        finally:
            self._reset_lap_state()
