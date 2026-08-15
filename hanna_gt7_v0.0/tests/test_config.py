"""
Testes de configuração e mascaramento de segredo.

A auditoria registrou como P3 (crítico) que o IP da LAN doméstica do autor
estava hardcoded e versionado, e que não havia onde colocar token do Discord ou
chave de IA. R5 acrescentou o risco de vazar um segredo ao adicioná-los.

O teste de mascaramento é o que fecha R5: a forma mais comum de vazar uma chave
não é commitá-la, é imprimir o objeto de configuração num log de diagnóstico.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gt7core.config.settings import SecretStr, Settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove qualquer GT7_* do ambiente real para o teste ser hermético."""
    import os

    for key in [k for k in os.environ if k.startswith("GT7_")]:
        monkeypatch.delenv(key, raising=False)


class TestSecretStr:
    def test_repr_nao_revela_o_valor(self) -> None:
        secret = SecretStr("sk-ant-chave-super-secreta")

        assert "chave-super-secreta" not in repr(secret)
        assert "chave-super-secreta" not in str(secret)
        assert "chave-super-secreta" not in f"{secret}"

    def test_reveal_devolve_o_valor(self) -> None:
        assert SecretStr("valor").reveal() == "valor"

    def test_vazio_e_falsy(self) -> None:
        assert not SecretStr("")
        assert SecretStr("x")

    def test_nao_vaza_em_mensagem_de_excecao(self) -> None:
        """Um traceback que inclua o objeto não pode carregar a chave."""
        secret = SecretStr("token-do-discord")
        message = f"falha ao autenticar com {secret}"

        assert "token-do-discord" not in message


class TestPrecedencia:
    def test_padroes_nao_tem_ip_nem_segredo(self) -> None:
        """O padrão não aponta para a rede de ninguém e não liga nada."""
        settings = Settings.load(env_file=Path("/nao/existe/.env"))

        assert settings.telemetry.ps_ip == ""
        assert settings.telemetry.source == "mock"
        assert settings.discord.enabled is False
        assert not settings.ai.api_key
        # A IA vem ligada, mas no provedor **local**: nada sai da máquina e
        # nada é cobrado. O que este teste protege é "o padrão não fala com o
        # mundo", e essa propriedade continua valendo.
        assert settings.ai.is_local
        assert settings.ai.local_url.startswith("http://localhost")

    def test_ambiente_sobrescreve_o_padrao(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GT7_PS_IP", "10.0.0.42")
        monkeypatch.setenv("GT7_TELEMETRY_SOURCE", "udp")

        settings = Settings.load(env_file=Path("/nao/existe/.env"))

        assert settings.telemetry.ps_ip == "10.0.0.42"
        assert settings.telemetry.source == "udp"

    def test_arquivo_env_e_lido(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# comentário ignorado\n"
            "GT7_PS_IP=192.168.1.99\n"
            "\n"
            'GT7_AI_MODEL="claude-opus-5"\n'
        )

        settings = Settings.load(env_file=env_file)

        assert settings.telemetry.ps_ip == "192.168.1.99"
        assert settings.ai.model == "claude-opus-5"

    def test_ambiente_ganha_do_arquivo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Produção e CI sobrescrevem sem editar arquivo."""
        env_file = tmp_path / ".env"
        env_file.write_text("GT7_PS_IP=1.1.1.1\n")
        monkeypatch.setenv("GT7_PS_IP", "2.2.2.2")

        assert Settings.load(env_file=env_file).telemetry.ps_ip == "2.2.2.2"

    def test_valor_numerico_invalido_cai_para_o_padrao(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Configuração ruim não pode derrubar o app na inicialização."""
        monkeypatch.setenv("GT7_RECEIVE_PORT", "não-é-número")

        assert Settings.load(env_file=Path("/nao/existe")).telemetry.receive_port == 33740


class TestAtivacaoDeModulos:
    def test_ia_liga_quando_ha_chave(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GT7_AI_API_KEY", "sk-ant-exemplo")

        settings = Settings.load(env_file=Path("/nao/existe"))

        assert settings.ai.enabled is True
        assert settings.ai.api_key.reveal() == "sk-ant-exemplo"

    def test_sem_chave_a_ia_e_local_e_nao_a_nuvem(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """§49 continua valendo, com a política invertida.

        Antes: sem chave, IA desligada. Agora: sem chave, IA **local** — porque
        o provedor local é gratuito, offline e degrada para a análise da Fase 4
        se o servidor não estiver de pé. Ligá-lo não gasta nem expõe nada.

        O que não pode acontecer, e é o que este teste guarda, é a nuvem subir
        sem chave: aí "ligada" significaria uma chamada que falha toda vez.
        """
        monkeypatch.setenv("GT7_AI_ENABLED", "true")
        monkeypatch.delenv("GT7_AI_API_KEY", raising=False)

        ai = Settings.load(env_file=Path("/nao/existe")).ai
        assert ai.is_local
        assert ai.enabled is True

        monkeypatch.setenv("GT7_AI_PROVIDER", "anthropic")
        assert Settings.load(env_file=Path("/nao/existe")).ai.enabled is False

    def test_discord_liga_quando_ha_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GT7_DISCORD_TOKEN", "token-exemplo")

        assert Settings.load(env_file=Path("/nao/existe")).discord.enabled is True


class TestDescribe:
    def test_describe_mascara_segredos(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """O que fecha R5: a serialização usada em log nunca traz o valor real."""
        monkeypatch.setenv("GT7_AI_API_KEY", "sk-ant-nao-pode-vazar")
        monkeypatch.setenv("GT7_DISCORD_TOKEN", "discord-nao-pode-vazar")

        rendered = repr(Settings.load(env_file=Path("/nao/existe")).describe())

        assert "nao-pode-vazar" not in rendered
        assert "***" in rendered

    def test_describe_mantem_o_resto_legivel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GT7_PS_IP", "10.0.0.7")

        described = Settings.load(env_file=Path("/nao/existe")).describe()

        assert described["telemetry"]["ps_ip"] == "10.0.0.7"  # type: ignore[index]


class TestRetencao:
    def test_retencao_por_pista_e_configuravel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Decisão do usuário: histórico limitado por pista (ex.: 20 voltas).
        Deixou de ser constante enterrada no módulo de banco (P8)."""
        monkeypatch.setenv("GT7_KEEP_RECENT_PER_TRACK", "20")

        settings = Settings.load(env_file=Path("/nao/existe"))

        assert settings.storage.keep_recent_per_track == 20
        assert settings.storage.keep_best_per_track == 5
