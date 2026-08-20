"""
Logging estruturado — §35 do briefing.

A auditoria registrou como P11 que o único destino de erro no sistema era um
`print()` dentro do `EventBus`: sem níveis, sem arquivo, sem contexto. Um
handler que quebrava durante uma sessão deixava uma linha solta no terminal e
nada mais.

Usa a `logging` da stdlib em vez de trazer `structlog`: o que faz um log ser
estruturado é o campo extra chegar em forma consultável, e o formatter JSON
abaixo resolve isso sem mais uma dependência no núcleo.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

# Atributos que o `logging` já põe em todo record. Tudo que **não** está aqui
# veio do `extra=` de quem chamou e é, portanto, contexto estruturado.
_STANDARD_ATTRS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info",
    "taskName", "thread", "threadName",
})


class JsonFormatter(logging.Formatter):
    """Uma linha JSON por registro, com os campos de `extra` no mesmo nível."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    """Formato legível para desenvolvimento, com o contexto extra ao final."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)-28s %(message)s", "%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_ATTRS and not key.startswith("_")
        }
        if extras:
            rendered = " ".join(f"{k}={v}" for k, v in extras.items())
            base = f"{base}  [{rendered}]"
        return base


def configure_logging(
    level: str = "INFO",
    *,
    json_format: bool = False,
    file_path: Path | None = None,
) -> None:
    """Instala os handlers na raiz. Idempotente — chamar duas vezes não duplica.

    Chamado uma vez no composition root. Nenhum outro módulo configura logging;
    todos apenas fazem `logging.getLogger(__name__)`.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    formatter: logging.Formatter = JsonFormatter() if json_format else ConsoleFormatter()

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    root.addHandler(console)

    if file_path is not None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        # Arquivo sempre em JSON, mesmo com o console legível: é o que vai ser
        # consultado depois de uma sessão, e grep em JSON é analisável.
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
