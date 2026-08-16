"""
Para onde a mensagem vai.

O bot inteiro fala com este protocolo, nunca com a `discord.py` diretamente —
pelo mesmo motivo que `gt7ai` fala com `AIClient` e não com a SDK da Anthropic:
**um teste que precisa de token, servidor e rede é um teste que ninguém roda.**

Com a fronteira aqui, a parte que importa (o que postar, quando, e o que
suprimir) é Python puro, roda offline e é determinística. O que sobra do lado da
`discord.py` é entregar uma string, que é o pedaço que não tem decisão nenhuma.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class MessageSink(Protocol):
    """Um destino de mensagens. Implementado pelo canal real e pelo de teste."""

    def send(self, text: str) -> None: ...


@dataclass
class RecordingSink:
    """Guarda o que seria enviado. É o `ScriptedClient` deste pacote."""

    messages: list[str] = field(default_factory=list)

    def send(self, text: str) -> None:
        self.messages.append(text)

    @property
    def last(self) -> str:
        return self.messages[-1] if self.messages else ""

    def clear(self) -> None:
        self.messages.clear()


class NullSink:
    """Descarta tudo. Para quando o bot está configurado mas desconectado."""

    def send(self, text: str) -> None:
        return None
