"""
Quem fala.

Terceira vez que este padrão aparece no projeto, e de propósito: `AIClient` para
o modelo, `MessageSink` para o Discord, `Speaker` para a voz. Um protocolo de um
método na fronteira do mundo externo é o que mantém a decisão — *o que falar e
quando calar* — em Python puro, testável sem áudio, sem microfone e sem rede.

Aqui a fronteira compra algo a mais: **não há como verificar áudio num teste
automatizado**. Sem o protocolo, a política de fala seria verificável só ouvindo,
o que na prática significa não verificada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class Speaker(Protocol):
    """Um sintetizador. Implementado pelo sistema e pelo de teste."""

    def say(self, text: str) -> None:
        """Fala. **Não bloqueia** quem chama."""
        ...

    def stop(self) -> None:
        """Interrompe a fala em andamento, se houver."""
        ...


@dataclass
class RecordingSpeaker:
    """Guarda o que seria falado. É o `RecordingSink` desta camada."""

    spoken: list[str] = field(default_factory=list)
    stops: int = 0

    def say(self, text: str) -> None:
        self.spoken.append(text)

    def stop(self) -> None:
        self.stops += 1

    @property
    def last(self) -> str:
        return self.spoken[-1] if self.spoken else ""


class NullSpeaker:
    """Não fala nada. É o que se usa com a voz desligada."""

    def say(self, text: str) -> None:
        return None

    def stop(self) -> None:
        return None
