"""
O que o engenheiro devolve.

Estes tipos existem para que o resto da aplicação nunca precise olhar dentro de
uma resposta de modelo. A interface recebe `Advice` — com título, texto e uma
lista de ações — e não sabe se aquilo veio da API, do cache ou da análise local
da Fase 4. Essa indiferença é o que permite a IA ser opcional de verdade.

O campo que carrega a decisão de projeto é `source`. Um conselho gerado
localmente **não é um erro**: é a resposta que a análise numérica já sabia dar
sozinha. Marcá-lo é honestidade com o piloto (ele vê de onde veio) e é o que
permite testar os dois caminhos separadamente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .client import AIUsage


class AdviceLevel(StrEnum):
    """Os três níveis do §7, que diferem em latência, custo e formato."""

    QUICK = "quick"
    """Em pilotagem. Uma frase, modelo rápido, dita em voz alta."""

    DEBRIEF = "debrief"
    """Depois da volta. Estruturado, com ações concretas."""

    SESSION = "session"
    """Fim de sessão. Texto corrido, olhando o conjunto das voltas."""


class AdviceSource(StrEnum):
    AI = "ia"
    LOCAL = "local"
    """Montado a partir da análise da Fase 4, sem chamar a API."""


@dataclass(frozen=True, slots=True)
class Action:
    """Uma coisa para fazer na próxima volta.

    Deliberadamente estreito. "Melhore a saída da curva 4" não cabe aqui sem
    responder *onde* e *o quê* — e é por isso que o esquema de saída
    estruturada exige os dois campos.
    """

    where: str
    """`"Curva 3"`, `"Reta dos boxes"` — o trecho, no vocabulário da análise."""

    instruction: str
    """A correção, no imperativo e verificável na volta seguinte."""

    gain_ms: float | None = None
    """Ganho estimado, quando a análise mediu a perda daquele trecho."""

    def describe(self) -> str:
        if self.gain_ms is None:
            return f"{self.where}: {self.instruction}"
        return f"{self.where}: {self.instruction} (~{self.gain_ms / 1000:.2f} s)"


@dataclass(frozen=True, slots=True)
class Advice:
    """Um conselho do engenheiro, pronto para exibir ou falar."""

    level: AdviceLevel
    headline: str
    """Uma linha. É o que vai no rádio e no cabeçalho do cartão."""

    detail: str = ""
    """O raciocínio, quando há espaço para mostrá-lo."""

    actions: list[Action] = field(default_factory=list)
    source: AdviceSource = AdviceSource.AI
    model: str = ""
    usage: AIUsage = field(default_factory=AIUsage)

    @property
    def is_local(self) -> bool:
        return self.source is AdviceSource.LOCAL

    @property
    def is_empty(self) -> bool:
        return not self.headline.strip() and not self.detail.strip()

    def speech(self) -> str:
        """O que a síntese de voz deve dizer.

        Só o título: no meio de uma curva, ninguém ouve um parágrafo. O detalhe
        fica na tela para depois.
        """
        return self.headline.strip()

    def full_text(self) -> str:
        parts = [self.headline.strip()]
        if self.detail.strip():
            parts.append(self.detail.strip())
        if self.actions:
            parts.append("\n".join(f"• {a.describe()}" for a in self.actions))
        return "\n\n".join(part for part in parts if part)
