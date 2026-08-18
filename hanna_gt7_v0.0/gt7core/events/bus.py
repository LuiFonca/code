"""
Barramento de eventos publish/subscribe — sem Qt, sem I/O.

Esta é a peça que destrava a arquitetura. A versão anterior
(`src/application/events/event_bus.py`) era um `QObject` e usava `Signal` para
atravessar threads: correto para a UI, mas amarrava o núcleo inteiro ao Qt.
Um bot do Discord, um worker de IA ou um teste unitário não conseguiam usar o
domínio sem um event loop gráfico.

Aqui o contrato é o mesmo (`publish`/`subscribe`/`unsubscribe`) e o
comportamento de thread muda de dono:

- `publish()` chama os handlers **na thread que publicou**;
- quem precisa de outra thread instala um adaptador.

O `QtEventBusAdapter` (em `gt7app`) assina uma única vez e reemite via `Signal`,
preservando exatamente a garantia de thread da UI que existia antes. A diferença
é que agora essa responsabilidade mora numa peça de UI, não no núcleo.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from typing import Any, TypeVar

_log = logging.getLogger(__name__)

E = TypeVar("E")
Handler = Callable[[Any], None]


class EventBus:
    """Desacopla quem produz um fato de quem reage a ele.

    Thread-safe: `publish` pode ser chamado da thread de rede enquanto outra
    thread assina. O lock protege apenas o registro de handlers — os handlers
    em si rodam **fora** do lock, senão um handler lento bloquearia todo o
    barramento e um que republicasse causaria deadlock.
    """

    def __init__(self) -> None:
        self._handlers: dict[type, list[Handler]] = defaultdict(list)
        self._lock = threading.RLock()

    def subscribe(self, event_type: type[E], handler: Callable[[E], None]) -> None:
        """Registra `handler` para eventos de `event_type`.

        Assinar o mesmo handler duas vezes é ignorado — sem isso, uma aba
        reconstruída processaria cada evento em duplicidade.
        """
        with self._lock:
            handlers = self._handlers[event_type]
            if handler not in handlers:
                handlers.append(handler)

    def unsubscribe(self, event_type: type[E], handler: Callable[[E], None]) -> None:
        """Remove o registro. Silencioso se não estava inscrito."""
        with self._lock:
            handlers = self._handlers.get(event_type)
            if handlers and handler in handlers:
                handlers.remove(handler)

    def publish(self, event: Any) -> None:
        """Publica um evento. Seguro para chamar de qualquer thread.

        Despacho por tipo exato (`type(event)`, não `isinstance`): mantém o
        custo constante no caminho quente, que roda a ~60 eventos/s.
        """
        with self._lock:
            handlers = list(self._handlers.get(type(event), ()))

        for handler in handlers:
            try:
                handler(event)
            except Exception:
                # Um assinante quebrado não pode derrubar os outros nem matar a
                # captura. Vai para o log estruturado com stack trace — a versão
                # anterior usava print() e o erro sumia no terminal.
                _log.exception(
                    "handler falhou",
                    extra={
                        "handler": getattr(handler, "__qualname__", repr(handler)),
                        "event_type": type(event).__name__,
                    },
                )

    def clear(self) -> None:
        """Descarta todas as inscrições — usado ao desmontar e em testes."""
        with self._lock:
            self._handlers.clear()

    def handler_count(self, event_type: type) -> int:
        """Quantos handlers estão inscritos. Existe para os testes verificarem
        que o desmonte realmente desinscreveu."""
        with self._lock:
            return len(self._handlers.get(event_type, ()))
