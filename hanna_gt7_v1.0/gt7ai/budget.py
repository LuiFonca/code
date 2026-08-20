"""
Quanto a IA já custou, e quando ela deve calar a boca.

Dois problemas diferentes moram aqui, e vale separá-los porque só um deles é
sobre dinheiro.

**Custo.** A conta de uma sessão de treino é a soma de chamadas pequenas, e
soma pequena passa despercebida até a fatura. O livro-caixa acumula o uso
declarado por cada resposta e expõe o total; o teto de sessão desliga a IA
quando ele é atingido, em vez de continuar gastando em silêncio.

**Cadência.** A nota em pilotagem dispara por evento — travamento, delta
piorando, alívio na saída. Numa volta ruim isso acontece oito vezes em noventa
segundos. Mesmo de graça seria errado falar oito vezes: o piloto não consegue
aplicar uma correção antes da próxima chegar, e o rádio vira ruído. O intervalo
mínimo entre notas existe por ergonomia; a economia é efeito colateral.

O relógio é injetado (`clock`) para que o teste do intervalo não precise
dormir — um teste que espera 20 segundos de verdade é um teste que alguém
acaba desligando.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .client import AIUsage
from .models import AdviceLevel


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    """Os tetos. Padrões pensados para uma sessão de treino de uma hora."""

    session_usd: float = 1.00
    """Teto de gasto por sessão. Atingido, a IA para até a próxima sessão."""

    quick_interval_s: float = 25.0
    """Silêncio mínimo entre duas notas de rádio."""

    quick_per_lap: int = 2
    """Notas por volta, no máximo. Mais que isso ninguém processa dirigindo."""


@dataclass(slots=True)
class BudgetLedger:
    """O que já foi gasto, por modelo e no total."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    total_usd: float = 0.0
    by_model: dict[str, float] = field(default_factory=dict)

    def record(self, usage: AIUsage) -> None:
        self.calls += 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cache_read_tokens += usage.cache_read_tokens
        cost = usage.cost_usd
        self.total_usd += cost
        if usage.model:
            self.by_model[usage.model] = self.by_model.get(usage.model, 0.0) + cost

    @property
    def cache_hit_ratio(self) -> float:
        """Fração da entrada que veio do cache.

        Vale vigiar: se este número desaba, alguém pôs algo variável no prompt
        de sistema e o prefixo parou de casar. O sintoma é a conta subindo sem
        que nada tenha mudado no uso.
        """
        total_input = self.input_tokens + self.cache_read_tokens
        if total_input <= 0:
            return 0.0
        return self.cache_read_tokens / total_input

    def summary(self) -> str:
        if self.calls == 0:
            return "IA não foi consultada nesta sessão."
        parts = [
            f"{self.calls} chamada(s), US$ {self.total_usd:.4f}",
            f"cache: {self.cache_hit_ratio * 100:.0f}% da entrada",
        ]
        for model, cost in sorted(self.by_model.items()):
            parts.append(f"{model}: US$ {cost:.4f}")
        return " | ".join(parts)


class Budget:
    """Decide se a próxima chamada pode acontecer, e contabiliza as que houve.

    `check()` devolve **string vazia quando permite** e o motivo quando recusa.
    Motivo em texto, e não um booleano, porque a recusa aparece na interface:
    "orçamento da sessão esgotado" é informação; um `False` silencioso vira
    relatório de bug.
    """

    def __init__(
        self,
        limits: BudgetLimits | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limits = limits or BudgetLimits()
        self._clock = clock
        self._ledger = BudgetLedger()
        self._last_quick_at: float | None = None
        self._quick_this_lap = 0

    @property
    def ledger(self) -> BudgetLedger:
        return self._ledger

    @property
    def limits(self) -> BudgetLimits:
        return self._limits

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self._limits.session_usd - self._ledger.total_usd)

    def check(self, level: AdviceLevel) -> str:
        """Motivo da recusa, ou string vazia se pode chamar."""
        if self._ledger.total_usd >= self._limits.session_usd:
            return (
                f"orçamento da sessão esgotado "
                f"(US$ {self._ledger.total_usd:.2f} de "
                f"US$ {self._limits.session_usd:.2f})"
            )

        if level is not AdviceLevel.QUICK:
            # Debrief e relatório acontecem com o carro parado, uma vez cada.
            # Não há cadência a limitar — só o teto de custo acima.
            return ""

        if self._quick_this_lap >= self._limits.quick_per_lap:
            return "já houve nota suficiente nesta volta"

        if self._last_quick_at is not None:
            elapsed = self._clock() - self._last_quick_at
            if elapsed < self._limits.quick_interval_s:
                return f"nota recente ({elapsed:.0f} s atrás)"

        return ""

    def allows(self, level: AdviceLevel) -> bool:
        return not self.check(level)

    def record(self, level: AdviceLevel, usage: AIUsage) -> None:
        """Contabiliza uma chamada que **de fato** aconteceu.

        Marcar a cadência aqui, e não em `check()`, é o que impede uma consulta
        recusada mais adiante (ou que estourou) de bloquear a próxima: só gasta
        a cota quem realmente falou.
        """
        self._ledger.record(usage)
        if level is AdviceLevel.QUICK:
            self._last_quick_at = self._clock()
            self._quick_this_lap += 1

    def new_lap(self) -> None:
        """Zera a cota de notas. O intervalo mínimo atravessa a linha de meta."""
        self._quick_this_lap = 0

    def new_session(self) -> None:
        self._ledger = BudgetLedger()
        self._last_quick_at = None
        self._quick_this_lap = 0
