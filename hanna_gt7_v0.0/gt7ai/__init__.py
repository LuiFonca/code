"""
`gt7ai` — o Race Engineer (§7 e §49).

Plugin, não núcleo. `gt7core` não importa nada daqui — o teste de arquitetura
falha o build se alguém tentar —, e a aplicação inteira funciona com este
pacote ausente, sem chave de API ou sem a SDK instalada. A IA acrescenta
redação e priorização em cima do diagnóstico; ela não produz o diagnóstico.

Ordem de leitura:

1. `local` — o provedor **padrão**: um modelo pequeno na máquina do piloto,
   via endpoint compatível com OpenAI (Ollama, llama.cpp, LM Studio). Sem
   custo, sem chave, sem rede;
2. `client` — o provedor de nuvem, opcional, que só é montado com chave paga.
   Tudo o que é específico da Anthropic mora ali, e só ali;
3. `guard` — verifica que a resposta não citou número que não estava no
   contexto. É a regra "não invente número" imposta por aritmética, para os
   modelos pequenos que não a seguem sozinhos;
4. `prompts` — o que sobe: **resultado de análise, nunca telemetria bruta**.
   Em duas versões, porque um modelo de 4B segue três regras e não seis;
5. `models` — o que desce: `Advice`, com ações e proveniência;
6. `budget` — quanto custou e de quanto em quanto tempo é aceitável falar;
7. `engineer` — os três níveis, todos com resposta local garantida.

Uso típico:

    from gt7ai import RaceEngineer

    engineer = RaceEngineer.from_settings(settings)
    advice = engineer.debrief(report, track="Suzuka", lap_time_ms=132_450)
    print(advice.full_text())

Por padrão isso fala com um modelo local e não custa nada. Sem servidor de IA
no ar, ainda imprime um debrief — montado a partir da análise da Fase 4 e
marcado com `advice.source == AdviceSource.LOCAL`. Nunca há um caminho em que
o piloto fica sem resposta.
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
from .guard import is_grounded, unsupported_numbers
from .local import LocalClient, LocalEndpoint
from .models import Action, Advice, AdviceLevel, AdviceSource

__all__ = [
    "LocalClient",
    "LocalEndpoint",
    "is_grounded",
    "unsupported_numbers",
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
