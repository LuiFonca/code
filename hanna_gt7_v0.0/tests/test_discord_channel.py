"""
Onde o bot escreve.

O comportamento anterior era pegar o **primeiro** canal de texto gravável em
**qualquer** servidor. Funcionava por acidente com um servidor e um canal, e
mandava o debrief para um lugar arbitrário em qualquer outra configuração: num
servidor com `#regras` antes de `#telemetria`, o relatório da sessão ia parar
nas regras.

O teste que carrega o módulo é `test_canal_inexistente_nao_cai_em_outro`. A
tentação de "ser prestativo" e usar o primeiro gravável quando o nome pedido não
existe reproduz exatamente o defeito — só que agora depois de a pessoa ter
configurado, o que é pior: ela tem motivo para acreditar que acertou.

Tudo aqui roda com servidores de mentira. A `discord.py` não precisa estar
instalada, e `select_channel` recebe os servidores por parâmetro justamente para
que a política seja verificável sem rede.
"""

from __future__ import annotations

from gt7core.config.settings import DiscordConfig
from gt7discord import select_channel


class FakePermissions:
    def __init__(self, *, send_messages: bool) -> None:
        self.send_messages = send_messages


class FakeChannel:
    def __init__(self, name: str, *, writable: bool = True) -> None:
        self.name = name
        self._writable = writable

    def permissions_for(self, _member: object) -> FakePermissions:
        return FakePermissions(send_messages=self._writable)

    def __repr__(self) -> str:
        return f"#{self.name}"


class FakeGuild:
    def __init__(self, name: str, channels: list[FakeChannel]) -> None:
        self.name = name
        self.text_channels = channels
        self.me = object()


def servidor(nome: str, *canais: str) -> FakeGuild:
    return FakeGuild(nome, [FakeChannel(c) for c in canais])


class TestSemConfiguracao:
    """Sem nome pedido, o comportamento antigo continua — é o padrão de quem
    nunca abriu a tela de configuração, e é razoável com um servidor só."""

    def test_pega_o_primeiro_gravavel(self) -> None:
        guilds = [servidor("Meu Servidor", "geral", "telemetria")]
        assert select_channel(guilds).name == "geral"

    def test_pula_canal_sem_permissao(self) -> None:
        guilds = [
            FakeGuild(
                "S",
                [FakeChannel("regras", writable=False), FakeChannel("geral")],
            )
        ]
        assert select_channel(guilds).name == "geral"

    def test_sem_servidor_nenhum_devolve_none(self) -> None:
        assert select_channel([]) is None


class TestCanalPedido:
    def test_encontra_o_canal_por_nome(self) -> None:
        """O defeito original: `#regras` vinha antes e vencia."""
        guilds = [servidor("Meu Servidor", "regras", "telemetria")]
        assert select_channel(guilds, channel_name="telemetria").name == "telemetria"

    def test_ignora_a_cerquilha_que_a_pessoa_copia(self) -> None:
        """Copiar da barra lateral do Discord traz o `#` junto."""
        guilds = [servidor("S", "regras", "telemetria")]
        assert select_channel(guilds, channel_name="#telemetria").name == "telemetria"

    def test_ignora_maiuscula(self) -> None:
        guilds = [servidor("S", "Telemetria")]
        assert select_channel(guilds, channel_name="telemetria") is not None

    def test_ignora_espaco_em_volta(self) -> None:
        guilds = [servidor("S", "telemetria")]
        assert select_channel(guilds, channel_name="  telemetria  ") is not None

    def test_canal_inexistente_nao_cai_em_outro(self) -> None:
        """O teste que justifica o módulo.

        A pessoa configurou `#telemetria` e errou uma letra. Publicar em
        `#geral` seria "prestativo" e reproduziria o defeito exato que esta
        função existe para corrigir — com o agravante de que agora ela tem
        motivo para acreditar que configurou certo. Silêncio com aviso no log é
        recuperável; publicar no lugar errado, não.
        """
        guilds = [servidor("S", "geral", "telemetria")]
        assert select_channel(guilds, channel_name="telemtria") is None

    def test_canal_certo_mas_sem_permissao_nao_vira_outro(self) -> None:
        guilds = [
            FakeGuild("S", [FakeChannel("telemetria", writable=False), FakeChannel("geral")])
        ]
        assert select_channel(guilds, channel_name="telemetria") is None


class TestServidorPedido:
    def test_escolhe_dentro_do_servidor_certo(self) -> None:
        """Com o bot em dois servidores, o primeiro canal do primeiro servidor
        era o que vencia — mesmo que o piloto quisesse o outro."""
        guilds = [
            servidor("Servidor de Trabalho", "geral"),
            servidor("Meu GT7", "telemetria"),
        ]
        escolhido = select_channel(guilds, guild_name="Meu GT7")
        assert escolhido.name == "telemetria"

    def test_servidor_e_canal_juntos(self) -> None:
        guilds = [
            servidor("Trabalho", "telemetria"),
            servidor("Meu GT7", "geral", "telemetria"),
        ]
        escolhido = select_channel(
            guilds, guild_name="Meu GT7", channel_name="telemetria"
        )
        assert escolhido is not None
        assert escolhido.name == "telemetria"
        # E não o `#telemetria` do servidor de trabalho, que vinha antes.
        assert escolhido is guilds[1].text_channels[1]

    def test_servidor_inexistente_devolve_none(self) -> None:
        guilds = [servidor("Meu GT7", "geral")]
        assert select_channel(guilds, guild_name="Outro") is None


class TestConfiguracao:
    def test_o_padrao_preserva_o_comportamento_antigo(self) -> None:
        """Vazio significa "qualquer" — quem nunca configurou não perde nada."""
        config = DiscordConfig()
        assert config.guild == ""
        assert config.channel == ""

    def test_o_env_alimenta_os_campos(self, tmp_path: object) -> None:
        from pathlib import Path

        from gt7core.config.settings import Settings

        assert isinstance(tmp_path, Path)
        env = tmp_path / ".env"
        env.write_text(
            "GT7_DISCORD_TOKEN=abc\n"
            "GT7_DISCORD_GUILD=Meu GT7\n"
            "GT7_DISCORD_CHANNEL=telemetria\n",
            encoding="utf-8",
        )
        discord = Settings.load(env_file=env).discord
        assert discord.guild == "Meu GT7"
        assert discord.channel == "telemetria"
