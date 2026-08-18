"""
Fronteira com o provedor de IA.

Todo o resto do `gt7ai` fala com esta interface, nunca com a SDK diretamente.
Isso compra três coisas concretas:

1. **Testes sem rede.** `ScriptedClient` devolve respostas fixas; a suíte roda
   offline, determinística e de graça. Um teste que precisa de chave de API é
   um teste que ninguém roda.
2. **Trocar de provedor sem tocar no engenheiro.** O briefing (§49) pede que a
   IA seja módulo adicional, nunca o núcleo — e um dia pode ser outro modelo.
3. **Um lugar só para os detalhes da API.** Os parâmetros que mudaram entre
   gerações de modelo (pensamento, esforço, amostragem) ficam contidos aqui.

O import da SDK é **preguiçoso**, dentro do construtor. Sem isso, `import
gt7ai` exigiria o pacote `anthropic` instalado, e a aplicação inteira passaria
a depender de uma biblioteca que só importa a quem liga a IA.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from gt7core.config.settings import AIConfig
from gt7core.observability.logging import get_logger

_log = get_logger(__name__)

# Preços públicos por milhão de tokens, para a estimativa de custo. São dados,
# não constantes de negócio: ficam aqui para que o número que a aplicação
# mostra seja rastreável a uma tabela, e não a uma conta inventada.
PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

# Leitura em cache custa ~10% da entrada; escrita custa ~25% a mais.
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25


class AIUnavailable(RuntimeError):
    """A IA não pôde responder. **Nunca deve derrubar a captura.**

    Quem chama trata isto como "sem conselho desta vez" e segue. Telemetria e
    gravação não podem parar porque uma API remota está fora do ar.
    """


@dataclass(frozen=True, slots=True)
class AIRequest:
    """Um pedido ao modelo, no vocabulário desta aplicação."""

    system: str
    """Instrução estável. É a parte cacheada — ver `cacheable`."""

    user: str
    """O conteúdo variável: os números desta volta, desta sessão."""

    model: str
    max_tokens: int = 2000

    effort: str | None = None
    """`low` | `medium` | `high` | `xhigh` | `max`. None usa o padrão do modelo."""

    schema: dict[str, Any] | None = None
    """Esquema JSON para saída estruturada. None devolve texto livre."""

    cacheable: bool = True
    """Marca o prompt de sistema para cache.

    O mínimo cacheável no `claude-opus-5` é 512 tokens; abaixo disso a marca é
    ignorada em silêncio — sem erro, só sem economia. O prompt de sistema do
    engenheiro passa folgadamente disso.
    """


@dataclass(frozen=True, slots=True)
class AIUsage:
    """Consumo de uma chamada, para o orçamento."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    model: str = ""

    @property
    def cost_usd(self) -> float:
        """Custo estimado em dólares, aos preços de tabela."""
        rates = PRICING_USD_PER_MTOK.get(self.model)
        if rates is None:
            return 0.0
        input_rate, output_rate = rates

        billable_input = (
            self.input_tokens
            + self.cache_read_tokens * CACHE_READ_MULTIPLIER
            + self.cache_write_tokens * CACHE_WRITE_MULTIPLIER
        )
        return (
            billable_input * input_rate + self.output_tokens * output_rate
        ) / 1_000_000

    @property
    def cache_hit(self) -> bool:
        """Se o prompt de sistema veio do cache.

        Zero em chamadas repetidas significa que algo invalida o prefixo — um
        timestamp no prompt de sistema, tipicamente. Vale vigiar.
        """
        return self.cache_read_tokens > 0


@dataclass(frozen=True, slots=True)
class AIResponse:
    """A resposta do modelo, já normalizada."""

    text: str
    usage: AIUsage
    stop_reason: str = "end_turn"
    parsed: dict[str, Any] | None = None
    """Saída estruturada já decodificada, quando houve `schema`."""

    @property
    def was_refused(self) -> bool:
        return self.stop_reason == "refusal"


class AIClient(Protocol):
    """O contrato. Implementado pela SDK real e pelo cliente de teste."""

    def complete(self, request: AIRequest) -> AIResponse: ...


class AnthropicClient:
    """Cliente real, sobre a SDK oficial da Anthropic.

    Parâmetros que **não** são enviados, de propósito
    -------------------------------------------------
    `temperature`, `top_p` e `top_k` foram removidos no `claude-opus-5` e
    devolvem 400. O mesmo vale para `budget_tokens`: o controle de profundidade
    de raciocínio passou a ser `output_config.effort`.

    O pensamento adaptativo fica **ligado** por padrão neste modelo — omitir o
    parâmetro não o desliga. A consequência prática é de dimensionamento:
    `max_tokens` limita raciocínio **mais** resposta, então um teto apertado
    trunca a resposta no meio. Os limites usados aqui já contam com isso.
    """

    def __init__(self, config: AIConfig) -> None:
        if not config.api_key:
            raise AIUnavailable("sem chave de API configurada")

        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depende do ambiente
            raise AIUnavailable(
                "pacote 'anthropic' não instalado — pip3 install anthropic"
            ) from exc

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=config.api_key.reveal())
        self._config = config

    def complete(self, request: AIRequest) -> AIResponse:
        payload = self._build_payload(request)

        try:
            message = self._client.messages.create(**payload)
        except self._anthropic.RateLimitError as exc:
            # Limite de taxa é transitório: quem chama pode tentar de novo mais
            # tarde. Não é motivo para desligar a IA da sessão inteira.
            raise AIUnavailable("limite de taxa atingido") from exc
        except self._anthropic.APIStatusError as exc:
            raise AIUnavailable(f"erro da API ({exc.status_code})") from exc
        except self._anthropic.APIConnectionError as exc:
            raise AIUnavailable("falha de rede ao falar com a IA") from exc

        return self._to_response(message, request)

    def _build_payload(self, request: AIRequest) -> dict[str, Any]:
        # O prompt de sistema vai como lista de blocos para poder carregar a
        # marca de cache no último. Como string simples não haveria onde pôr.
        system_block: dict[str, Any] = {"type": "text", "text": request.system}
        if request.cacheable:
            system_block["cache_control"] = {"type": "ephemeral"}

        payload: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": [system_block],
            "messages": [{"role": "user", "content": request.user}],
        }

        output_config: dict[str, Any] = {}
        if request.effort:
            output_config["effort"] = request.effort
        if request.schema:
            output_config["format"] = {
                "type": "json_schema",
                "schema": request.schema,
            }
        if output_config:
            payload["output_config"] = output_config

        return payload

    def _to_response(self, message: Any, request: AIRequest) -> AIResponse:
        usage = AIUsage(
            input_tokens=getattr(message.usage, "input_tokens", 0) or 0,
            output_tokens=getattr(message.usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(message.usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(message.usage, "cache_creation_input_tokens", 0)
            or 0,
            model=request.model,
        )

        stop_reason = getattr(message, "stop_reason", "end_turn") or "end_turn"

        # Recusa **antes** de ler o conteúdo: numa recusa o `content` vem vazio
        # (ou parcial), e indexar o primeiro bloco levanta IndexError. É a
        # ordem que não pode ser invertida.
        if stop_reason == "refusal":
            _log.warning("a IA recusou o pedido", extra={"model": request.model})
            return AIResponse(text="", usage=usage, stop_reason=stop_reason)

        text = _first_text(message.content)
        parsed: dict[str, Any] | None = None
        if request.schema and text:
            try:
                decoded = json.loads(text)
                parsed = decoded if isinstance(decoded, dict) else None
            except json.JSONDecodeError:
                # Saída estruturada garante JSON válido, mas um truncamento por
                # `max_tokens` produz um fragmento. Degrada para texto em vez
                # de estourar.
                _log.warning("resposta estruturada veio truncada ou inválida")

        return AIResponse(
            text=text, usage=usage, stop_reason=stop_reason, parsed=parsed
        )


def _first_text(blocks: Sequence[Any]) -> str:
    """Concatena os blocos de texto da resposta.

    A resposta é uma lista de blocos heterogêneos — pensamento, texto, uso de
    ferramenta. Só os de texto interessam aqui, e checar `.type` antes de ler
    `.text` evita o erro clássico de assumir que o primeiro bloco é texto (com
    pensamento ligado, muitas vezes não é).
    """
    return "".join(
        block.text for block in blocks if getattr(block, "type", None) == "text"
    ).strip()


@dataclass
class ScriptedClient:
    """Cliente de teste: devolve respostas roteirizadas, sem rede.

    Existe pelo mesmo motivo que `MockTelemetrySource`: sem ele, exercitar o
    engenheiro exigiria chave de API, rede e dinheiro por execução — e o teste
    passaria a depender do que um modelo respondeu naquele dia, que é o oposto
    de determinístico.
    """

    responses: list[AIResponse] = field(default_factory=list)
    requests: list[AIRequest] = field(default_factory=list)
    failure: Exception | None = None

    def complete(self, request: AIRequest) -> AIResponse:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        if not self.responses:
            raise AIUnavailable("nenhuma resposta roteirizada")
        return self.responses.pop(0)

    @classmethod
    def replying(cls, *texts: str) -> ScriptedClient:
        """Atalho: um cliente que devolve estes textos, nesta ordem."""
        return cls(
            responses=[
                AIResponse(text=text, usage=AIUsage(model="claude-haiku-4-5"))
                for text in texts
            ]
        )
