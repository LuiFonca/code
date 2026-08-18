"""
O provedor local: um modelo pequeno rodando na máquina do piloto.

Fala o dialeto **compatível com OpenAI** (`POST /v1/chat/completions`), que é o
denominador comum de praticamente todo servidor local: Ollama, `llama-server`
do llama.cpp, LM Studio e vLLM expõem exatamente esse endpoint. Uma
implementação cobre os quatro, e trocar de runtime é mudar uma URL.

Duas escolhas que valem explicação
----------------------------------
**Só biblioteca padrão.** O HTTP é feito com `urllib`. Instalar um cliente HTTP
para falar com um servidor em `localhost` seria pagar uma dependência (e o
risco de conflito de versão que vem com ela) por conveniência nenhuma. O ponto
deste provedor é não custar nada — nem dinheiro, nem dependência.

**Falhar é normal e barato.** O servidor local não estar de pé é o estado
esperado, não a exceção: o piloto pode simplesmente não ter aberto o Ollama.
Conexão recusada vira `AIUnavailable` como qualquer outro problema, e a
aplicação segue com a análise da Fase 4 — que é gratuita e determinística. Por
isso o provedor local pode vir ligado por padrão: ligado sem servidor não
quebra nada.

Portabilidade
-------------
O alvo é Mac (Apple Silicon, Metal) agora e Windows sem GPU depois. Nada aqui é
específico de plataforma: quem resolve isso é o runtime do outro lado da URL, e
tanto Ollama quanto llama.cpp instalam nos dois. O que muda entre as máquinas é
só a velocidade, e o `timeout` existe para que a máquina lenta degrade para o
conselho local em vez de travar a interface.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from gt7core.config.settings import AIConfig
from gt7core.observability.logging import get_logger

from .client import AIRequest, AIResponse, AIUnavailable, AIUsage

_log = get_logger(__name__)

# Modelo local não tem preço. `AIUsage.cost_usd` já devolve 0 para modelo fora
# da tabela de preços, então o livro-caixa contabiliza as chamadas e soma zero —
# que é exatamente o relatório correto para este provedor.


@dataclass(frozen=True, slots=True)
class LocalEndpoint:
    """Para onde falar. Separado da configuração para os testes apontarem
    para um servidor de mentira sem montar `Settings` inteiro."""

    url: str = "http://localhost:11434/v1"
    model: str = "qwen3:4b"
    fast_model: str = "qwen3:4b"
    timeout_s: float = 30.0

    @classmethod
    def from_config(cls, config: AIConfig) -> LocalEndpoint:
        return cls(
            url=config.local_url.rstrip("/"),
            model=config.local_model,
            fast_model=config.local_fast_model,
            timeout_s=config.local_timeout_s,
        )


class LocalClient:
    """Cliente para um servidor local compatível com OpenAI."""

    def __init__(self, endpoint: LocalEndpoint | None = None) -> None:
        self._endpoint = endpoint or LocalEndpoint()

    @classmethod
    def from_config(cls, config: AIConfig) -> LocalClient:
        return cls(LocalEndpoint.from_config(config))

    @property
    def endpoint(self) -> LocalEndpoint:
        return self._endpoint

    def complete(self, request: AIRequest) -> AIResponse:
        payload = self._build_payload(request)

        try:
            body = self._post(payload, timeout=self._timeout_for(request))
        except _SchemaRejected:
            # Servidor antigo que não conhece `json_schema`. Em vez de desistir,
            # cai para o modo JSON genérico: o esquema deixa de ser imposto na
            # decodificação, mas o `parsed` continua sendo validado depois e o
            # guarda de números segue valendo. Melhor degradar que exigir uma
            # versão específica do runtime de quem só quer usar o programa.
            _log.info("servidor local sem json_schema — usando modo JSON genérico")
            payload["response_format"] = {"type": "json_object"}
            body = self._post(payload, timeout=self._timeout_for(request))

        return self._to_response(body, request)

    # ------------------------------------------------------------------

    def _model_for(self, request: AIRequest) -> str:
        """O pedido traz o nome do modelo da nuvem; aqui ele é reinterpretado.

        Quem monta o pedido não deve precisar saber qual provedor vai atendê-lo
        — é o que mantém `prompts.py` com uma versão só de cada nível. O que o
        pedido de fato comunica é *urgência*, e `effort="low"` é como o nível
        do rádio a expressa.
        """
        return (
            self._endpoint.fast_model
            if request.effort == "low"
            else self._endpoint.model
        )

    def _timeout_for(self, request: AIRequest) -> float:
        """A nota de rádio tem orçamento de latência; o debrief não.

        Um teto curto no nível 1 não é economia: é a diferença entre um
        conselho útil e um conselho sobre a curva que já passou. Estourar o
        tempo cai no conselho local, que chega na hora.
        """
        if request.effort == "low":
            return min(8.0, self._endpoint.timeout_s)
        return self._endpoint.timeout_s

    def _build_payload(self, request: AIRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model_for(request),
            "max_tokens": request.max_tokens,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            # Modelo pequeno com temperatura alta inventa. Aqui não se quer
            # criatividade: quer-se a leitura mais provável de um diagnóstico
            # que já está pronto.
            "temperature": 0.3,
            "stream": False,
        }

        if request.schema:
            # Impor o formato na **decodificação**, não no prompt. Pedir JSON a
            # um modelo de 4B com uma frase produz JSON quebrado com frequência
            # alta; restringir os tokens possíveis produz JSON válido sempre.
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "debrief",
                    "strict": True,
                    "schema": request.schema,
                },
            }
        return payload

    def _post(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        http_request = urllib.request.Request(
            f"{self._endpoint.url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(http_request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = _read_error(exc)
            # A condição olha para `json_schema` especificamente, e não para
            # "existe response_format". Com a versão frouxa, a **segunda**
            # tentativa (já em `json_object`) levantava o sinal de novo e ele
            # escapava sem ninguém para pegá-lo — um erro de servidor virava
            # exceção interna vazando pela fronteira que existe para impedir
            # exatamente isso.
            sent_schema = (
                payload.get("response_format", {}).get("type") == "json_schema"
            )
            if exc.code == 400 and sent_schema and (
                "schema" in detail.lower() or "format" in detail.lower()
            ):
                raise _SchemaRejected from exc

            # Servidor no ar, modelo ausente. É um caso distinto o bastante para
            # merecer a própria frase: o 404 cru dizia
            # `{"error":{"message":"model 'qwen3:4b' not found",...}}`, que tem a
            # informação certa embrulhada em JSON e não diz o que fazer. Quem lê
            # isso na tela conclui que a IA está quebrada, quando falta um
            # comando de uma linha — e o servidor ter respondido prova que a
            # parte difícil (instalar e subir o Ollama) já deu certo.
            if exc.code == 404 and "not found" in detail.lower():
                wanted = payload.get("model", self._endpoint.model)
                raise AIUnavailable(
                    f"o modelo '{wanted}' não está instalado. "
                    f"Rode: ollama pull {wanted}"
                ) from exc

            raise AIUnavailable(
                f"servidor local respondeu {exc.code}: {detail[:200]}"
            ) from exc
        except TimeoutError as exc:
            raise AIUnavailable(f"modelo local demorou mais de {timeout:.0f} s") from exc
        except urllib.error.URLError as exc:
            # O caso comum: o Ollama simplesmente não está aberto. A mensagem
            # diz o que fazer, porque ela chega ao usuário na interface.
            raise AIUnavailable(
                f"nenhum servidor de IA em {self._endpoint.url} "
                "— abra o Ollama ou desligue a IA"
            ) from exc

        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AIUnavailable("resposta do servidor local não era JSON") from exc

        if not isinstance(body, dict):
            raise AIUnavailable("resposta do servidor local em formato inesperado")
        return body

    def _to_response(self, body: dict[str, Any], request: AIRequest) -> AIResponse:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AIUnavailable("servidor local não devolveu nenhuma resposta")

        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        text = _strip_reasoning(content if isinstance(content, str) else "")

        maybe_usage = body.get("usage")
        raw_usage: dict[str, Any] = maybe_usage if isinstance(maybe_usage, dict) else {}
        usage = AIUsage(
            input_tokens=int(raw_usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(raw_usage.get("completion_tokens", 0) or 0),
            model=self._model_for(request),
        )

        parsed: dict[str, Any] | None = None
        if request.schema and text:
            try:
                decoded = json.loads(text)
                parsed = decoded if isinstance(decoded, dict) else None
            except json.JSONDecodeError:
                _log.warning("modelo local devolveu JSON inválido")

        return AIResponse(text=text, usage=usage, stop_reason="end_turn", parsed=parsed)


class _SchemaRejected(Exception):
    """Sinal interno: o servidor não entendeu `json_schema`."""


def _read_error(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover - o corpo do erro é opcional
        return exc.reason if isinstance(exc.reason, str) else ""


def _strip_reasoning(text: str) -> str:
    """Remove o bloco de raciocínio dos modelos que pensam em voz alta.

    Qwen3 e vários outros emitem `<think>…</think>` antes da resposta. Sem isto
    o raciocínio interno iria para o rádio do piloto — que é o equivalente local
    de confundir bloco de pensamento com bloco de texto na API da Anthropic.
    """
    while "<think>" in text and "</think>" in text:
        start = text.index("<think>")
        end = text.index("</think>") + len("</think>")
        if end <= start:
            break
        text = text[:start] + text[end:]
    return text.strip()
