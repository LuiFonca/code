"""
O provedor local e o guarda de números.

O cliente local é testado contra um **servidor HTTP de verdade**, subido em
`localhost` numa porta efêmera. Não é rigor gratuito: o que se quer verificar é
justamente o comportamento de rede — cabeçalho, corpo, código de erro, timeout,
conexão recusada — e um `urlopen` remendado não erraria nenhuma dessas coisas do
mesmo jeito que a rede erra. O servidor sobe em milissegundos e a suíte continua
offline, determinística e sem depender de ter um modelo instalado.

O caso mais importante do arquivo é `test_servidor_fora_do_ar_nao_derruba_nada`.
Ele descreve o estado **normal** deste provedor: o piloto abriu o programa e não
abriu o Ollama. Isso não pode virar erro na tela — vira o debrief da Fase 4.
"""

from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from gt7ai import LocalClient, LocalEndpoint, RaceEngineer, unsupported_numbers
from gt7ai.client import AIRequest, AIUnavailable
from gt7ai.guard import is_grounded, numbers_in
from gt7ai.models import AdviceSource
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


# ---------------------------------------------------------------------------
# Um servidor de mentira que fala o dialeto compatível com OpenAI
# ---------------------------------------------------------------------------


class FakeServer:
    """Servidor local roteirizado. Guarda o que recebeu, devolve o que mandarem."""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.reply: dict[str, Any] = _chat_reply("ok")
        self.status = 200
        self.error_body = ""
        self.delay_s = 0.0
        self._server = HTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def endpoint(self, **overrides: Any) -> LocalEndpoint:
        return LocalEndpoint(url=self.url, **overrides)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802  (assinatura do stdlib)
                import time

                length = int(self.headers.get("Content-Length", 0))
                outer.received.append(json.loads(self.rfile.read(length)))

                if outer.delay_s:
                    time.sleep(outer.delay_s)

                if outer.status != 200:
                    body = outer.error_body.encode()
                    self.send_response(outer.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                body = json.dumps(outer.reply).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                """Silêncio: o servidor de teste não polui a saída do pytest."""

        return Handler


def _chat_reply(content: str, **usage: int) -> dict[str, Any]:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 400, "completion_tokens": 60, **usage},
    }


@pytest.fixture
def server():  # noqa: ANN201
    fake = FakeServer()
    fake.start()
    yield fake
    fake.stop()


def request(**overrides: Any) -> AIRequest:
    fields: dict[str, Any] = {
        "system": "instrução",
        "user": "Curva 1: 0.652 s perdidos",
        "model": "ignorado",
    }
    fields.update(overrides)
    return AIRequest(**fields)


# ---------------------------------------------------------------------------
# O que o cliente local manda
# ---------------------------------------------------------------------------


class TestPedidoLocal:
    def test_fala_o_dialeto_compativel_com_openai(self, server) -> None:  # noqa: ANN001
        LocalClient(server.endpoint()).complete(request())

        sent = server.received[0]
        assert [m["role"] for m in sent["messages"]] == ["system", "user"]
        assert sent["stream"] is False
        # Modelo pequeno com temperatura alta inventa; aqui não se quer criar.
        assert sent["temperature"] <= 0.4

    def test_esquema_e_imposto_na_decodificacao(self, server) -> None:  # noqa: ANN001
        """Pedir JSON no prompt a um 4B produz JSON quebrado; restringir não."""
        server.reply = _chat_reply('{"headline": "ok"}')
        schema = {"type": "object", "properties": {"headline": {"type": "string"}}}

        response = LocalClient(server.endpoint()).complete(request(schema=schema))

        sent = server.received[0]
        assert sent["response_format"]["type"] == "json_schema"
        assert sent["response_format"]["json_schema"]["schema"] == schema
        assert response.parsed == {"headline": "ok"}

    def test_sem_esquema_nao_restringe(self, server) -> None:  # noqa: ANN001
        LocalClient(server.endpoint()).complete(request())
        assert "response_format" not in server.received[0]

    def test_o_nivel_do_radio_usa_o_modelo_rapido(self, server) -> None:  # noqa: ANN001
        """O pedido não sabe qual provedor o atende; `effort` comunica urgência."""
        endpoint = server.endpoint(model="grande:8b", fast_model="pequeno:4b")
        client = LocalClient(endpoint)

        client.complete(request(effort="low"))
        client.complete(request(effort="high"))

        assert server.received[0]["model"] == "pequeno:4b"
        assert server.received[1]["model"] == "grande:8b"


class TestRespostaLocal:
    def test_le_conteudo_e_uso(self, server) -> None:  # noqa: ANN001
        server.reply = _chat_reply("Freie mais tarde na Curva 1.")
        response = LocalClient(server.endpoint()).complete(request())

        assert response.text == "Freie mais tarde na Curva 1."
        assert response.usage.input_tokens == 400
        assert response.usage.output_tokens == 60

    def test_modelo_local_nao_custa_nada(self, server) -> None:  # noqa: ANN001
        """O livro-caixa continua contando chamadas; a soma é zero."""
        response = LocalClient(server.endpoint()).complete(request())
        assert response.usage.cost_usd == 0.0

    def test_bloco_de_raciocinio_nao_vai_para_o_radio(self, server) -> None:  # noqa: ANN001
        """Qwen3 e afins pensam em voz alta antes de responder.

        É o mesmo erro que confundir bloco de pensamento com bloco de texto na
        API da Anthropic — só que aqui a marcação é `<think>`.
        """
        server.reply = _chat_reply(
            "<think>O piloto perdeu 0.652 s, devo focar na Curva 1</think>"
            "Freie 10 metros mais tarde na Curva 1."
        )
        response = LocalClient(server.endpoint()).complete(request())
        assert response.text == "Freie 10 metros mais tarde na Curva 1."
        assert "<think>" not in response.text

    def test_json_invalido_degrada_para_texto(self, server) -> None:  # noqa: ANN001
        server.reply = _chat_reply('{"headline": "corta')
        response = LocalClient(server.endpoint()).complete(
            request(schema={"type": "object"})
        )
        assert response.parsed is None
        assert response.text


class TestFalhasDoServidorLocal:
    def test_servidor_fora_do_ar_vira_ai_unavailable(self) -> None:
        """O estado **normal**: o piloto não abriu o Ollama."""
        client = LocalClient(LocalEndpoint(url="http://127.0.0.1:1/v1"))
        with pytest.raises(AIUnavailable) as exc:
            client.complete(request())
        # A mensagem chega ao usuário: precisa dizer o que fazer.
        assert "Ollama" in str(exc.value) or "servidor" in str(exc.value)

    def test_servidor_fora_do_ar_nao_derruba_nada(self) -> None:
        """A propriedade que sustenta ligar a IA local por padrão.

        Sem servidor, o debrief sai da análise da Fase 4 e o piloto nem percebe
        que havia um modelo envolvido.
        """
        config = AIConfig(provider="local", local_url="http://127.0.0.1:1/v1")
        engineer = RaceEngineer(LocalClient.from_config(config), config)
        report = analyse_time_loss(build_lap(lap_time_ms=102_000),
                                   build_lap(lap_time_ms=104_500))

        advice = engineer.debrief(report, track="Suzuka")
        assert advice.source is AdviceSource.LOCAL
        assert advice.actions

    def test_timeout_do_radio_e_mais_curto_que_o_do_debrief(self, server) -> None:  # noqa: ANN001
        """Conselho sobre a curva que já passou não é conselho."""
        server.delay_s = 0.5
        client = LocalClient(server.endpoint(timeout_s=0.1))

        with pytest.raises(AIUnavailable) as exc:
            client.complete(request(effort="low"))
        assert "demorou" in str(exc.value)

    def test_erro_http_vira_ai_unavailable(self, server) -> None:  # noqa: ANN001
        server.status = 500
        server.error_body = "modelo não carregado"
        with pytest.raises(AIUnavailable) as exc:
            LocalClient(server.endpoint()).complete(request())
        assert "500" in str(exc.value)

    def test_servidor_antigo_sem_json_schema_degrada(self, server) -> None:  # noqa: ANN001
        """Runtime velho recusa `json_schema`; cai para JSON genérico.

        Exigir uma versão específica do Ollama de quem só quer usar o programa
        seria transformar um detalhe nosso em problema do piloto.
        """
        server.status = 400
        server.error_body = json.dumps(
            {"error": {"message": "unsupported response_format schema"}}
        )

        client = LocalClient(server.endpoint())
        with pytest.raises(AIUnavailable):
            # A segunda tentativa também falha (o servidor está fixo em 400),
            # mas o que se verifica é que **houve** segunda tentativa.
            client.complete(request(schema={"type": "object"}))

        assert len(server.received) == 2, "não tentou de novo sem o esquema"
        assert server.received[0]["response_format"]["type"] == "json_schema"
        assert server.received[1]["response_format"] == {"type": "json_object"}

    def test_resposta_sem_escolhas_e_erro_claro(self, server) -> None:  # noqa: ANN001
        server.reply = {"choices": []}
        with pytest.raises(AIUnavailable):
            LocalClient(server.endpoint()).complete(request())


# ---------------------------------------------------------------------------
# O guarda de números
# ---------------------------------------------------------------------------


class TestGuardaDeNumeros:
    CONTEXTO = (
        "Curva 1: 0.652 s perdidos — 4 km/h a menos saindo\n"
        "Curva 2 @ 1802 m: 165 → 92 → 153"
    )

    def test_numero_do_contexto_passa(self) -> None:
        assert is_grounded("Perdeu 0.652 s na Curva 1.", self.CONTEXTO)

    def test_numero_inventado_e_apontado(self) -> None:
        """O caso que motivou o módulo: a velocidade que ninguém mediu."""
        invented = unsupported_numbers("Você passou a 170 km/h.", self.CONTEXTO)
        assert invented == [170.0]

    def test_arredondar_nao_e_inventar(self) -> None:
        """Recusar isto tornaria o guarda inútil: são as frases bem escritas."""
        assert is_grounded("Perdeu 0.65 s na Curva 1.", self.CONTEXTO)

    def test_virgula_decimal_conta_como_o_mesmo_numero(self) -> None:
        assert is_grounded("Perdeu 0,652 s.", self.CONTEXTO)

    def test_milissegundo_e_segundo_sao_a_mesma_grandeza(self) -> None:
        """O falso positivo que quase desligou a IA local em silêncio.

        O contexto fala em segundos porque é como o piloto lê; o esquema pede
        `gain_ms` porque é como o `Action` guarda. As duas unidades circulam de
        propósito. Comparando número cru, `652` não tinha origem em "0.652 s" —
        e o efeito seria a IA local cair no conselho da Fase 4 em **toda** volta,
        desligada por um verificador que ninguém suspeitaria.
        """
        assert is_grounded("gain_ms 652", self.CONTEXTO)
        assert unsupported_numbers("652", self.CONTEXTO) == []
        # E o inverso: o contexto em metros, a resposta em quilômetros.
        assert is_grounded("1.802 km", self.CONTEXTO)

    def test_a_tolerancia_de_unidade_nao_abre_a_porteira(self) -> None:
        """Aceitar escala não pode virar aceitar qualquer coisa."""
        assert not is_grounded("8500 rpm", self.CONTEXTO)
        assert not is_grounded("A 250 km/h no fim da reta.", self.CONTEXTO)

    def test_inteiro_pequeno_passa_sem_conferencia(self) -> None:
        """"Curva 3" e "duas correções" são referências, não medições."""
        assert is_grounded("Foque nas 2 primeiras curvas, sobretudo a 3.", "")

    def test_numero_grande_sem_origem_nao_passa(self) -> None:
        assert not is_grounded("O motor subiu a 8500 rpm.", self.CONTEXTO)

    def test_extrai_numeros_de_um_tempo_de_volta(self) -> None:
        assert numbers_in("1:32.345") == [1.0, 32.345]

    def test_texto_sem_numero_e_sempre_valido(self) -> None:
        assert is_grounded("Abra o acelerador mais cedo.", "")


class TestGuardaLigadoNoEngenheiro:
    """A verificação só age no provedor local, e isso é intencional."""

    def _report(self):  # noqa: ANN202
        return analyse_time_loss(
            build_lap(lap_time_ms=102_000), build_lap(lap_time_ms=104_500)
        )

    def _client_that_invents(self):  # noqa: ANN202
        from gt7ai import AIResponse, AIUsage, ScriptedClient

        return ScriptedClient(
            responses=[
                AIResponse(
                    text="",
                    usage=AIUsage(model="qwen3:4b"),
                    parsed={
                        "headline": "Você passou a 342 km/h na Curva 1.",
                        "detail": "",
                        "actions": [],
                    },
                )
            ]
        )

    def test_local_descarta_resposta_com_numero_inventado(self) -> None:
        config = AIConfig(provider="local")
        advice = RaceEngineer(self._client_that_invents(), config).debrief(
            self._report(), track="Suzuka"
        )
        assert advice.source is AdviceSource.LOCAL
        assert "342" not in advice.full_text()

    def test_nuvem_nao_aplica_o_guarda(self) -> None:
        """Num modelo grande, somar duas perdas do contexto é legítimo.

        O guarda não distingue soma correta de número inventado, então ligá-lo
        onde a regra já é seguida trocaria um problema real por um falso.
        """
        config = AIConfig(provider="anthropic", api_key=SecretStr("k"))
        advice = RaceEngineer(self._client_that_invents(), config).debrief(
            self._report(), track="Suzuka"
        )
        assert advice.source is AdviceSource.AI


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------


class TestConfiguracao:
    def test_o_padrao_e_local_e_gratuito(self) -> None:
        config = AIConfig()
        assert config.is_local
        assert config.provider == "local"
        assert not config.api_key

    def test_sem_chave_o_carregador_escolhe_local(
        self, monkeypatch, tmp_path  # noqa: ANN001
    ) -> None:
        """Padrão do carregador, isolado do `.env` de quem roda.

        `env_file=None` cai em `Path(".env")` — o arquivo do diretório atual.
        Estes dois testes passavam só porque o repositório não tinha um; numa
        cópia entregue com `.env` configurado eles falhavam, e a falha era do
        teste, que afirmava testar padrões e na verdade lia a máquina alheia.
        """
        for name in ("GT7_AI_API_KEY", "GT7_AI_PROVIDER", "GT7_AI_ENABLED"):
            monkeypatch.delenv(name, raising=False)
        settings = Settings.load(env_file=tmp_path / "inexistente.env")
        assert settings.ai.is_local
        assert settings.ai.enabled, "o local é gratuito: não há motivo para vir desligado"

    def test_com_chave_o_carregador_escolhe_a_nuvem(
        self, monkeypatch, tmp_path  # noqa: ANN001
    ) -> None:
        monkeypatch.setenv("GT7_AI_API_KEY", "sk-ant-teste")
        monkeypatch.delenv("GT7_AI_PROVIDER", raising=False)
        monkeypatch.delenv("GT7_AI_ENABLED", raising=False)
        settings = Settings.load(env_file=tmp_path / "inexistente.env")
        assert not settings.ai.is_local
        assert settings.ai.enabled

    def test_provedor_explicito_ganha_da_chave(self, monkeypatch, tmp_path) -> None:  # noqa: ANN001
        """Ter chave não obriga a gastá-la."""
        monkeypatch.setenv("GT7_AI_API_KEY", "sk-ant-teste")
        monkeypatch.setenv("GT7_AI_PROVIDER", "local")
        assert Settings.load(env_file=tmp_path / "inexistente.env").ai.is_local

    def test_nuvem_sem_chave_nao_liga(self, monkeypatch, tmp_path) -> None:  # noqa: ANN001
        monkeypatch.setenv("GT7_AI_PROVIDER", "anthropic")
        monkeypatch.delenv("GT7_AI_API_KEY", raising=False)
        assert not Settings.load(env_file=tmp_path / "inexistente.env").ai.enabled

    def test_endereco_e_modelo_vem_do_ambiente(self, monkeypatch, tmp_path) -> None:  # noqa: ANN001
        """Portar para outra máquina não pode exigir editar código."""
        monkeypatch.setenv("GT7_AI_LOCAL_URL", "http://192.168.0.9:8080/v1")
        monkeypatch.setenv("GT7_AI_LOCAL_MODEL", "gemma3:4b")
        config = Settings.load(env_file=tmp_path / "inexistente.env").ai
        assert config.local_url == "http://192.168.0.9:8080/v1"
        assert config.local_model == "gemma3:4b"
        # Sem modelo rápido explícito, usa o mesmo: com 4B, manter dois modelos
        # carregados custa mais memória do que economiza em latência.
        assert config.local_fast_model == "gemma3:4b"

    def test_o_engenheiro_monta_o_cliente_local_sem_tocar_a_rede(self) -> None:
        settings = Settings()
        settings.ai.provider = "local"
        engineer = RaceEngineer.from_settings(settings)
        assert engineer.is_online, "o cliente local deve ser montado sem checar o servidor"

    def test_desligada_por_ambiente_continua_valendo(self, monkeypatch, tmp_path) -> None:  # noqa: ANN001
        monkeypatch.setenv("GT7_AI_ENABLED", "false")
        assert not Settings.load(env_file=tmp_path / "inexistente.env").ai.enabled

    def test_prompt_compacto_no_local_completo_na_nuvem(self) -> None:
        from gt7ai.prompts import COMPACT_SYSTEM_PROMPT, SYSTEM_PROMPT, system_prompt_for

        assert system_prompt_for(compact=True) == COMPACT_SYSTEM_PROMPT
        assert system_prompt_for(compact=False) == SYSTEM_PROMPT
        # Três regras, não seis: é o que um 4B segue de verdade.
        assert COMPACT_SYSTEM_PROMPT.count("\n1.") == 1
        assert "4." not in COMPACT_SYSTEM_PROMPT
        assert len(COMPACT_SYSTEM_PROMPT) < len(SYSTEM_PROMPT) / 2


class TestModeloAusente:
    """O servidor está no ar; o modelo é que não foi baixado.

    Caso real, colhido do console de um usuário:

        IA indisponível (quick): servidor local respondeu 404:
        {"error":{"message":"model 'qwen3:4b' not found","type":"not_found_error"...

    A informação certa estava lá, embrulhada em JSON e sem dizer o que fazer.
    Quem lê isso na tela conclui que a IA está quebrada — quando o servidor ter
    respondido prova justamente o contrário: a parte difícil (instalar e subir o
    Ollama) já deu certo, e falta um comando de uma linha.
    """

    def _cliente_que_responde_404(self):  # noqa: ANN202
        import urllib.error
        import urllib.request

        from gt7ai.local import LocalClient, LocalEndpoint

        client = LocalClient(LocalEndpoint(model="qwen3:4b"))

        def falso_urlopen(*_args: object, **_kwargs: object) -> object:
            raise urllib.error.HTTPError(
                url="http://localhost:11434/v1/chat/completions",
                code=404,
                msg="Not Found",
                hdrs=None,  # type: ignore[arg-type]
                fp=io.BytesIO(
                    b'{"error":{"message":"model \'qwen3:4b\' not found",'
                    b'"type":"not_found_error","param":null,"code":null}}'
                ),
            )

        return client, falso_urlopen

    def test_diz_o_comando_que_resolve(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import urllib.request

        from gt7ai.client import AIUnavailable

        client, falso = self._cliente_que_responde_404()
        monkeypatch.setattr(urllib.request, "urlopen", falso)

        with pytest.raises(AIUnavailable) as erro:
            client.complete(request())

        mensagem = str(erro.value)
        assert "ollama pull qwen3:4b" in mensagem
        assert "não está instalado" in mensagem
        # E não o despejo de JSON que o usuário viu.
        assert "not_found_error" not in mensagem
        assert '{"error"' not in mensagem

    def test_outro_erro_do_servidor_continua_detalhado(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Só o 404 de modelo ausente ganha frase própria.

        Um 500 continua mostrando o corpo: ali o detalhe é a única pista, e
        inventar uma explicação amigável esconderia a causa real.
        """
        import urllib.error
        import urllib.request

        from gt7ai.client import AIUnavailable
        from gt7ai.local import LocalClient, LocalEndpoint

        client = LocalClient(LocalEndpoint(model="qwen3:4b"))

        def falso(*_args: object, **_kwargs: object) -> object:
            raise urllib.error.HTTPError(
                url="http://x", code=500, msg="Server Error",
                hdrs=None,  # type: ignore[arg-type]
                fp=io.BytesIO(b"algo explodiu no servidor"),
            )

        monkeypatch.setattr(urllib.request, "urlopen", falso)
        with pytest.raises(AIUnavailable) as erro:
            client.complete(request())

        assert "500" in str(erro.value)
        assert "algo explodiu" in str(erro.value)
