"""
`gt7discord` — o bot que leva o engenheiro para o celular (§12 e §23).

Plugin, não núcleo. `gt7core` não importa nada daqui, e a aplicação funciona com
este pacote ausente, sem token ou sem a `discord.py` instalada.

Custo de hospedagem: **nenhum**. O bot assina o barramento no mesmo processo do
programa — não há servidor para manter. A contrapartida é que ele só existe
enquanto o programa estiver aberto, o que para telemetria não é limitação: os
dados só existem enquanto se pilota.

Ordem de leitura:

1. `sink` — para onde a mensagem vai. É o que torna tudo testável sem rede;
2. `formatting` — do domínio para o texto;
3. `notifier` — a política: o que vira mensagem e, principalmente, o que não vira;
4. `commands/` — um arquivo por comando, descobertos por varredura (§23);
5. `bot` — a única parte que conhece `discord.py`.

Uso típico:

    from gt7discord import build_bot

    bot = build_bot(core)
    if bot is not None:
        bot.start()
"""

from typing import Any

from .bot import DiscordBot, DiscordSink, DiscordUnavailable
from .commands import Command, Context, discover
from .notifier import Notifier, NotifierPolicy
from .sink import MessageSink, NullSink, RecordingSink

__all__ = [
    "Command",
    "Context",
    "DiscordBot",
    "DiscordSink",
    "DiscordUnavailable",
    "MessageSink",
    "Notifier",
    "NotifierPolicy",
    "NullSink",
    "RecordingSink",
    "build_bot",
    "discover",
]


def build_bot(core: Any) -> DiscordBot | None:
    """Monta bot e notificador a partir de um núcleo já pronto.

    Devolve `None` quando o Discord está desligado ou sem token — a mesma
    política do engenheiro: ausente é um modo de operação, não um erro.
    """
    from gt7core.observability.logging import get_logger

    log = get_logger(__name__)
    settings = core.settings
    config = settings.discord

    if not config.enabled or not config.token:
        log.info("Discord desligado — sem token configurado")
        return None

    def context_factory() -> Context:
        return Context(
            laps=core.laps,
            tracks=core.tracks,
            session=core.session_manager.session,
            engineer=getattr(core, "engineer", None),
            catalog=getattr(core, "catalog", None),
        )

    bot = DiscordBot(config, context_factory)
    notifier = Notifier(bot.sink, engineer=getattr(core, "engineer", None))
    notifier.register(core.bus)
    return bot
