"""
O engenheiro rodando fora da thread da interface.

Esta é a mitigação do **R2** da auditoria — *"thread única satura ao adicionar
IA/Discord: UI trava durante corrida"* —, o único risco do documento de Fase 0
que continuava aberto. Ele estava aberto porque nada ainda chamava a IA; a
partir do momento em que uma página chama, ele deixa de ser hipótese.

O número que torna isso concreto: um modelo de 4B em CPU leva de 5 a 15 segundos
para escrever um debrief. Chamado direto de um `clicked`, isso é a janela
congelada — sem repintar, sem responder — durante uma volta.

Três decisões que valem explicação
----------------------------------
**`QThreadPool` e não `QThread`.** O trabalho aqui é tarefa isolada e sem
estado: recebe dados, devolve um `Advice`. `QRunnable` descreve exatamente isso,
e evita o erro clássico de herdar de `QThread` e sobrescrever `run()` — que põe
o objeto numa thread e seus slots em outra.

**Uma chamada por vez, com a última ganhando.** Não é elegância: o alvo é uma
máquina de 8 GB rodando um modelo que já ocupa quase tudo o que sobra. Dois
prompts simultâneos disputariam memória que não existe. Quando chega um pedido
com outro em andamento, o novo fica **pendente** e substitui qualquer pendente
anterior — porque se o piloto trocou de volta duas vezes, o que ele quer é a
última, não uma fila de três.

**Contador de geração contra resultado obsoleto.** O piloto pede o debrief da
volta 5, muda para a volta 3, e a resposta da 5 chega depois. Sem o contador,
ela sobrescreveria a tela com a análise da volta errada — e nada no texto
denunciaria isso. Cada pedido carrega um número; resposta de número vencido é
descartada.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from gt7core.analytics.driver import DriverProfile
from gt7core.analytics.timeloss import TimeLossReport
from gt7core.observability.logging import get_logger

_log = get_logger(__name__)


class _Task(QRunnable):
    """Executa uma função no pool e devolve o resultado por sinal.

    O sinal vive num `QObject` separado porque `QRunnable` não é um — e é o
    sinal que faz a travessia de volta para a thread da interface, com o Qt
    escolhendo conexão enfileirada sozinho.
    """

    def __init__(
        self,
        work: Callable[[], Any],
        signals: _TaskSignals,
        generation: int,
    ) -> None:
        super().__init__()
        self._work = work
        self._signals = signals
        self._generation = generation

    def run(self) -> None:
        try:
            result = self._work()
        except Exception as exc:  # pragma: no cover - o engenheiro já degrada
            # O `RaceEngineer` não levanta: ele cai no conselho da Fase 4. Se
            # algo chegar aqui é defeito de programação, e engolir na thread de
            # fundo transformaria isso num silêncio inexplicável.
            _log.exception("falha inesperada ao consultar o engenheiro")
            self._signals.failed.emit(self._generation, str(exc))
            return
        self._signals.done.emit(self._generation, result)


class _TaskSignals(QObject):
    done = Signal(int, object)
    failed = Signal(int, str)


class EngineerService(QObject):
    """Fachada da interface para o `RaceEngineer`.

    As páginas falam com este objeto e nunca com o engenheiro direto. Elas
    pedem, seguem repintando, e recebem o conselho por sinal quando ele existir.
    """

    #: Emitido ao começar a pensar. Carrega o nível pedido, para a tela dizer
    #: *o quê* está sendo preparado em vez de um "carregando" genérico.
    started = Signal(str)

    #: Emitido com o `Advice` pronto. Já na thread da interface.
    ready = Signal(object)

    #: Só para defeito de programação — indisponibilidade normal vira conselho
    #: local e chega por `ready`.
    failed = Signal(str)

    def __init__(self, engineer: Any | None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._engineer = engineer
        self._pool = QThreadPool(self)
        # Um só: ver a nota sobre memória no cabeçalho do módulo.
        self._pool.setMaxThreadCount(1)

        self._signals = _TaskSignals(self)
        self._signals.done.connect(self._on_done)
        self._signals.failed.connect(self._on_failed)

        self._generation = 0
        self._running = False
        self._pending: tuple[Callable[[], Any], str] | None = None

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------

    @property
    def is_available(self) -> bool:
        """Se há engenheiro. Falso só quando o pacote `gt7ai` não está instalado.

        Não confundir com "a IA está no ar": mesmo sem modelo algum respondendo,
        o engenheiro devolve o conselho da análise da Fase 4.
        """
        return self._engineer is not None

    @property
    def is_busy(self) -> bool:
        return self._running

    @property
    def engineer(self) -> Any | None:
        return self._engineer

    # ------------------------------------------------------------------
    # Pedidos
    # ------------------------------------------------------------------

    def request_debrief(
        self,
        report: TimeLossReport,
        *,
        track: str,
        car: str = "",
        lap_time_ms: int = 0,
        reference_time_ms: int | None = None,
        corners: list[Any] | None = None,
        profile: DriverProfile | None = None,
    ) -> None:
        engineer = self._engineer
        if engineer is None:
            return

        self._submit(
            lambda: engineer.debrief(
                report,
                track=track,
                car=car,
                lap_time_ms=lap_time_ms,
                reference_time_ms=reference_time_ms,
                corners=corners,
                profile=profile,
            ),
            "debrief",
        )

    def request_session_report(
        self,
        profile: DriverProfile | None,
        *,
        track: str,
        car: str = "",
        lap_times_ms: list[int] | None = None,
    ) -> None:
        engineer = self._engineer
        if engineer is None:
            return

        self._submit(
            lambda: engineer.session_report(
                profile, track=track, car=car, lap_times_ms=lap_times_ms
            ),
            "session",
        )

    def request_quick_note(self, situation: str, *, fallback: str = "") -> None:
        engineer = self._engineer
        if engineer is None:
            return

        self._submit(
            lambda: engineer.quick_note(situation, fallback=fallback), "quick"
        )

    def new_lap(self) -> None:
        """Zera a cota de notas de rádio da volta.

        Precisa ser chamado por alguém — sem isso a cadência nunca reseta e o
        rádio emudece depois das primeiras notas da sessão.
        """
        if self._engineer is not None:
            self._engineer.new_lap()

    def new_session(self) -> None:
        if self._engineer is not None:
            self._engineer.new_session()

    def cancel_pending(self) -> None:
        """Invalida o que está em voo. A tarefa termina, o resultado é jogado fora.

        Não há como interromper uma inferência já iniciada — o que se pode
        garantir é que ela não apareça na tela depois de deixar de ser
        relevante.
        """
        self._generation += 1
        self._pending = None

    # ------------------------------------------------------------------
    # Mecânica
    # ------------------------------------------------------------------

    def _submit(self, work: Callable[[], Any], level: str) -> None:
        if self._running:
            # Substitui o pendente: dois pedidos enfileirados significam que o
            # primeiro já não interessa.
            self._pending = (work, level)
            return

        self._generation += 1
        self._running = True
        self.started.emit(level)
        self._pool.start(_Task(work, self._signals, self._generation))

    def _on_done(self, generation: int, result: object) -> None:
        if generation != self._generation:
            _log.debug("conselho obsoleto descartado (geração %d)", generation)
            self._drain()
            return

        self._running = False
        if result is not None:
            self.ready.emit(result)
        self._drain()

    def _on_failed(self, generation: int, message: str) -> None:
        self._running = False
        if generation == self._generation:
            self.failed.emit(message)
        self._drain()

    def _drain(self) -> None:
        self._running = False
        pending = self._pending
        if pending is None:
            return
        self._pending = None
        self._submit(*pending)

    def shutdown(self) -> None:
        """Espera as tarefas em voo antes de a aplicação fechar.

        Sem isto, fechar a janela no meio de uma inferência deixa uma thread
        tocando objetos que o Qt está destruindo — o mesmo modo de falha que o
        adaptador de barramento existe para evitar, só que na volta.
        """
        self.cancel_pending()
        self._pool.waitForDone(5000)
