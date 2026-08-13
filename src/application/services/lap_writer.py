"""
Gravação de voltas fora da thread da interface.

A gravação acontece exatamente ao cruzar a linha de chegada — o pior momento
possível para travar a tela, porque é quando o delta e o tempo de volta
importam. Medido antes desta mudança: uma volta de 90 s a 60 Hz (~5.400
amostras) segurava a interface por 32 ms, e uma volta longa por 65 ms.

O repositório SQLite já era seguro para uso fora da thread principal — a
conexão usa `check_same_thread=False` e as escritas passam por um lock — então
o que faltava era só tirar a chamada do caminho de renderização.
"""

import queue
import threading
from typing import Callable

from ...domain.interfaces.lap_repository import LapRepository
from ...domain.models.lap import Lap

# Sentinela de encerramento. Um objeto próprio em vez de None para não colidir
# com um valor legítimo na fila.
_STOP = object()


class LapWriter:
    """Fila de gravação com uma única thread de trabalho.

    Uma thread só, de propósito: as gravações têm que manter a ordem em que as
    voltas foram completadas, e a política de retenção lê o estado do banco
    logo depois de inserir — duas gravações simultâneas na mesma pista
    poderiam podar uma à outra.
    """

    def __init__(
        self,
        lap_repository: LapRepository,
        on_saved: Callable[[Lap, int, int, object], None] | None = None,
        on_error: Callable[[Lap, Exception], None] | None = None,
    ):
        self._laps = lap_repository
        self._on_saved = on_saved
        self._on_error = on_error
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread = threading.Thread(
            target=self._run, name="lap-writer", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Encerra depois de escoar a fila.

        O `join` com prazo importa: fechar o app logo após cruzar a linha não
        pode descartar a volta que acabou de ser feita.
        """
        if not self._started:
            return
        self._queue.put(_STOP)
        if self._thread is not None:
            self._thread.join(timeout)
        self._started = False
        self._thread = None

    def submit(self, lap: Lap, context: object = None) -> None:
        """Enfileira a volta. Retorna na hora, sem tocar no banco.

        `context` volta intacto no callback — o serviço usa para carregar o
        `is_best`, decidido antes da gravação porque depende de estado que só
        existe no momento em que a volta fecha.
        """
        if not self._started:
            self.start()
        self._queue.put((lap, context))

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            lap, context = item
            try:
                lap_id = self._laps.save(lap)
                purged = getattr(self._laps, "last_purged_count", 0)
                if self._on_saved is not None:
                    self._on_saved(lap, lap_id, purged, context)
            except Exception as exc:  # noqa: BLE001
                # A falha não pode matar a thread: a próxima volta ainda tem
                # que ser gravada. Quem assina decide o que mostrar.
                if self._on_error is not None:
                    self._on_error(lap, exc)
