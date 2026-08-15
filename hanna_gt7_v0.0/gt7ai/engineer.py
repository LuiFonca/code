"""
O engenheiro de corrida — os três níveis do §7 num objeto só.

| Nível | Quando | Modelo | Formato |
|-------|--------|--------|---------|
| `quick_note` | com o piloto na pista | rápido | uma frase falada |
| `debrief` | volta terminada | principal | JSON com ações |
| `session_report` | fim da sessão | principal | quatro parágrafos |

A propriedade que atravessa os três, e que é o ponto desta classe: **nenhum
deles pode falhar.** Sem chave, sem rede, sem crédito, com a API fora do ar ou
com o modelo recusando — todos os caminhos terminam num `Advice` válido, porque
a análise da Fase 4 já sabia responder sozinha. A IA melhora a redação e a
priorização; ela não é a fonte do diagnóstico.

É por isso que os métodos privados `_local_*` não são "tratamento de erro". São
a resposta padrão do sistema, e a chamada remota é o caminho que tenta melhorá-la.
Quem lê o resultado distingue os dois por `Advice.source`.
"""

from __future__ import annotations

from typing import Any

from gt7core.analytics.corners import Corner
from gt7core.analytics.driver import DriverProfile
from gt7core.analytics.timeloss import TimeLossReport
from gt7core.config.settings import AIConfig, Settings
from gt7core.observability.logging import get_logger

from . import guard, prompts
from .budget import Budget
from .client import AIClient, AIRequest, AIResponse, AIUnavailable
from .models import Action, Advice, AdviceLevel, AdviceSource

_log = get_logger(__name__)

# Resposta combinada com o prompt de nível 1 para "não tenho nada a dizer".
# Vale mais que uma frase inventada: o rádio ficar quieto é informação.
NO_NOTE = "SEM NOTA"


class RaceEngineer:
    """Ponto único de entrada da IA na aplicação."""

    def __init__(
        self,
        client: AIClient | None,
        config: AIConfig,
        *,
        budget: Budget | None = None,
    ) -> None:
        self._client = client
        self._config = config
        self._budget = budget or Budget()

    @classmethod
    def from_settings(
        cls, settings: Settings, *, budget: Budget | None = None
    ) -> RaceEngineer:
        """Monta o engenheiro a partir da configuração, sem nunca estourar.

        Configuração desligada, chave ausente ou SDK não instalada produzem um
        engenheiro **sem cliente**, que responde localmente. A aplicação sobe
        igual nos dois casos; é exatamente o que o §49 pede ao dizer que a IA é
        módulo adicional.
        """
        config = settings.ai
        if not config.enabled:
            return cls(None, config, budget=budget)

        try:
            if config.is_local:
                from .local import LocalClient

                # Nada de rede aqui: montar o cliente local só guarda uma URL.
                # Se o servidor não estiver de pé, descobre-se na primeira
                # chamada e ela vira conselho local — não vale checar agora e
                # atrasar a abertura do programa por um servidor que pode subir
                # depois.
                return cls(LocalClient.from_config(config), config, budget=budget)

            from .client import AnthropicClient

            return cls(AnthropicClient(config), config, budget=budget)
        except AIUnavailable as exc:
            _log.info("IA desligada: %s", exc)
            return cls(None, config, budget=budget)

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------

    @property
    def budget(self) -> Budget:
        return self._budget

    @property
    def is_online(self) -> bool:
        """Se há um cliente. Falso não significa quebrado — significa local."""
        return self._client is not None

    @property
    def _compact(self) -> bool:
        """Modelo pequeno pede prompt pequeno e verificação de números.

        As duas coisas andam juntas porque têm a mesma causa: um modelo de 4B
        segue três regras, não seis. O que não cabe no prompt vira verificação
        depois da resposta.
        """
        return self._config.is_local

    @property
    def _main_model(self) -> str:
        return (
            self._config.local_model if self._config.is_local else self._config.model
        )

    @property
    def _quick_model(self) -> str:
        return (
            self._config.local_fast_model
            if self._config.is_local
            else self._config.fast_model
        )

    def new_lap(self) -> None:
        self._budget.new_lap()

    def new_session(self) -> None:
        self._budget.new_session()

    # ------------------------------------------------------------------
    # Nível 1 — nota de rádio
    # ------------------------------------------------------------------

    def quick_note(self, situation: str, *, fallback: str = "") -> Advice | None:
        """Uma frase para o piloto agora. `None` quando não há o que dizer.

        Devolver `None` — e não um `Advice` vazio — é deliberado: quem chama faz
        `if note:` e o silêncio se propaga sem que ninguém precise checar
        conteúdo. Um conselho de string vazia acabaria narrado pela voz.
        """
        denial = self._budget.check(AdviceLevel.QUICK)
        if denial:
            _log.debug("nota rápida suprimida: %s", denial)
            return None

        response = self._call(
            prompts.build_quick_request(
                model=self._quick_model,
                situation=situation,
                compact=self._compact,
            ),
            AdviceLevel.QUICK,
        )
        if response is None:
            return self._local_quick(fallback)

        text = response.text.strip()
        if not text or NO_NOTE in text.upper():
            return None

        return Advice(
            level=AdviceLevel.QUICK,
            headline=_one_line(text),
            source=AdviceSource.AI,
            model=self._quick_model,
            usage=response.usage,
        )

    # ------------------------------------------------------------------
    # Nível 2 — debrief da volta
    # ------------------------------------------------------------------

    def debrief(
        self,
        report: TimeLossReport,
        *,
        track: str,
        car: str = "",
        lap_time_ms: int = 0,
        reference_time_ms: int | None = None,
        corners: list[Corner] | None = None,
        profile: DriverProfile | None = None,
    ) -> Advice:
        """O debrief da volta que acabou. Sempre devolve algo."""
        local = self._local_debrief(report)

        if self._budget.check(AdviceLevel.DEBRIEF):
            return local

        response = self._call(
            prompts.build_debrief_request(
                model=self._main_model,
                header=prompts.format_header(
                    track=track,
                    car=car,
                    lap_time_ms=lap_time_ms,
                    reference_time_ms=reference_time_ms,
                ),
                time_loss=prompts.format_time_loss(report),
                corners=prompts.format_corners(corners or []),
                profile=prompts.format_profile(profile),
                compact=self._compact,
            ),
            AdviceLevel.DEBRIEF,
        )
        if response is None:
            return local

        advice = _advice_from_payload(response, self._main_model)
        return advice if advice is not None else local

    # ------------------------------------------------------------------
    # Nível 3 — relatório da sessão
    # ------------------------------------------------------------------

    def session_report(
        self,
        profile: DriverProfile | None,
        *,
        track: str,
        car: str = "",
        lap_times_ms: list[int] | None = None,
        recurring: str = "",
    ) -> Advice:
        """O fechamento da sessão, olhando todas as voltas."""
        local = self._local_session(profile)

        if self._budget.check(AdviceLevel.SESSION):
            return local

        response = self._call(
            prompts.build_session_request(
                model=self._main_model,
                header=prompts.format_header(track=track, car=car),
                pace=prompts.format_pace(lap_times_ms or []),
                profile=prompts.format_profile(profile),
                recurring=recurring,
                compact=self._compact,
            ),
            AdviceLevel.SESSION,
        )
        if response is None or not response.text.strip():
            return local

        text = response.text.strip()
        headline, _, detail = text.partition("\n")
        return Advice(
            level=AdviceLevel.SESSION,
            headline=_one_line(headline),
            detail=detail.strip() or text,
            source=AdviceSource.AI,
            model=self._main_model,
            usage=response.usage,
        )

    # ------------------------------------------------------------------
    # Chamada
    # ------------------------------------------------------------------

    def _call(self, request: AIRequest, level: AdviceLevel) -> AIResponse | None:
        """Faz a chamada e contabiliza. `None` significa "use o local".

        Toda exceção da fronteira morre aqui. Um erro de IA nunca sobe para o
        laço de captura — a telemetria continua gravando com a API fora do ar,
        que é a regra que o `AIUnavailable` existe para sustentar.
        """
        if self._client is None:
            return None

        try:
            response = self._client.complete(request)
        except AIUnavailable as exc:
            _log.warning("IA indisponível (%s): %s", level, exc)
            return None
        except Exception as exc:  # pragma: no cover - rede é imprevisível
            # Rede e SDK inventam exceções que a fronteira não previu. Aqui,
            # engolir é o comportamento correto: o custo é um conselho local,
            # e a alternativa é derrubar a gravação da sessão.
            _log.error("erro inesperado ao consultar a IA: %s", exc)
            return None

        self._budget.record(level, response.usage)

        if response.was_refused:
            _log.warning("a IA recusou o pedido (%s)", level)
            return None

        if self._compact and not self._is_grounded(response, request):
            return None
        return response

    def _is_grounded(self, response: AIResponse, request: AIRequest) -> bool:
        """Confere se a resposta só cita números que estavam no contexto.

        Ativo apenas no provedor local, e a assimetria é intencional. Um modelo
        pequeno erra a regra "não invente número" com frequência que importa; um
        grande a segue, e nele o guarda passaria a atrapalhar — porque somar
        duas perdas do contexto produz um número novo e **legítimo**, que a
        verificação não tem como distinguir de um inventado.

        No local esse mesmo caso continua sendo recusado, e é a escolha certa
        pelo mesmo motivo: um 4B somando 0,652 com 0,574 erra a conta com
        frequência parecida com a que acerta. Recusar custa um debrief da
        análise da Fase 4 — que é gratuito, correto, e já estava pronto.
        """
        answer = response.text
        if response.parsed:
            answer = " ".join(
                str(value) for value in _flatten(response.parsed)
            )

        invented = guard.unsupported_numbers(answer, request.user)
        if invented:
            _log.warning(
                "resposta descartada: números sem origem no contexto %s", invented
            )
            return False
        return True

    # ------------------------------------------------------------------
    # As respostas locais
    # ------------------------------------------------------------------

    def _local_quick(self, fallback: str) -> Advice | None:
        if not fallback.strip():
            return None
        return Advice(
            level=AdviceLevel.QUICK,
            headline=_one_line(fallback),
            source=AdviceSource.LOCAL,
        )

    def _local_debrief(self, report: TimeLossReport) -> Advice:
        """O debrief que a análise já sabia escrever.

        Cada ação sai de um `SegmentLoss`: o trecho dá o *onde*, `cause()` dá o
        *o quê* — e `cause()` é a frase que os detectores de frenagem e
        acelerador produziram medindo, não adivinhando.
        """
        if not report.segments:
            return Advice(
                level=AdviceLevel.DEBRIEF,
                headline="Sem comparação possível: falta uma volta de referência.",
                source=AdviceSource.LOCAL,
            )

        worst = report.worst(3)
        if not worst:
            headline = (
                f"Volta consistente com a referência "
                f"({report.total_delta_ms / 1000:+.3f} s), sem trecho destacado."
            )
        else:
            headline = (
                f"{report.total_delta_ms / 1000:+.3f} s no total; "
                f"{report.recoverable_ms / 1000:.3f} s recuperáveis, "
                f"a maior parte em {worst[0].label}."
            )

        return Advice(
            level=AdviceLevel.DEBRIEF,
            headline=headline,
            detail=_dominant_pattern(report),
            actions=[
                Action(
                    where=segment.label,
                    instruction=segment.cause(),
                    gain_ms=segment.time_delta_ms,
                )
                for segment in worst
            ],
            source=AdviceSource.LOCAL,
        )

    def _local_session(self, profile: DriverProfile | None) -> Advice:
        if profile is None:
            return Advice(
                level=AdviceLevel.SESSION,
                headline="Voltas insuficientes para um relatório de sessão.",
                source=AdviceSource.LOCAL,
            )

        weaknesses = profile.weaknesses()
        headline = (
            f"{profile.lap_count} voltas, melhor "
            f"{prompts.format_lap_time(profile.best_lap_ms)}, "
            f"ritmo {profile.consistency_label}."
        )
        return Advice(
            level=AdviceLevel.SESSION,
            headline=headline,
            detail=profile.summary(),
            actions=[
                Action(where="Sessão", instruction=note) for note in weaknesses[:3]
            ],
            source=AdviceSource.LOCAL,
        )


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------


def _one_line(text: str) -> str:
    """Achata para uma linha. O rádio e o cabeçalho não têm quebra."""
    return " ".join(text.split())


def _dominant_pattern(report: TimeLossReport) -> str:
    """O padrão que se repete entre as perdas — a síntese que o local pode fazer.

    Vale explicar por que este método existe. A primeira versão do debrief local
    punha `report.summary()` no detalhe, e o resultado impresso dizia a mesma
    coisa três vezes: o título nomeava o pior trecho, o detalhe relistava os
    mesmos trechos, e as ações relistavam de novo. Informação repetida ocupa
    espaço e ainda dá a impressão de que há mais conteúdo do que há.

    O que o texto da IA acrescentava naquele mesmo caso não era estilo: era
    perceber que as quatro perdas tinham a **mesma causa** e portanto eram um
    problema só. Isso é contagem, não linguagem — dá para fazer aqui, de graça,
    e é o que o detalhe passa a dizer.
    """
    losses = report.losses
    if len(losses) < 2:
        return ""

    counts: dict[str, list[str]] = {}
    for segment in losses:
        cause = segment.cause()
        if cause:
            counts.setdefault(cause, []).append(segment.label)

    if not counts:
        return ""

    cause, labels = max(counts.items(), key=lambda item: len(item[1]))
    if len(labels) < 2:
        return (
            f"As perdas têm causas diferentes: {len(counts)} padrões distintos "
            f"em {len(losses)} trecho(s)."
        )

    shared_ms = sum(s.time_delta_ms for s in losses if s.cause() == cause)
    return (
        f"O mesmo padrão aparece em {len(labels)} trechos "
        f"({', '.join(labels)}): {cause}. "
        f"Somados, valem {shared_ms / 1000:.3f} s — é um problema só, "
        f"não {len(labels)}."
    )


def _advice_from_payload(response: AIResponse, model: str) -> Advice | None:
    """Converte a saída estruturada em `Advice`, ou `None` se veio inutilizável.

    A saída estruturada garante o formato, mas garantia de esquema não é
    garantia de conteúdo: um truncamento por `max_tokens` deixa `parsed` nulo, e
    a única resposta honesta é cair no debrief local em vez de exibir um cartão
    com título vazio.
    """
    payload = response.parsed
    if not isinstance(payload, dict):
        return None

    headline = str(payload.get("headline", "")).strip()
    if not headline:
        return None

    return Advice(
        level=AdviceLevel.DEBRIEF,
        headline=_one_line(headline),
        detail=str(payload.get("detail", "")).strip(),
        actions=_actions_from_payload(payload.get("actions")),
        source=AdviceSource.AI,
        model=model,
        usage=response.usage,
    )


def _flatten(payload: Any) -> list[Any]:
    """Todos os valores escalares de uma estrutura aninhada.

    O guarda precisa ver os números que estão dentro das ações, não só os do
    título — é justamente no `gain_ms` de uma ação que um número inventado
    passaria despercebido.
    """
    if isinstance(payload, dict):
        return [item for value in payload.values() for item in _flatten(value)]
    if isinstance(payload, list):
        return [item for value in payload for item in _flatten(value)]
    return [payload]


def _actions_from_payload(raw: Any) -> list[Action]:
    if not isinstance(raw, list):
        return []

    actions: list[Action] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        where = str(item.get("where", "")).strip()
        instruction = str(item.get("instruction", "")).strip()
        if not where or not instruction:
            continue

        gain = item.get("gain_ms")
        actions.append(
            Action(
                where=where,
                instruction=instruction,
                gain_ms=float(gain) if isinstance(gain, (int, float)) else None,
            )
        )
    return actions
