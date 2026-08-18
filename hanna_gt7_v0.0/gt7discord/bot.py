"""
O cliente do Discord — a única parte que conhece `discord.py`.

Duas fronteiras se cruzam aqui, e são de naturezas diferentes.

**Thread → asyncio.** O núcleo é síncrono e multithread: o barramento entrega
eventos na thread de captura. A `discord.py` é asyncio e roda um laço próprio.
Tocar objetos do laço de fora dele é o mesmo tipo de erro que tocar widgets Qt
de outra thread, e produz falhas igualmente difíceis de reproduzir. A travessia
acontece num lugar só, com `run_coroutine_threadsafe` — é o análogo exato do
`QtEventBusAdapter`, na outra direção.

**asyncio → bloqueante.** Um comando pode consultar o modelo, o que leva
segundos. Chamado direto de dentro do laço, congela o bot inteiro: ele para de
responder a qualquer coisa, inclusive a heartbeats, e o Discord derruba a
conexão. Comandos rodam em executor.

Custo de hospedagem: **nenhum.** O bot assina o barramento no mesmo processo do
programa, como a auditoria previu no §12. Não há servidor para manter, e a
contrapartida é que ele só existe enquanto o programa estiver aberto — o que
para telemetria é irrelevante, já que os dados só existem enquanto se pilota.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from collections.abc import Callable
from typing import Any

from gt7core.config.settings import DiscordConfig
from gt7core.observability.logging import get_logger

from .commands import Command, Context, discover

_log = get_logger(__name__)

# Mensagens guardadas enquanto o bot ainda não conectou. Pequeno de propósito:
# a fila existe para não perder o "sessão iniciada" disparado durante a conexão,
# não para virar histórico. O que envelhece demais deixou de ser notícia.
PENDING_LIMIT = 20


class DiscordUnavailable(RuntimeError):
    """O bot não pôde subir. **Nunca deve derrubar a captura.**"""


class DiscordSink:
    """Envia para um canal, atravessando para o laço do asyncio.

    Antes de haver canal, guarda. Depois de haver, esvazia a fila na ordem.
    """

    def __init__(self, limit: int = PENDING_LIMIT) -> None:
        self._pending: deque[str] = deque(maxlen=limit)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._channel: Any | None = None
        self._lock = threading.Lock()

    def attach(self, loop: asyncio.AbstractEventLoop, channel: Any) -> None:
        with self._lock:
            self._loop = loop
            self._channel = channel
            pending, self._pending = list(self._pending), deque(
                maxlen=self._pending.maxlen
            )
        for text in pending:
            self.send(text)

    def detach(self) -> None:
        with self._lock:
            self._loop = None
            self._channel = None

    def send(self, text: str) -> None:
        """Chamado da thread de captura. Nunca bloqueia, nunca levanta."""
        with self._lock:
            loop, channel = self._loop, self._channel
            if loop is None or channel is None:
                self._pending.append(text)
                return

        try:
            asyncio.run_coroutine_threadsafe(channel.send(text), loop)
        except Exception as exc:  # pragma: no cover - depende da rede
            _log.warning("não foi possível enfileirar mensagem no Discord: %s", exc)

    @property
    def is_connected(self) -> bool:
        return self._channel is not None

    @property
    def pending_count(self) -> int:
        return len(self._pending)


def _matches(actual: str, wanted: str) -> bool:
    """Compara nomes de forma tolerante ao que a pessoa realmente digita.

    Ela copia o nome da barra lateral do Discord, e vem com `#` na frente ou
    maiúscula trocada. Exigir a forma exata transformaria um acerto em erro
    silencioso — o bot ficaria mudo e nada diria por quê.
    """
    return actual.strip().lower() == wanted.strip().lstrip("#").lower()


def select_channel(
    guilds: Any, *, guild_name: str = "", channel_name: str = ""
) -> Any | None:
    """Escolhe onde o bot vai escrever.

    Substitui o comportamento anterior, que era pegar o **primeiro** canal de
    texto gravável em **qualquer** servidor. Isso funcionava por acidente com um
    servidor e um canal, e mandava o debrief para um lugar arbitrário em
    qualquer outra configuração — num servidor com `#regras` antes de
    `#telemetria`, o relatório da sessão ia parar nas regras.

    A decisão que mais importa aqui: **nome pedido que não existe devolve
    `None`, não um canal qualquer.** Cair no primeiro gravável seria "prestativo"
    e reproduziria exatamente o defeito — a pessoa configurou `#telemetria`,
    errou uma letra, e o bot passa a publicar num canal que ela não escolheu,
    sem nada na tela indicando isso. Ficar em silêncio com um aviso no log é
    recuperável; publicar no lugar errado, não.

    Sem nome pedido, mantém o comportamento antigo — que é razoável quando o bot
    está num servidor só, e é o padrão de quem nunca abriu esta configuração.

    Recebe os servidores por parâmetro (tipados como `Any`) em vez de ler do
    cliente: é o que permite verificar a política inteira sem `discord.py`
    instalada e sem rede.
    """
    for guild in guilds:
        if guild_name and not _matches(getattr(guild, "name", ""), guild_name):
            continue
        for channel in guild.text_channels:
            if channel_name and not _matches(getattr(channel, "name", ""), channel_name):
                continue
            if channel.permissions_for(guild.me).send_messages:
                return channel
    return None


class DiscordBot:
    """Sobe a `discord.py` numa thread própria e despacha os comandos."""

    def __init__(
        self,
        config: DiscordConfig,
        context_factory: Callable[[], Context],
        *,
        commands: dict[str, Command] | None = None,
    ) -> None:
        self._config = config
        self._context_factory = context_factory
        self._commands = commands if commands is not None else discover()
        self._sink = DiscordSink()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: Any | None = None

    @property
    def sink(self) -> DiscordSink:
        return self._sink

    @property
    def commands(self) -> dict[str, Command]:
        return self._commands

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------

    def handle_message(self, content: str) -> str | None:
        """Traduz uma mensagem em resposta. **Sem asyncio e sem `discord.py`.**

        Está separado do cliente de propósito: é aqui que mora o despacho, o
        prefixo e o tratamento de comando desconhecido, e é isto que os testes
        exercitam. O que sobra do outro lado é receber e enviar texto.
        """
        prefix = self._config.command_prefix
        text = content.strip()
        if not text.startswith(prefix):
            return None

        parts = text[len(prefix) :].split()
        if not parts:
            return self._commands["help"].run(self._context_factory(), [])

        name, args = parts[0].lower(), parts[1:]
        command = self._commands.get(name)
        if command is None:
            known = ", ".join(f"`{n}`" for n in sorted(self._commands))
            return f"Comando `{name}` não existe. Disponíveis: {known}"

        try:
            return command.run(self._context_factory(), args)
        except Exception:
            # Um comando com defeito responde "deu erro" em vez de derrubar o
            # bot — e o traceback vai para o log de quem opera, não para o chat.
            _log.exception("comando %s falhou", name)
            return f"O comando `{name}` falhou. Veja o log da aplicação."

    # ------------------------------------------------------------------

    def start(self) -> None:
        """Sobe o bot numa thread. Não bloqueia; falhar não derruba nada."""
        if self.is_running:
            return
        if not self._config.token:
            raise DiscordUnavailable("sem token do Discord configurado")

        try:
            import discord
        except ImportError as exc:  # pragma: no cover - depende do ambiente
            raise DiscordUnavailable(
                "pacote 'discord.py' não instalado — pip3 install discord.py"
            ) from exc

        self._thread = threading.Thread(
            target=self._run, args=(discord,), name="gt7discord", daemon=True
        )
        self._thread.start()

    def _run(self, discord: Any) -> None:  # pragma: no cover - exige rede
        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        self._client = client

        # A `discord.py` não publica stubs para `@client.event`, e no modo
        # strict um decorador sem tipo contamina a função inteira. O silêncio é
        # cirúrgico: vale para as duas corrotinas do cliente, e não afrouxa a
        # verificação de nada mais neste pacote.
        @client.event  # type: ignore[untyped-decorator]
        async def on_ready() -> None:
            loop = asyncio.get_running_loop()
            self._loop = loop
            channel = select_channel(
                client.guilds,
                guild_name=self._config.guild,
                channel_name=self._config.channel,
            )
            if channel is not None:
                self._sink.attach(loop, channel)
            else:
                _log.warning(
                    "nenhum canal do Discord corresponde à configuração",
                    extra={
                        "guild": self._config.guild or "(qualquer)",
                        "channel": self._config.channel or "(o primeiro gravável)",
                    },
                )
            _log.info("bot do Discord conectado", extra={"user": str(client.user)})

        @client.event  # type: ignore[untyped-decorator]
        async def on_message(message: Any) -> None:
            if message.author == client.user:
                return
            # O despacho é síncrono e pode consultar o modelo. Rodá-lo no laço
            # congelaria o bot inteiro, inclusive os heartbeats — e o Discord
            # derruba conexões que param de responder.
            reply = await asyncio.get_running_loop().run_in_executor(
                None, self.handle_message, message.content
            )
            if reply:
                await message.channel.send(reply)

        try:
            client.run(self._config.token.reveal(), log_handler=None)
        except Exception:
            _log.exception("o bot do Discord parou")
        finally:
            self._sink.detach()

    def stop(self) -> None:
        """Fecha a conexão. Idempotente."""
        client, loop = self._client, self._loop
        self._sink.detach()
        if client is not None and loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(client.close(), loop).result(timeout=5)
            except Exception:  # pragma: no cover - fechar pode correr com a queda
                _log.debug("falha ao fechar o cliente do Discord", exc_info=True)
        self._client = None
        self._loop = None
        self._thread = None
