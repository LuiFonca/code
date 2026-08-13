"""
Barramento de eventos publish/subscribe.
"""

from collections import defaultdict
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

Handler = Callable[[Any], None]


class EventBus(QObject):
    """Desacopla quem produz um fato de quem reage a ele.

    Por que sobre `Signal` e não callbacks Python puros
    ---------------------------------------------------
    O listener de telemetria roda numa QThread própria. Se `publish` chamasse
    os handlers diretamente, o código de UI executaria **na thread de rede** —
    que é exatamente como se produz corrupção de widget e crash intermitente
    em Qt. Passando por um `Signal`, o Qt usa conexão enfileirada quando
    emissor e receptor estão em threads diferentes, e cada assinante roda na
    thread à qual pertence.

    Por que um único sinal genérico
    -------------------------------
    Sinais Qt são declarados em tempo de classe; não dá para criar um por tipo
    de evento em runtime. Então existe um `event_published = Signal(object)`
    que carrega qualquer evento, e o despacho por tipo é feito aqui dentro.

    O singleton é conveniência para o composition root. As demais classes
    **recebem o bus pelo construtor** — nunca chamam `EventBus.instance()`
    lá de dentro, senão a injeção de dependência vira fachada e volta o
    estado global que a refatoração veio eliminar.
    """

    event_published = Signal(object)

    _instance: "EventBus | None" = None

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._handlers: dict[type, list[Handler]] = defaultdict(list)
        # Conexão interna: tudo que é publicado passa pelo sinal antes de
        # chegar nos handlers. É esse desvio que dá a troca de thread.
        self.event_published.connect(self._dispatch)

    @classmethod
    def instance(cls) -> "EventBus":
        """Instância compartilhada, para uso do composition root."""
        if cls._instance is None:
            cls._instance = EventBus()
        return cls._instance

    def subscribe(self, event_type: type, handler: Handler) -> None:
        """Registra `handler` para eventos de `event_type`.

        Assinar o mesmo handler duas vezes é ignorado — sem isso, uma aba
        reconstruída acabaria processando cada evento em duplicidade."""
        handlers = self._handlers[event_type]
        if handler not in handlers:
            handlers.append(handler)

    def unsubscribe(self, event_type: type, handler: Handler) -> None:
        """Remove o registro. Silencioso se não estava inscrito."""
        handlers = self._handlers.get(event_type)
        if handlers and handler in handlers:
            handlers.remove(handler)

    def publish(self, event: Any) -> None:
        """Publica um evento. Seguro para chamar de qualquer thread."""
        self.event_published.emit(event)

    def clear(self) -> None:
        """Descarta todas as inscrições — usado ao desmontar e em testes."""
        self._handlers.clear()

    def _dispatch(self, event: Any) -> None:
        # `type(event)` e não isinstance: despacho por tipo exato mantém o
        # custo constante no caminho quente (~60 eventos/s de telemetria).
        for handler in list(self._handlers.get(type(event), ())):
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001
                # Um assinante quebrado não pode derrubar os outros nem matar
                # a captura. O erro aparece no console em vez de virar um
                # travamento silencioso do fluxo de telemetria.
                print(
                    f"[EventBus] handler {getattr(handler, '__qualname__', handler)!r} "
                    f"falhou em {type(event).__name__}: {exc}"
                )
