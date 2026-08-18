"""
Ponte entre o barramento do núcleo e a thread da interface.

Esta é a peça que justifica ter tirado o Qt do núcleo. A garantia que o
`EventBus` original dava — "seu handler roda na thread da UI" — não foi perdida
na extração: ela mudou de lugar. Aqui, e só aqui, o Qt aparece.

Por que a garantia importa
--------------------------
A captura roda numa thread própria. Se um widget fosse tocado a partir dela, o
resultado seria corrupção de interface e crash intermitente — o modo de falha
clássico do Qt, e a razão pela qual o barramento original usava `Signal` em vez
de chamar os handlers direto.

Como funciona
-------------
O adaptador assina **um** handler no barramento puro, por tipo de evento, e
reemite via `Signal`. Quando emissor e receptor estão em threads diferentes, o
Qt usa conexão enfileirada e entrega na thread do receptor. Os assinantes da
interface registram-se no adaptador, não no barramento::

    bus = EventBus()                       # núcleo, sem Qt
    adapter = QtEventBusAdapter(bus)       # única peça que conhece Qt
    adapter.subscribe(TelemetryReceived, self._on_telemetry)   # roda na UI

O núcleo continua sem saber que existe interface: se ninguém instalar o
adaptador, o barramento funciona igual e entrega na thread de quem publicou.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from PySide6.QtCore import QObject, Signal

from gt7core.events.bus import EventBus

E = TypeVar("E")


class QtEventBusAdapter(QObject):
    """Reemite eventos do núcleo na thread da interface.

    Um único `Signal(object)` carrega qualquer evento: sinais Qt são declarados
    em tempo de classe e não dá para criar um por tipo em runtime. O despacho
    por tipo acontece do lado de cá, depois da travessia de thread.
    """

    event_received = Signal(object)

    def __init__(self, bus: EventBus, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bus = bus
        self._handlers: dict[type, list[Callable[[Any], None]]] = {}
        self._bridged: set[type] = set()

        self.event_received.connect(self._dispatch)

    def subscribe(self, event_type: type[E], handler: Callable[[E], None]) -> None:
        """Registra um handler que rodará **na thread da interface**."""
        handlers = self._handlers.setdefault(event_type, [])
        if handler not in handlers:
            handlers.append(handler)

        # Uma única ponte por tipo de evento, criada sob demanda. Assinar o
        # barramento uma vez por handler faria o evento atravessar a fronteira
        # de thread N vezes em vez de uma.
        if event_type not in self._bridged:
            self._bridged.add(event_type)
            self._bus.subscribe(event_type, self._relay)

    def unsubscribe(self, event_type: type[E], handler: Callable[[E], None]) -> None:
        handlers = self._handlers.get(event_type)
        if handlers and handler in handlers:
            handlers.remove(handler)

    def close(self) -> None:
        """Desliga o adaptador do barramento.

        Chamado ao fechar a janela. Sem isto, o barramento continuaria
        segurando referência ao adaptador e emitindo para um QObject destruído
        — que em Qt é acesso a ponteiro morto, não uma exceção Python.
        """
        for event_type in self._bridged:
            self._bus.unsubscribe(event_type, self._relay)
        self._bridged.clear()
        self._handlers.clear()

    # ---------- travessia de thread ----------

    def _relay(self, event: Any) -> None:
        """Roda na thread que publicou (rede, replay, worker). Só reemite."""
        self.event_received.emit(event)

    def _dispatch(self, event: Any) -> None:
        """Roda na thread da interface, graças à conexão enfileirada do Qt."""
        for handler in list(self._handlers.get(type(event), ())):
            try:
                handler(event)
            except Exception:
                # Mesma regra do núcleo: um widget quebrado não derruba os
                # outros nem mata a captura.
                from gt7core.observability.logging import get_logger

                get_logger(__name__).exception(
                    "handler da interface falhou",
                    extra={"event_type": type(event).__name__},
                )
