"""
`gt7ai` — o Race Engineer (§7 e §49).

Plugin, não núcleo. `gt7core` não importa nada daqui — o teste de arquitetura
falha o build se alguém tentar —, e a aplicação inteira funciona com este
pacote ausente, sem chave de API ou sem a SDK instalada. A IA acrescenta
redação e priorização em cima do diagnóstico; ela não produz o diagnóstico.

Ordem de leitura:

1. `client` — a fronteira com a API. Tudo o que é específico da Anthropic mora
   aqui, e só aqui;
2. `prompts` — o que sobe: **resultado de análise, nunca telemetria bruta**;
3. `models` — o que desce: `Advice`, com ações e proveniência;
4. `budget` — quanto custou e de quanto em quanto tempo é aceitável falar;
5. `engineer` — os três níveis, todos com resposta local garantida.

Uso típico:

    from gt7ai import RaceEngineer

    engineer = RaceEngineer.from_settings(settings)
    advice = engineer.debrief(report, track="Suzuka", lap_time_ms=132_450)
    print(advice.full_text())

Sem chave configurada isso ainda imprime um debrief — montado a partir da
análise da Fase 4, marcado com `advice.source == AdviceSource.LOCAL`.
"""

from .budget import Budget, BudgetLedger, BudgetLimits
from .client import (
    AIClient,
    AIRequest,
    AIResponse,
    AIUnavailable,
    AIUsage,
    AnthropicClient,
    ScriptedClient,
)
from .engineer import RaceEngineer
from .models import Action, Advice, AdviceLevel, AdviceSource

__all__ = [
    "Action",
    "Advice",
    "AdviceLevel",
    "AdviceSource",
    "AIClient",
    "AIRequest",
    "AIResponse",
    "AIUnavailable",
    "AIUsage",
    "AnthropicClient",
    "Budget",
    "BudgetLedger",
    "BudgetLimits",
    "RaceEngineer",
    "ScriptedClient",
]
