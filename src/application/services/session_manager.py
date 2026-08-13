"""
Estado da sessão corrente: qual pista, qual carro, e se as voltas contam.
"""

from datetime import datetime

from ...domain.models.car import Car
from ...domain.models.lap import Lap
from ...domain.models.session import Session
from ...domain.models.track import Track
from ..events.event_bus import EventBus
from ..events.events import (
    CarChanged,
    SessionEnded,
    SessionStarted,
    TrackChanged,
)


class SessionManager:
    """Dono da decisão "esta volta deve ser gravada?".

    Essa regra estava embutida no gravador antigo (`LapRecorder.can_persist`),
    misturada com detecção de volta e cálculo de força G. Isolá-la aqui é o que
    permite mudar a política de gravação sem tocar no caminho quente da
    telemetria.

    Duas condições distintas impedem a gravação, e a diferença importa para a
    mensagem que o usuário vê:

    - **Sem pista definida.** O GT7 não transmite qual pista está em uso, e sem
      isso não há onde arquivar a volta. Note que não existe pista-padrão: um
      nome inventado automaticamente misturaria voltas de circuitos diferentes
      no mesmo histórico, corrompendo recordes e deltas.
    - **Modo replay/IA.** A telemetria continua válida e é exibida ao vivo, mas
      não é do piloto — gravá-la falsificaria os recordes.
    """

    def __init__(self, event_bus: EventBus):
        self._bus = event_bus
        self._session = Session()
        self._track: Track | None = None
        self._car: Car | None = None
        self._is_player_mode = True

    # ---------- estado ----------

    @property
    def session(self) -> Session:
        return self._session

    @property
    def track(self) -> Track | None:
        return self._track

    @property
    def car(self) -> Car | None:
        return self._car

    @property
    def track_id(self) -> int | None:
        return self._track.id if self._track else None

    @property
    def car_id(self) -> int | None:
        return self._car.id if self._car else None

    @property
    def is_player_mode(self) -> bool:
        return self._is_player_mode

    @property
    def can_persist(self) -> bool:
        """True quando a volta que fechar agora deve ser gravada.

        Pista **não** é mais exigida. Antes era, e a consequência era a pior
        possível: o piloto rodava uma sessão inteira e descobria no fim que
        nada tinha sido salvo, com os dados já perdidos. Uma volta sem pista
        ainda é uma volta — ela é gravada com o campo vazio, e o app tenta
        reconhecer a pista pelo desenho do traçado.

        O modo replay/IA continua bloqueando, e por um motivo diferente: ali a
        volta não é do piloto, então gravá-la sujaria os recordes dele.
        """
        return self._is_player_mode

    @property
    def blocked_reason(self) -> str | None:
        """Por que a gravação está bloqueada, em texto pronto para exibir.
        None quando não está bloqueada."""
        if not self._is_player_mode:
            return "modo replay/IA"
        return None

    # ---------- mudanças ----------

    def set_track(self, track: Track | None) -> bool:
        """Define a pista. Devolve True se mudou de fato.

        Pode ser chamado com a captura em andamento: trocar de pista no meio da
        sessão é um caso real (o piloto muda de circuito sem fechar o app) e não
        exige reconectar.
        """
        if (track.id if track else None) == self.track_id:
            return False
        self._track = track
        self._session.track = track
        self._bus.publish(
            TrackChanged(track_id=track.id if track else None,
                         track_name=track.name if track else None)
        )
        return True

    def set_car(self, car: Car | None) -> bool:
        if (car.id if car else None) == self.car_id:
            return False
        self._car = car
        self._session.car = car
        self._bus.publish(
            CarChanged(car_id=car.id if car else None,
                       car_name=car.name if car else None)
        )
        return True

    def set_player_mode(self, is_player: bool) -> None:
        """Alterna entre pilotagem real e replay/IA."""
        self._is_player_mode = is_player

    # ---------- ciclo de vida ----------

    def start_session(self) -> None:
        self._session = Session(
            car=self._car, track=self._track, start=datetime.now(), laps=[]
        )
        self._bus.publish(
            SessionStarted(
                track_name=self._track.name if self._track else None,
                car_name=self._car.name if self._car else None,
            )
        )

    def end_session(self) -> None:
        if self._session.start is None:
            return
        self._session.end = datetime.now()
        self._bus.publish(
            SessionEnded(session_id=self._session.id, lap_count=self._session.lap_count)
        )

    def register_lap(self, lap: Lap) -> Lap:
        """Adiciona a volta à sessão e devolve o registro guardado.

        O registro não carrega as amostras (ver `Session.add_lap`); o `id`
        fica em aberto até a gravação terminar, e quem chamou preenche.
        """
        return self._session.add_lap(lap)
