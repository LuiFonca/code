"""
Detecta início/fim de volta a partir do stream de telemetria e persiste
cada volta completa no banco de dados, associada a uma pista específica.

Roda na thread principal do Qt (junto com a interface) — o trabalho aqui
é leve (só acumular listas em memória), então não precisa de thread própria,
diferente da captura de rede.
"""

from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from . import lap_storage
from .lap_comparator import LapComparator


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


class LapRecorder(QObject):
    # Emitido sempre que uma volta é salva: (lap_id, tempo_ms, é_melhor_volta)
    lap_saved = Signal(int, int, bool)

    # Emitido a cada frame com referência disponível: delta em segundos
    # (positivo = mais devagar que a referência, negativo = mais rápido).
    # Emite None quando não há referência ainda, ou quando já passamos do
    # fim da volta de referência.
    delta_changed = Signal(object)

    def __init__(self, track_id: int, parent=None):
        super().__init__(parent)
        lap_storage.init_db()

        self.track_id = track_id
        self._buffer: list[RecordedFrame] = []
        self._cumulative_distance = 0.0
        self._last_lap_count = None
        self._last_current_lap_ms = None

        self._comparator = LapComparator([])
        self._load_reference()

    def set_track(self, track_id: int):
        """Troca a pista ativa (ex: ao reconectar numa sessão nova).
        Descarta qualquer volta em andamento e recarrega a referência."""
        self.track_id = track_id
        self._buffer = []
        self._cumulative_distance = 0.0
        self._last_lap_count = None
        self._last_current_lap_ms = None
        self._load_reference()

    def _load_reference(self):
        """(Re)carrega a volta de referência (a melhor salva até agora) do banco."""
        best = lap_storage.get_best_lap_time(self.track_id)
        if best is None:
            self._comparator = LapComparator([])
            return
        best_lap_id, _ = best
        frames = lap_storage.get_lap_frames(best_lap_id)
        self._comparator = LapComparator(frames)

    def on_frame(self, frame):
        """Chamado a cada frame recebido (mesma taxa da rede, ~60/s)."""

        # Detecta troca de volta: lap_count incrementa toda vez que uma
        # volta é cruzada. Nesse momento, last_lap_ms já traz o tempo total
        # da volta que acabou de ser completada — não precisamos somar nada
        # manualmente.
        if self._last_lap_count is not None and frame.lap_count != self._last_lap_count:
            self._finalize_lap(frame.last_lap_ms)

        # Estima a distância percorrida integrando velocidade pelo tempo de
        # jogo decorrido entre frames (current_lap_ms). Preferimos o relógio
        # do jogo ao relógio de parede porque não sofre variação de rede.
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
        ))

        self._last_lap_count = frame.lap_count
        self._last_current_lap_ms = frame.current_lap_ms

        if self._comparator.has_reference:
            delta_ms = self._comparator.delta_ms_at(self._cumulative_distance, frame.current_lap_ms)
            self.delta_changed.emit(None if delta_ms is None else delta_ms / 1000)
        else:
            self.delta_changed.emit(None)

    def _finalize_lap(self, lap_time_ms: int):
        # Ignora voltas inválidas (buffer vazio ou tempo zerado/negativo,
        # que pode acontecer na primeira volta detectada após conectar).
        if not self._buffer or not lap_time_ms or lap_time_ms <= 0:
            self._buffer = []
            self._cumulative_distance = 0.0
            return

        # CRÍTICO: independentemente do salvamento funcionar ou não, o
        # buffer e o estado da volta são resetados no final (bloco finally).
        # Sem isso, uma falha ao salvar (ex: erro de banco) faz esta mesma
        # volta ser detectada como "não finalizada" no próximo frame,
        # tentando salvar de novo a ~60x/s — um loop de erro que trava o
        # app e satura o banco de conexões.
        try:
            previous_best = lap_storage.get_best_lap_time(self.track_id)
            lap_id = lap_storage.save_lap(self.track_id, lap_time_ms, self._buffer)
            is_best = previous_best is None or lap_time_ms < previous_best[1]

            self.lap_saved.emit(lap_id, lap_time_ms, is_best)

            if is_best:
                # A volta que acabou de terminar virou a nova referência —
                # recarrega direto do buffer que já temos em memória, sem
                # precisar ir ao banco de novo.
                self._comparator = LapComparator([
                    (
                        f.elapsed_ms, f.distance_m, f.speed_kmh, f.rpm, f.gear,
                        f.throttle, f.brake, f.fuel_level,
                        f.tire_temp_fl, f.tire_temp_fr, f.tire_temp_rl, f.tire_temp_rr,
                    )
                    for f in self._buffer
                ])
        except Exception as e:
            # Não deixa a exceção subir e travar a thread de UI: registra
            # no console e segue em frente. A volta em questão é perdida,
            # mas o app continua funcionando para as próximas voltas.
            print(f"[HANNA GT7 AI] Falha ao salvar volta, dado descartado: {e}")
        finally:
            self._buffer = []
            self._cumulative_distance = 0.0
