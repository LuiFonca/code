"""
Fase 7 — o Race Engineer.

Nenhum teste deste arquivo toca a rede. `ScriptedClient` devolve respostas
fixas, e é isso que torna a suíte executável por qualquer pessoa, em qualquer
máquina, de graça e com o mesmo resultado. Um teste que precisa de chave de API
é um teste que roda uma vez e depois é pulado para sempre.

Três propriedades concentram o valor daqui:

1. **A IA nunca vê telemetria bruta.** `TestOQueSobe` mede o tamanho e o
   conteúdo do que é enviado a partir de uma volta sintética de verdade.
2. **Nada derruba a captura.** `TestDegradacao` corta a IA de todas as formas
   possíveis — ausente, fora do ar, recusando, truncada — e exige um `Advice`
   válido em todas.
3. **O prefixo cacheado é estável.** Um único caractere variável no prompt de
   sistema anula o cache e multiplica a conta sem que nada quebre visivelmente.
"""

from __future__ import annotations

import pytest

from gt7ai import (
    Advice,
    AdviceLevel,
    AdviceSource,
    AIRequest,
    AIResponse,
    AIUnavailable,
    AIUsage,
    Budget,
    BudgetLimits,
    RaceEngineer,
    ScriptedClient,
    prompts,
)
from gt7ai.client import AnthropicClient
from gt7core.analytics.corners import detect_corners
from gt7core.analytics.driver import build_profile
from gt7core.analytics.timeloss import analyse_time_loss
from gt7core.config.settings import AIConfig, SecretStr, Settings
from gt7core.domain.models import TelemetryPoint
from gt7core.events.bus import EventBus
from gt7core.telemetry.engine import TelemetryEngine, TelemetryReceived
from gt7core.telemetry.sources.mock import synthetic_lap


def build_lap(**kwargs: object) -> list[TelemetryPoint]:
    bus = EventBus()
    engine = TelemetryEngine(bus)
    points: list[TelemetryPoint] = []
    bus.subscribe(TelemetryReceived, lambda e: points.append(e.point))
    for frame in synthetic_lap(**kwargs):  # type: ignore[arg-type]
        engine.on_frame(frame)
    return points


@pytest.fixture(scope="module")
def report():  # noqa: ANN201
    """Uma comparação real entre duas voltas — a entrada do nível 2."""
    reference = build_lap(lap_time_ms=102_000)
    slower = build_lap(lap_time_ms=104_500)
    return analyse_time_loss(reference, slower)


@pytest.fixture(scope="module")
def corners():  # noqa: ANN201
    return detect_corners(build_lap(lap_time_ms=104_500))


def make_config(**overrides: object) -> AIConfig:
    """Configuração do provedor de **nuvem**.

    Explícita desde que o padrão passou a ser local: sem `provider`, estes
    testes passariam a exercitar os nomes de modelo locais e o guarda de
    números, que é outro caminho e tem arquivo próprio (`test_ai_local.py`).
    """
    config = AIConfig(
        provider="anthropic", enabled=True, api_key=SecretStr("test-key")
    )
    for name, value in overrides.items():
        setattr(config, name, value)
    return config


def ai_response(text: str = "", **overrides: object) -> AIResponse:
    fields: dict[str, object] = {
        "text": text,
        "usage": AIUsage(input_tokens=900, output_tokens=120, model="claude-opus-5"),
    }
    fields.update(overrides)
    return AIResponse(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# A fronteira com a API
# ---------------------------------------------------------------------------


class TestPayload:
    """O formato do pedido. Cada asserção aqui corresponde a um erro 400 real."""

    def _payload(self, request: AIRequest) -> dict:
        # `_build_payload` é puro: monta o dicionário sem tocar na rede, então
        # dá para verificá-lo sem instanciar a SDK.
        return AnthropicClient._build_payload(None, request)  # type: ignore[arg-type]  # noqa: SLF001

    def test_sistema_vai_como_bloco_com_marca_de_cache(self) -> None:
        payload = self._payload(
            AIRequest(system="instrução", user="dados", model="claude-opus-5")
        )
        assert payload["system"] == [
            {
                "type": "text",
                "text": "instrução",
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def test_sem_cache_nao_marca(self) -> None:
        payload = self._payload(
            AIRequest(
                system="i", user="d", model="claude-opus-5", cacheable=False
            )
        )
        assert "cache_control" not in payload["system"][0]

    def test_esforco_e_formato_ficam_dentro_de_output_config(self) -> None:
        """Na raiz, ambos devolvem 400 — é o erro mais fácil de cometer."""
        payload = self._payload(
            AIRequest(
                system="i",
                user="d",
                model="claude-opus-5",
                effort="high",
                schema={"type": "object"},
            )
        )
        assert payload["output_config"]["effort"] == "high"
        assert payload["output_config"]["format"]["type"] == "json_schema"
        assert "effort" not in payload
        assert "format" not in payload

    def test_sem_output_config_quando_nao_ha_nada_para_pôr(self) -> None:
        payload = self._payload(AIRequest(system="i", user="d", model="claude-opus-5"))
        assert "output_config" not in payload

    def test_parametros_removidos_no_modelo_atual_nunca_sao_enviados(self) -> None:
        """`temperature`, `top_p`, `top_k` e `budget_tokens` viraram 400."""
        payload = self._payload(
            AIRequest(system="i", user="d", model="claude-opus-5", effort="low")
        )
        for proibido in ("temperature", "top_p", "top_k", "budget_tokens", "thinking"):
            assert proibido not in payload


class TestRespostaDaApi:
    class _Block:
        def __init__(self, block_type: str, text: str = "") -> None:
            self.type = block_type
            self.text = text

    class _Usage:
        input_tokens = 1000
        output_tokens = 200
        cache_read_input_tokens = 4000
        cache_creation_input_tokens = 0

    class _Message:
        stop_reason = "end_turn"

        def __init__(self, content: list, stop_reason: str = "end_turn") -> None:
            self.content = content
            self.stop_reason = stop_reason
            self.usage = TestRespostaDaApi._Usage()

    def _to_response(self, message: object, request: AIRequest) -> AIResponse:
        return AnthropicClient._to_response(None, message, request)  # type: ignore[arg-type]  # noqa: SLF001

    def test_bloco_de_pensamento_nao_e_confundido_com_texto(self) -> None:
        """Com pensamento ligado, `content[0]` normalmente **não** é a resposta.

        Ler o primeiro bloco cegamente devolveria o raciocínio interno — ou
        estouraria, porque bloco de pensamento não tem `.text` utilizável.
        """
        message = self._Message(
            [self._Block("thinking", "..."), self._Block("text", "a resposta")]
        )
        response = self._to_response(
            message, AIRequest(system="i", user="d", model="claude-opus-5")
        )
        assert response.text == "a resposta"

    def test_recusa_e_lida_antes_do_conteudo(self) -> None:
        """Numa recusa o conteúdo vem vazio: indexá-lo levantaria IndexError."""
        message = self._Message([], stop_reason="refusal")
        response = self._to_response(
            message, AIRequest(system="i", user="d", model="claude-opus-5")
        )
        assert response.was_refused
        assert response.text == ""

    def test_saida_estruturada_e_decodificada(self) -> None:
        message = self._Message([self._Block("text", '{"headline": "ok"}')])
        response = self._to_response(
            message,
            AIRequest(
                system="i", user="d", model="claude-opus-5", schema={"type": "object"}
            ),
        )
        assert response.parsed == {"headline": "ok"}

    def test_json_truncado_degrada_para_texto(self) -> None:
        """Um teto de tokens curto corta o JSON no meio. Não pode estourar."""
        message = self._Message([self._Block("text", '{"headline": "inco')])
        response = self._to_response(
            message,
            AIRequest(
                system="i", user="d", model="claude-opus-5", schema={"type": "object"}
            ),
        )
        assert response.parsed is None
        assert response.text


class TestCusto:
    def test_conta_bate_com_a_tabela(self) -> None:
        usage = AIUsage(input_tokens=1_000_000, output_tokens=0, model="claude-opus-5")
        assert usage.cost_usd == pytest.approx(5.0)

    def test_leitura_de_cache_custa_um_decimo(self) -> None:
        cached = AIUsage(cache_read_tokens=1_000_000, model="claude-opus-5")
        assert cached.cost_usd == pytest.approx(0.5)
        assert cached.cache_hit

    def test_modelo_desconhecido_nao_inventa_preco(self) -> None:
        assert AIUsage(input_tokens=999, model="modelo-fantasia").cost_usd == 0.0


# ---------------------------------------------------------------------------
# O que sobe para o modelo
# ---------------------------------------------------------------------------


class TestOQueSobe:
    def test_o_debrief_manda_analise_e_nao_amostras(self, report, corners) -> None:  # noqa: ANN001
        """A decisão central da fase, medida em números.

        A volta tem milhares de amostras de 27 canais. O que sobe são algumas
        dezenas de linhas de diagnóstico. Se um dia alguém "melhorar" o contexto
        despejando os pontos, este teste falha antes da fatura chegar.
        """
        request = prompts.build_debrief_request(
            model="claude-opus-5",
            header=prompts.format_header(track="Suzuka", lap_time_ms=104_500),
            time_loss=prompts.format_time_loss(report),
            corners=prompts.format_corners(corners),
        )

        assert len(request.user) < 4000, "contexto grande demais para um debrief"
        assert request.user.count("\n") < 60
        # Vestígio de despejo bruto: nenhuma linha deve ser uma sequência de
        # números separados por vírgula.
        assert "," not in request.user or max(
            line.count(",") for line in request.user.splitlines()
        ) < 5

    def test_o_contexto_carrega_o_diagnostico_da_fase_4(self, report) -> None:  # noqa: ANN001
        texto = prompts.format_time_loss(report)
        assert "Diferença total" in texto
        assert "Recuperável" in texto
        # Os rótulos vêm da detecção de curvas, não de string inventada aqui.
        assert any(
            segment.label in texto for segment in report.worst(3)
        ), "os piores trechos não apareceram no contexto"

    def test_a_nota_de_radio_e_curta(self) -> None:
        """Nível 1 paga latência por linha. Não é lugar para contexto amplo."""
        situacao = prompts.format_live_situation(
            track="Suzuka",
            lap_number=4,
            delta_ms=320.0,
            where="Curva 2",
            event="travamento da dianteira esquerda",
        )
        assert len(situacao) < 300
        request = prompts.build_quick_request(
            model="claude-haiku-4-5", situation=situacao
        )
        assert request.effort == "low"
        assert request.schema is None

    def test_o_ritmo_mostra_forma_e_nao_so_media(self) -> None:
        texto = prompts.format_pace([104_000, 103_000, 102_500, 105_000])
        assert texto.count("Volta") == 4
        assert "← melhor" in texto

    def test_ritmo_longo_e_truncado(self) -> None:
        texto = prompts.format_pace([100_000 + i for i in range(50)], limit=10)
        assert texto.count("- Volta") == 10
        assert "omitida" in texto

    def test_sem_volta_valida_nao_inventa_ritmo(self) -> None:
        assert "nenhuma volta" in prompts.format_pace([0, -1]).lower()


class TestPrefixoCacheado:
    """O prompt de sistema é a única coisa que se repete entre chamadas."""

    def test_e_identico_entre_pedidos_diferentes(self, report) -> None:  # noqa: ANN001
        a = prompts.build_debrief_request(
            model="claude-opus-5",
            header=prompts.format_header(track="Suzuka"),
            time_loss=prompts.format_time_loss(report),
        )
        b = prompts.build_quick_request(model="claude-haiku-4-5", situation="x")
        c = prompts.build_session_request(
            model="claude-opus-5", header="h", pace="p", profile="q"
        )
        assert a.system == b.system == c.system

    def test_e_grande_o_bastante_para_o_cache_pegar(self) -> None:
        """Abaixo de 512 tokens a marca de cache é ignorada **em silêncio**.

        Não há como contar tokens sem chamar a API, então o guarda é por
        caracteres, com a razão mais pessimista plausível para português
        acentuado (4 caracteres por token). Passando por aqui, passa lá.
        """
        assert len(prompts.SYSTEM_PROMPT) >= 512 * 4

    def test_nao_contem_nada_variavel(self) -> None:
        """Data, pista ou piloto no sistema invalidariam o prefixo a cada volta."""
        import datetime

        ano = str(datetime.date.today().year)
        assert ano not in prompts.SYSTEM_PROMPT

    def test_as_regras_que_seguram_a_alucinacao_estao_no_prompt(self) -> None:
        texto = prompts.SYSTEM_PROMPT.lower()
        assert "não invente" in texto
        assert "sempre diga onde" in texto


# ---------------------------------------------------------------------------
# Os três níveis
# ---------------------------------------------------------------------------


class TestDebrief:
    def test_usa_a_resposta_estruturada_da_ia(self, report) -> None:  # noqa: ANN001
        client = ScriptedClient(
            responses=[
                ai_response(
                    parsed={
                        "headline": "Perdeu na frenagem da Curva 3.",
                        "detail": "Solta o freio cedo.",
                        "actions": [
                            {
                                "where": "Curva 3",
                                "instruction": "Segure o freio 15 m mais.",
                                "gain_ms": 180.0,
                            }
                        ],
                    }
                )
            ]
        )
        engineer = RaceEngineer(client, make_config())

        advice = engineer.debrief(report, track="Suzuka", lap_time_ms=104_500)

        assert advice.source is AdviceSource.AI
        assert advice.level is AdviceLevel.DEBRIEF
        assert advice.actions[0].where == "Curva 3"
        assert advice.actions[0].gain_ms == 180.0
        assert "0.18 s" in advice.actions[0].describe()

    def test_o_pedido_leva_esquema_e_esforco(self, report) -> None:  # noqa: ANN001
        client = ScriptedClient(responses=[ai_response(parsed={"headline": "ok"})])
        RaceEngineer(client, make_config()).debrief(report, track="Suzuka")

        sent = client.requests[0]
        assert sent.schema is not None
        assert sent.model == "claude-opus-5"
        assert sent.effort == "medium"

    def test_acao_incompleta_e_descartada_sem_derrubar(self, report) -> None:  # noqa: ANN001
        client = ScriptedClient(
            responses=[
                ai_response(
                    parsed={
                        "headline": "ok",
                        "actions": [
                            {"where": "", "instruction": "algo"},
                            {"where": "Curva 1", "instruction": "Freie mais tarde."},
                            "isto não é um objeto",
                        ],
                    }
                )
            ]
        )
        advice = RaceEngineer(client, make_config()).debrief(report, track="S")
        assert len(advice.actions) == 1
        assert advice.actions[0].gain_ms is None


class TestNotaDeRadio:
    def test_uma_frase_so(self) -> None:
        client = ScriptedClient.replying("Freie 10 metros mais tarde na Curva 3.")
        advice = RaceEngineer(client, make_config()).quick_note("contexto")

        assert advice is not None
        assert advice.level is AdviceLevel.QUICK
        assert advice.speech() == "Freie 10 metros mais tarde na Curva 3."

    def test_multilinha_e_achatada_para_a_voz(self) -> None:
        client = ScriptedClient.replying("Freie mais tarde\n  na Curva 3.")
        advice = RaceEngineer(client, make_config()).quick_note("contexto")
        assert advice is not None
        assert "\n" not in advice.speech()

    def test_sem_nota_produz_silencio(self) -> None:
        """O rádio calado é resposta válida, e melhor que frase inventada."""
        client = ScriptedClient.replying("SEM NOTA")
        assert RaceEngineer(client, make_config()).quick_note("contexto") is None

    def test_usa_o_modelo_rapido(self) -> None:
        client = ScriptedClient.replying("ok")
        RaceEngineer(client, make_config()).quick_note("contexto")
        assert client.requests[0].model == "claude-haiku-4-5"


class TestRelatorioDeSessao:
    def test_primeira_linha_vira_titulo(self) -> None:
        client = ScriptedClient(
            responses=[
                ai_response("O ritmo caiu no fim.\n\nDetalhe do raciocínio aqui.")
            ]
        )
        advice = RaceEngineer(client, make_config()).session_report(
            None, track="Suzuka", lap_times_ms=[104_000, 103_000]
        )
        assert advice.headline == "O ritmo caiu no fim."
        assert "Detalhe" in advice.detail

    def test_pede_mais_profundidade_que_os_outros_niveis(self) -> None:
        client = ScriptedClient(responses=[ai_response("linha")])
        RaceEngineer(client, make_config()).session_report(None, track="S")
        sent = client.requests[0]
        assert sent.effort == "high"
        assert sent.max_tokens >= 4000


# ---------------------------------------------------------------------------
# Degradação — a propriedade que não pode falhar
# ---------------------------------------------------------------------------


class TestDegradacao:
    """Todos os caminhos de falha terminam num conselho utilizável."""

    def test_sem_cliente_o_debrief_sai_da_analise_local(self, report) -> None:  # noqa: ANN001
        engineer = RaceEngineer(None, make_config())
        advice = engineer.debrief(report, track="Suzuka")

        assert not engineer.is_online
        assert advice.source is AdviceSource.LOCAL
        assert advice.actions, "o debrief local precisa apontar trechos"
        # A instrução local é a causa medida pelos detectores da Fase 4.
        assert advice.actions[0].instruction
        assert advice.actions[0].where == report.worst(1)[0].label

    def test_o_debrief_local_sintetiza_em_vez_de_relistar(self, report) -> None:  # noqa: ANN001
        """A síntese que o caminho local consegue fazer sem a API.

        A primeira versão punha `report.summary()` no detalhe e dizia a mesma
        coisa três vezes — título, detalhe e ações, todos relistando os mesmos
        trechos. O que a IA acrescentava naquele caso era perceber que as perdas
        tinham a **mesma causa** e portanto eram um problema só; isso é
        contagem, e contagem roda offline.
        """
        advice = RaceEngineer(None, make_config()).debrief(report, track="S")

        assert advice.detail, "o detalhe local ficou vazio"
        assert "problema só" in advice.detail
        # E não é a lista das ações repetida.
        for action in advice.actions:
            assert action.describe() not in advice.detail

    def test_sem_padrao_repetido_nao_inventa_um(self) -> None:
        from gt7ai.engineer import _dominant_pattern
        from gt7core.analytics.timeloss import SegmentLoss, TimeLossReport

        def segment(label: str, delta: float) -> SegmentLoss:
            return SegmentLoss(
                label=label,
                start_distance_m=0.0,
                end_distance_m=100.0,
                time_delta_ms=delta,
                corner=None,
                braking=None,
                throttle=None,
            )

        # Um trecho perdido só: não há padrão a declarar.
        assert _dominant_pattern(TimeLossReport([segment("Curva 1", 400.0)], 400.0)) == ""
        # Nenhuma perda significativa: idem.
        assert _dominant_pattern(TimeLossReport([segment("Curva 1", 5.0)], 5.0)) == ""

    def test_api_fora_do_ar_cai_no_local(self, report) -> None:  # noqa: ANN001
        client = ScriptedClient(failure=AIUnavailable("sem rede"))
        advice = RaceEngineer(client, make_config()).debrief(report, track="S")
        assert advice.source is AdviceSource.LOCAL
        assert not advice.is_empty

    def test_recusa_do_modelo_cai_no_local(self, report) -> None:  # noqa: ANN001
        client = ScriptedClient(responses=[ai_response(stop_reason="refusal")])
        advice = RaceEngineer(client, make_config()).debrief(report, track="S")
        assert advice.source is AdviceSource.LOCAL

    def test_resposta_truncada_cai_no_local(self, report) -> None:  # noqa: ANN001
        """Esquema válido, conteúdo inutilizável: `parsed` nulo por truncamento."""
        client = ScriptedClient(responses=[ai_response("{\"headl", parsed=None)])
        advice = RaceEngineer(client, make_config()).debrief(report, track="S")
        assert advice.source is AdviceSource.LOCAL

    def test_titulo_vazio_cai_no_local(self, report) -> None:  # noqa: ANN001
        client = ScriptedClient(responses=[ai_response(parsed={"headline": "   "})])
        advice = RaceEngineer(client, make_config()).debrief(report, track="S")
        assert advice.source is AdviceSource.LOCAL

    def test_excecao_inesperada_nao_sobe(self, report) -> None:  # noqa: ANN001
        """A regra do §7: um erro de IA nunca derruba a captura."""
        client = ScriptedClient(failure=ValueError("bug na SDK"))
        advice = RaceEngineer(client, make_config()).debrief(report, track="S")
        assert advice.source is AdviceSource.LOCAL

    def test_sessao_sem_perfil_diz_que_faltam_voltas(self) -> None:
        advice = RaceEngineer(None, make_config()).session_report(None, track="S")
        assert advice.source is AdviceSource.LOCAL
        assert "insuficientes" in advice.headline

    def test_sessao_local_usa_o_perfil_do_piloto(self) -> None:
        laps = [build_lap(lap_time_ms=102_000 + i * 400) for i in range(6)]
        profile = build_profile(laps)
        assert profile is not None

        advice = RaceEngineer(None, make_config()).session_report(profile, track="S")
        assert advice.source is AdviceSource.LOCAL
        assert "6 voltas" in advice.headline
        assert profile.summary() == advice.detail

    def test_relatorio_sem_segmentos_e_honesto(self) -> None:
        empty = analyse_time_loss([], [])
        advice = RaceEngineer(None, make_config()).debrief(empty, track="S")
        assert "referência" in advice.headline
        assert advice.actions == []

    def test_configuracao_desligada_produz_engenheiro_local(self) -> None:
        settings = Settings()
        settings.ai.enabled = False
        engineer = RaceEngineer.from_settings(settings)
        assert not engineer.is_online

    def test_nuvem_ligada_sem_chave_nao_estoura(self) -> None:
        """Marcar `enabled` sem exportar a chave é o engano mais comum."""
        settings = Settings()
        settings.ai.provider = "anthropic"
        settings.ai.enabled = True
        engineer = RaceEngineer.from_settings(settings)
        assert not engineer.is_online


# ---------------------------------------------------------------------------
# Orçamento e cadência
# ---------------------------------------------------------------------------


class _Clock:
    """Relógio manual: o teste de intervalo não pode dormir de verdade."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class TestOrcamento:
    def test_nota_recente_cala_a_proxima(self) -> None:
        clock = _Clock()
        budget = Budget(BudgetLimits(quick_interval_s=25.0), clock=clock)

        assert budget.allows(AdviceLevel.QUICK)
        budget.record(AdviceLevel.QUICK, AIUsage(model="claude-haiku-4-5"))
        assert not budget.allows(AdviceLevel.QUICK)

        clock.now += 26.0
        assert budget.allows(AdviceLevel.QUICK)

    def test_cota_por_volta_zera_na_linha_de_meta(self) -> None:
        clock = _Clock()
        budget = Budget(
            BudgetLimits(quick_interval_s=0.0, quick_per_lap=2), clock=clock
        )
        for _ in range(2):
            budget.record(AdviceLevel.QUICK, AIUsage(model="claude-haiku-4-5"))

        assert "nesta volta" in budget.check(AdviceLevel.QUICK)
        budget.new_lap()
        assert budget.allows(AdviceLevel.QUICK)

    def test_cadencia_nao_afeta_o_debrief(self) -> None:
        """Debrief e relatório acontecem com o carro parado."""
        budget = Budget(BudgetLimits(quick_interval_s=999.0))
        budget.record(AdviceLevel.QUICK, AIUsage(model="claude-haiku-4-5"))
        assert budget.allows(AdviceLevel.DEBRIEF)
        assert budget.allows(AdviceLevel.SESSION)

    def test_teto_de_sessao_desliga_tudo(self) -> None:
        budget = Budget(BudgetLimits(session_usd=0.01))
        budget.record(
            AdviceLevel.DEBRIEF,
            AIUsage(input_tokens=1_000_000, model="claude-opus-5"),
        )
        assert "orçamento" in budget.check(AdviceLevel.DEBRIEF)
        assert budget.remaining_usd == 0.0

    def test_chamada_recusada_nao_gasta_cota(self) -> None:
        """Só marca a cadência quem de fato falou — `check` não cobra."""
        clock = _Clock()
        budget = Budget(BudgetLimits(quick_interval_s=25.0), clock=clock)
        for _ in range(5):
            budget.check(AdviceLevel.QUICK)
        assert budget.allows(AdviceLevel.QUICK)
        assert budget.ledger.calls == 0

    def test_livro_caixa_soma_por_modelo(self) -> None:
        budget = Budget()
        budget.record(
            AdviceLevel.DEBRIEF, AIUsage(input_tokens=200_000, model="claude-opus-5")
        )
        budget.record(
            AdviceLevel.QUICK, AIUsage(input_tokens=200_000, model="claude-haiku-4-5")
        )
        ledger = budget.ledger
        assert ledger.calls == 2
        assert ledger.by_model["claude-opus-5"] == pytest.approx(1.0)
        assert ledger.by_model["claude-haiku-4-5"] == pytest.approx(0.2)
        assert "US$" in ledger.summary()

    def test_taxa_de_cache_e_visivel(self) -> None:
        """Se ela desabar, alguém pôs algo variável no prompt de sistema."""
        budget = Budget()
        budget.record(
            AdviceLevel.DEBRIEF,
            AIUsage(input_tokens=250, cache_read_tokens=750, model="claude-opus-5"),
        )
        assert budget.ledger.cache_hit_ratio == pytest.approx(0.75)

    def test_sem_chamada_o_resumo_diz_isso(self) -> None:
        assert "não foi consultada" in Budget().ledger.summary()

    def test_o_engenheiro_respeita_a_cadencia(self) -> None:
        clock = _Clock()
        client = ScriptedClient.replying("Freie mais tarde.", "Solte o freio antes.")
        engineer = RaceEngineer(
            client,
            make_config(),
            budget=Budget(BudgetLimits(quick_interval_s=25.0), clock=clock),
        )

        assert engineer.quick_note("a") is not None
        assert engineer.quick_note("b") is None, "falou duas vezes seguidas"
        assert len(client.requests) == 1, "chamou a API mesmo suprimindo a nota"

        clock.now += 30.0
        assert engineer.quick_note("c") is not None

    def test_teto_estourado_devolve_debrief_local(self, report) -> None:  # noqa: ANN001
        client = ScriptedClient(responses=[ai_response(parsed={"headline": "ia"})])
        budget = Budget(BudgetLimits(session_usd=0.0001))
        budget.record(
            AdviceLevel.DEBRIEF, AIUsage(input_tokens=100_000, model="claude-opus-5")
        )

        advice = RaceEngineer(client, make_config(), budget=budget).debrief(
            report, track="S"
        )
        assert advice.source is AdviceSource.LOCAL
        assert client.requests == [], "gastou depois de estourar o teto"

    def test_nova_sessao_zera_o_livro(self) -> None:
        budget = Budget()
        budget.record(AdviceLevel.DEBRIEF, AIUsage(input_tokens=1000, model="claude-opus-5"))
        budget.new_session()
        assert budget.ledger.calls == 0


# ---------------------------------------------------------------------------
# Formatação do resultado
# ---------------------------------------------------------------------------


class TestAdvice:
    def test_texto_completo_junta_titulo_detalhe_e_acoes(self) -> None:
        from gt7ai import Action

        advice = Advice(
            level=AdviceLevel.DEBRIEF,
            headline="Título",
            detail="Detalhe",
            actions=[Action(where="Curva 1", instruction="Freie depois", gain_ms=250.0)],
        )
        texto = advice.full_text()
        assert "Título" in texto
        assert "Detalhe" in texto
        assert "Curva 1: Freie depois (~0.25 s)" in texto

    def test_conselho_vazio_e_reconhecivel(self) -> None:
        assert Advice(level=AdviceLevel.QUICK, headline="  ").is_empty

    def test_tempo_de_volta_no_formato_do_painel(self) -> None:
        assert prompts.format_lap_time(92_345) == "1:32.345"
        assert prompts.format_lap_time(0) == "—"

    def test_cabecalho_mostra_o_delta_para_a_referencia(self) -> None:
        header = prompts.format_header(
            track="Suzuka", car="GT-R", lap_time_ms=104_500, reference_time_ms=102_000
        )
        assert "Suzuka" in header
        assert "GT-R" in header
        assert "+2.500 s" in header
