"""
O que vira mensagem, e o que **não** vira.

Esta é a peça com decisão de produto, e por isso é Python puro: sem
`discord.py`, sem asyncio, sem rede. Toda a suíte deste pacote roda contra um
`RecordingSink` e verifica exatamente o que o piloto receberia.

O que não é notificado, e por quê
---------------------------------
`RaceEventDetected` **não** vira mensagem. A Fase 9 mede doze eventos numa
sessão de duas voltas; no celular isso é spam, e spam ensina o piloto a silenciar
o canal — o que mata junto as mensagens que importavam. O evento ao vivo tem
destino próprio: o rádio na tela, que ele olha de relance enquanto pilota.

O Discord serve o outro momento: o piloto parado, olhando o telefone entre
sessões. Ali o que vale é o tempo da volta, o recorde, e o relatório do fim.

Volta comum também não é notificada por padrão. Trinta voltas de treino são
trinta mensagens, e a que interessava — a melhor — fica enterrada. Quem quiser
o registro completo liga `post_every_lap`.

Por que o engenheiro roda numa thread separada
----------------------------------------------
As notificações chegam pelo barramento, na **thread de captura**. Pedir um
debrief ali bloquearia a captura pelos segundos que o modelo leva — o mesmo R2
que a Fase 8 resolveu do lado da interface, com o agravante de que aqui o que
trava é a gravação da telemetria, não a tela.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from gt7core.observability.logging import get_logger
from gt7core.session.manager import (
    LapSaved,
    LapSaveFailed,
    SessionEnded,
    SessionStarted,
)

from . import formatting
from .sink import MessageSink

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class NotifierPolicy:
    """O que este canal quer receber."""

    post_every_lap: bool = False
    """Toda volta, e não só as melhores. Trinta voltas viram trinta mensagens."""

    post_session_report: bool = True
    """O relatório do engenheiro no fim da sessão — a mensagem mais valiosa."""

    post_failures: bool = True
    """Falha ao gravar. Sempre: o piloto precisa saber que perdeu a volta."""


class Notifier:
    """Traduz eventos do núcleo em mensagens, segundo a política."""

    def __init__(
        self,
        sink: MessageSink,
        *,
        policy: NotifierPolicy | None = None,
        engineer: Any | None = None,
        defer: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._sink = sink
        self._policy = policy or NotifierPolicy()
        self._engineer = engineer
        self._pool: ThreadPoolExecutor | None = None

        if defer is not None:
            self._defer = defer
        else:
            # Um trabalhador só: numa máquina de 8 GB o modelo já ocupa quase
            # tudo, e duas inferências ao mesmo tempo disputam memória que não
            # existe. O `RaceEngineer` serializa por dentro, mas não faz sentido
            # empilhar tarefas esperando aqui.
            self._pool = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="gt7discord"
            )
            self._defer = self._submit

        self._track = ""
        self._car = ""
        self._lap_count = 0
        self._best_ms: int | None = None

    def _submit(self, work: Callable[[], None]) -> None:
        """Enfileira no pool descartando o `Future`.

        Ninguém espera o resultado: o relatório chega quando chegar, e o
        `Future` só existiria para ser ignorado.
        """
        if self._pool is not None:
            self._pool.submit(work)

    # ------------------------------------------------------------------
    # Estado observado
    # ------------------------------------------------------------------

    @property
    def track(self) -> str:
        return self._track

    @property
    def car(self) -> str:
        return self._car

    @property
    def lap_count(self) -> int:
        return self._lap_count

    @property
    def best_ms(self) -> int | None:
        return self._best_ms

    # ------------------------------------------------------------------
    # Assinaturas
    # ------------------------------------------------------------------

    def register(self, bus: Any) -> None:
        """Assina os eventos que interessam. `RaceEventDetected` fica de fora."""
        bus.subscribe(SessionStarted, self.on_session_started)
        bus.subscribe(SessionEnded, self.on_session_ended)
        bus.subscribe(LapSaved, self.on_lap_saved)
        bus.subscribe(LapSaveFailed, self.on_lap_save_failed)

    def on_session_started(self, event: SessionStarted) -> None:
        self._track = event.track_name or ""
        self._car = event.car_name or ""
        self._lap_count = 0
        self._best_ms = None
        self._send(f"**Sessão iniciada** — {self._track or 'pista não informada'}")

    def on_lap_saved(self, event: LapSaved) -> None:
        self._lap_count += 1
        lap_ms = event.lap.lap_time_ms
        previous_best = self._best_ms
        if event.is_best or previous_best is None or lap_ms < previous_best:
            self._best_ms = lap_ms

        if not event.is_best and not self._policy.post_every_lap:
            return

        self._send(
            formatting.lap_saved(
                event.lap, is_best=event.is_best, best_ms=previous_best
            )
        )

    def on_lap_save_failed(self, event: LapSaveFailed) -> None:
        if not self._policy.post_failures:
            return
        # O tempo entra na mensagem porque saber **qual** volta se perdeu é
        # metade da informação: se foi a melhor da sessão, o piloto pode querer
        # repetir o esforço enquanto o ritmo ainda está na mão.
        perdida = formatting.lap_time(event.lap_time_ms)
        self._send(f"⚠️ **Volta {perdida} não foi gravada:** {event.message}")

    def on_session_ended(self, event: SessionEnded) -> None:
        self._send(
            formatting.session_summary(
                track=self._track, lap_count=event.lap_count, best_ms=self._best_ms
            )
        )
        if self._policy.post_session_report and self._engineer is not None:
            self._defer(self._send_session_report)

    # ------------------------------------------------------------------

    def _send_session_report(self) -> None:
        """Roda **fora** da thread de captura. Ver o cabeçalho do módulo."""
        engineer = self._engineer
        if engineer is None:
            return
        try:
            report = engineer.session_report(None, track=self._track, car=self._car)
        except Exception:  # pragma: no cover - o engenheiro já degrada sozinho
            _log.exception("falha ao montar o relatório de sessão para o Discord")
            return

        text = formatting.advice(report, title="Relatório de sessão")
        if text:
            self._send(text)

    def _send(self, text: str) -> None:
        """Enviar nunca pode derrubar a captura.

        Este método é chamado de dentro de um retorno do barramento, na thread
        que está gravando telemetria. Uma exceção de rede subindo daqui mataria
        a gravação da sessão por causa de uma mensagem que ninguém leu ainda.
        """
        if not text:
            return
        try:
            self._sink.send(text)
        except Exception as exc:
            _log.warning("falha ao enviar mensagem ao Discord: %s", exc)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False)
            self._pool = None
