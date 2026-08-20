"""
Registro de comandos por **descoberta**.

O §23 do briefing pede que adicionar um comando não toque no núcleo. A forma de
garantir isso não é disciplina: é não existir lista nenhuma para atualizar.
Cada módulo deste pacote declara um `COMMAND` no nível do módulo, e `discover()`
varre o diretório. Um arquivo novo aparece sozinho no `help`.

O `Context` é o que um comando pode ver. Deliberadamente estreito — repositórios
para ler, o engenheiro para consultar, e o estado da sessão. Um comando **não**
recebe o barramento nem a fonte de telemetria: nada aqui deve poder parar a
captura, e a forma mais confiável de garantir isso é não entregar o botão.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from gt7core.observability.logging import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Context:
    """O que um comando enxerga da aplicação."""

    laps: Any
    tracks: Any
    session: Any
    engineer: Any | None = None
    catalog: Any | None = None

    @property
    def track_name(self) -> str:
        track = getattr(self.session, "track", None)
        return getattr(track, "name", "") or ""

    @property
    def car_name(self) -> str:
        car = getattr(self.session, "car", None)
        return getattr(car, "name", "") or ""


@dataclass(frozen=True, slots=True)
class Command:
    """Um comando do bot."""

    name: str
    help: str
    run: Callable[[Context, list[str]], str]
    """Recebe o contexto e os argumentos; devolve o texto a postar.

    Síncrono e puro em relação ao Discord — devolver texto em vez de enviar é o
    que torna cada comando testável sem token, sem rede e sem `discord.py`.
    """


def discover() -> dict[str, Command]:
    """Todos os comandos deste pacote, por nome.

    Um módulo que falhe ao importar é registrado no log e **pulado**: um comando
    quebrado não pode impedir os outros de existirem, nem o bot de subir.
    """
    found: dict[str, Command] = {}

    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"{__name__}.{info.name}")
        except Exception:
            _log.exception("comando %s não pôde ser carregado", info.name)
            continue

        command = getattr(module, "COMMAND", None)
        if isinstance(command, Command):
            found[command.name] = command
        else:
            _log.warning("módulo %s não declara COMMAND", info.name)

    return found
