"""`help` — a lista de comandos, montada por descoberta.

Não existe lista escrita à mão aqui: o texto sai do mesmo `discover()` que o bot
usa para despachar. Um comando novo aparece sozinho, e nunca há como o `help`
divergir do que de fato funciona.
"""

from __future__ import annotations

from . import Command, Context, discover


def run(_context: Context, _args: list[str]) -> str:
    commands = discover()
    lines = ["**Comandos disponíveis**"]
    for name in sorted(commands):
        lines.append(f"`{name}` — {commands[name].help}")
    return "\n".join(lines)


COMMAND = Command(name="help", help="Esta lista", run=run)
