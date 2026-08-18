"""
A página de Configurações.

Ela nasceu de um relato: *"o aplicativo tá funcionando cheio de dados mocados,
não funciona a conexão com o PS5"*. Não havia conexão falhando — a fonte
sintética é o padrão e não existia caminho pela interface para trocá-la. Quem
usava fazia tudo certo e via dados inventados.

Estes testes cobrem a política do formulário, que é onde moram os defeitos que
custam caro: salvar sem persistir, salvar sem aplicar, e apagar o token de quem
só queria trocar de canal. O que eles **não** cobrem é a aparência — para isso
a página foi renderizada, e foi o desenho (não o código) que revelou que sem
`QScrollArea` o botão de salvar ficava fora da tela num laptop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gt7core.config.settings import Settings

pytest.importorskip("PySide6", reason="a página é Qt")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gt7app.application import build_core  # noqa: E402
from gt7app.design.tokens import get_theme  # noqa: E402
from gt7app.pages.settings import SOURCE_VALUES, SettingsPage  # noqa: E402


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(app: QApplication, tmp_path: Path):  # noqa: ANN201, ARG001
    settings = Settings()
    settings.storage.database_path = tmp_path / "t.db"
    settings.storage.telemetry_path = tmp_path / "tel"
    settings.env_path = tmp_path / ".env"

    core = build_core(settings)
    page = SettingsPage(core, get_theme("dark"))
    yield page
    page.close_page()
    core.close()


class TestPersistencia:
    def test_salvar_grava_no_env(self, page: SettingsPage) -> None:
        """Sem isto a tela é teatro: aplica no processo e esquece ao fechar."""
        page._source.setCurrentText(SOURCE_VALUES["udp"])  # noqa: SLF001
        page._ps_ip.setText("192.168.1.50")  # noqa: SLF001
        page._on_save()  # noqa: SLF001

        gravado = page.core.settings.env_path.read_text(encoding="utf-8")
        assert "GT7_TELEMETRY_SOURCE=udp" in gravado
        assert "GT7_PS_IP=192.168.1.50" in gravado

    def test_o_que_foi_salvo_volta_ao_recarregar(self, page: SettingsPage) -> None:
        """A prova de que a tela e o carregador falam a mesma língua."""
        page._source.setCurrentText(SOURCE_VALUES["udp"])  # noqa: SLF001
        page._ps_ip.setText("10.0.0.7")  # noqa: SLF001
        page._on_save()  # noqa: SLF001

        recarregado = Settings.load(env_file=page.core.settings.env_path)
        assert recarregado.telemetry.source == "udp"
        assert recarregado.telemetry.ps_ip == "10.0.0.7"

    def test_salvar_aplica_no_nucleo_vivo(self, page: SettingsPage) -> None:
        """Trocar de fonte não pode exigir reiniciar o programa."""
        antes = page.core.source
        page._source.setCurrentText(SOURCE_VALUES["udp"])  # noqa: SLF001
        page._ps_ip.setText("192.168.1.50")  # noqa: SLF001
        page._on_save()  # noqa: SLF001

        assert page.core.settings.telemetry.source == "udp"
        assert page.core.source is not antes
        assert type(page.core.source).__name__ == "Gt7UdpTelemetrySource"
        page.core.source.stop()

    def test_ip_invalido_nao_derruba_a_tela(self, page: SettingsPage) -> None:
        """"PS5 na rede" com IP em branco é o erro mais provável do formulário.

        A fonte antiga continua valendo e a tela explica — em vez de estourar
        uma exceção por cima de um clique em Salvar.
        """
        antes = page.core.source
        page._source.setCurrentText(SOURCE_VALUES["udp"])  # noqa: SLF001
        page._ps_ip.setText("")  # noqa: SLF001
        page._on_save()  # noqa: SLF001

        assert page.core.source is antes, "ficou sem captura por um campo vazio"
        assert "não trocou" in page._save_status.text()  # noqa: SLF001


class TestToken:
    def test_campo_em_branco_preserva_o_token(self, page: SettingsPage) -> None:
        """Quem só quer trocar o canal não pode perder o token no caminho.

        O campo nasce vazio (o segredo nunca volta para a tela), então gravar
        seu conteúdo literal desconectaria o bot de quem mexeu em outra coisa.
        """
        page._discord_channel.setText("telemetria")  # noqa: SLF001
        page._on_save()  # noqa: SLF001

        gravado = page.core.settings.env_path.read_text(encoding="utf-8")
        assert "GT7_DISCORD_CHANNEL=telemetria" in gravado
        assert "GT7_DISCORD_TOKEN" not in gravado

    def test_token_digitado_e_gravado(self, page: SettingsPage) -> None:
        page._discord_token.setText("segredo-do-bot")  # noqa: SLF001
        page._on_save()  # noqa: SLF001

        gravado = page.core.settings.env_path.read_text(encoding="utf-8")
        assert "GT7_DISCORD_TOKEN=segredo-do-bot" in gravado

    def test_o_token_nunca_aparece_no_campo(self, tmp_path: Path, app: QApplication) -> None:
        """`SecretStr` não adianta se a tela reexibe o valor: um campo
        preenchido é copiável, e o segredo passa a depender de ninguém olhar."""
        env = tmp_path / ".env"
        env.write_text("GT7_DISCORD_TOKEN=super-secreto\n", encoding="utf-8")

        settings = Settings.load(env_file=env)
        settings.storage.database_path = tmp_path / "t.db"
        settings.storage.telemetry_path = tmp_path / "tel"
        core = build_core(settings)
        try:
            page = SettingsPage(core, get_theme("dark"))
            assert page._discord_token.text() == ""  # noqa: SLF001
            assert "super-secreto" not in page._discord_token.placeholderText()  # noqa: SLF001
            page.close_page()
        finally:
            core.close()


class TestCamposPorFonte:
    def test_ip_desligado_na_fonte_sintetica(self, page: SettingsPage) -> None:
        """Campo irrelevante ligado sugere que preenchê-lo faria diferença."""
        page._source.setCurrentText(SOURCE_VALUES["mock"])  # noqa: SLF001
        assert not page._ps_ip.isEnabled()  # noqa: SLF001
        assert not page._test_button.isEnabled()  # noqa: SLF001
        assert page._mock_speed.isEnabled()  # noqa: SLF001

    def test_ip_ligado_no_ps5(self, page: SettingsPage) -> None:
        page._source.setCurrentText(SOURCE_VALUES["udp"])  # noqa: SLF001
        assert page._ps_ip.isEnabled()  # noqa: SLF001
        assert page._test_button.isEnabled()  # noqa: SLF001

    def test_a_tela_comeca_no_estado_atual(self, page: SettingsPage) -> None:
        assert page._source.currentText() == SOURCE_VALUES["mock"]  # noqa: SLF001


class TestTesteDeConexao:
    def test_sem_ip_o_botao_explica_em_vez_de_sondar(self, page: SettingsPage) -> None:
        page._source.setCurrentText(SOURCE_VALUES["udp"])  # noqa: SLF001
        page._ps_ip.setText("")  # noqa: SLF001
        page._on_test()  # noqa: SLF001
        assert "Digite o IP" in page._test_result.text()  # noqa: SLF001

    def test_o_veredito_chega_na_tela(self, page: SettingsPage) -> None:
        """A sondagem real exige rede; o que se verifica é o caminho de volta.

        O veredito vem de `diagnose_counts`, que é puro e já tem oito testes
        próprios — aqui só importa que o texto chegue ao rótulo e que o botão
        volte a funcionar.
        """
        from gt7core.tools.diagnose import Diagnosis

        page._test_button.setEnabled(False)  # noqa: SLF001
        page._on_probe_done(  # noqa: SLF001
            Diagnosis(ok=False, headline="SEM ROTA até o console.", steps=("VPN?",))
        )

        assert "SEM ROTA" in page._test_result.text()  # noqa: SLF001
        assert "VPN?" in page._test_result.text()  # noqa: SLF001
        assert page._test_button.isEnabled(), "o botão ficaria morto após um teste"  # noqa: SLF001


class TestAvisoDeAmbiente:
    def test_avisa_quando_a_variavel_vence_o_arquivo(
        self, page: SettingsPage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Salvar corretamente e não surtir efeito é o pior desfecho possível:
        a pessoa conclui que o programa está quebrado."""
        monkeypatch.setenv("GT7_PS_IP", "1.2.3.4")
        page._show_environment_warning()  # noqa: SLF001

        assert page._env_warning.isVisible() or page._env_warning.text()  # noqa: SLF001
        assert "GT7_PS_IP" in page._env_warning.text()  # noqa: SLF001

    def test_sem_variavel_nao_polui_a_tela(self, page: SettingsPage) -> None:
        page._show_environment_warning()  # noqa: SLF001
        assert not page._env_warning.isVisible()  # noqa: SLF001
