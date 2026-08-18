"""
Gravação da configuração — a metade que faltava do `.env`.

O risco aqui não é perder um valor: é destruir o arquivo do usuário. O
`.env.example` tem 98 linhas, quase todas comentário explicando por que cada
opção existe, e um "salvar" que reescreve tudo a partir do estado atual apagaria
essa documentação inteira no primeiro clique — silenciosamente, e sem desfazer.

Por isso a maior parte destes testes verifica o que **não** mudou.
"""

from __future__ import annotations

import os
from pathlib import Path

from gt7core.config.persistence import (
    HEADER,
    env_key_for,
    overridden_by_environment,
    save_env,
    update_env_text,
)
from gt7core.config.settings import Settings

EXEMPLO = """\
# HANNA GT7 — configuração de exemplo

# ---------- TELEMETRIA ----------
# mock = gerador sintético (padrão; roda sem PS5)
GT7_TELEMETRY_SOURCE=mock

# IP do PlayStation. Sem padrão de propósito.
GT7_PS_IP=
GT7_SEND_PORT=33739

# ---------- VOZ ----------
GT7_VOICE_ENABLED=false
# Nome da voz do sistema. Vazio usa a padrão.
# GT7_VOICE_NAME=Luciana
"""


class TestPreservacao:
    """O arquivo é do usuário. Mexemos na linha da chave e em mais nada."""

    def test_muda_o_valor_sem_mover_a_linha(self) -> None:
        saida = update_env_text(EXEMPLO, {"GT7_PS_IP": "192.168.1.50"})

        linhas = saida.splitlines()
        assert "GT7_PS_IP=192.168.1.50" in linhas
        # Continua logo depois do comentário que a explica, e não no rodapé.
        assert linhas[linhas.index("GT7_PS_IP=192.168.1.50") - 1].startswith(
            "# IP do PlayStation"
        )

    def test_nao_perde_um_comentario_sequer(self) -> None:
        """O teste que justifica o módulo inteiro existir."""
        antes = [ln for ln in EXEMPLO.splitlines() if ln.strip().startswith("#")]
        depois = update_env_text(
            EXEMPLO, {"GT7_PS_IP": "10.0.0.2", "GT7_TELEMETRY_SOURCE": "udp"}
        ).splitlines()

        # Todo comentário sobrevive, menos o que virou atribuição de propósito
        # (ver test_descomenta_no_lugar).
        for comentario in antes:
            if comentario.strip().lstrip("#").strip().startswith("GT7_"):
                continue
            assert comentario in depois, f"comentário perdido: {comentario}"

    def test_nao_toca_no_que_nao_foi_pedido(self) -> None:
        saida = update_env_text(EXEMPLO, {"GT7_PS_IP": "10.0.0.2"})
        assert "GT7_SEND_PORT=33739" in saida
        assert "GT7_VOICE_ENABLED=false" in saida

    def test_descomenta_no_lugar(self) -> None:
        """`# GT7_VOICE_NAME=Luciana` está pronta para uso — use aquela linha.

        A alternativa seria deixar a versão comentada no meio do arquivo e a
        viva no rodapé, e quem abrisse o `.env` veria a mesma chave duas vezes
        sem saber qual vale.
        """
        saida = update_env_text(EXEMPLO, {"GT7_VOICE_NAME": "Felipe"})

        assert "GT7_VOICE_NAME=Felipe" in saida
        assert "# GT7_VOICE_NAME=Luciana" not in saida
        assert saida.count("GT7_VOICE_NAME") == 1

    def test_chave_nova_vai_para_o_fim_sob_cabecalho(self) -> None:
        saida = update_env_text(EXEMPLO, {"GT7_DISCORD_CHANNEL": "telemetria"})
        assert HEADER in saida
        assert saida.strip().endswith("GT7_DISCORD_CHANNEL=telemetria")

    def test_salvar_duas_vezes_nao_empilha_cabecalho(self) -> None:
        """Sem isto, cada clique em Salvar acrescentaria uma seção nova."""
        uma = update_env_text(EXEMPLO, {"GT7_DISCORD_CHANNEL": "a"})
        duas = update_env_text(uma, {"GT7_DISCORD_CHANNEL": "b"})

        assert duas.count(HEADER) == 1
        assert duas.count("GT7_DISCORD_CHANNEL") == 1
        assert "GT7_DISCORD_CHANNEL=b" in duas

    def test_arquivo_vazio_vira_arquivo_valido(self) -> None:
        saida = update_env_text("", {"GT7_PS_IP": "10.0.0.2"})
        assert "GT7_PS_IP=10.0.0.2" in saida
        assert saida.endswith("\n")

    def test_sem_mudanca_o_texto_e_identico(self) -> None:
        assert update_env_text(EXEMPLO, {}) == EXEMPLO

    def test_valor_vazio_e_gravado_explicitamente(self) -> None:
        """Limpar o IP pela tela precisa apagar o valor, não virar no-op."""
        saida = update_env_text("GT7_PS_IP=10.0.0.2\n", {"GT7_PS_IP": ""})
        assert "GT7_PS_IP=" in saida
        assert "10.0.0.2" not in saida

    def test_espaco_em_volta_do_igual_e_reconhecido(self) -> None:
        saida = update_env_text("GT7_PS_IP = 10.0.0.2\n", {"GT7_PS_IP": "10.0.0.9"})
        assert saida.count("GT7_PS_IP") == 1
        assert "GT7_PS_IP=10.0.0.9" in saida


class TestIdaEVolta:
    """O que a tela grava, o carregador precisa reler igual."""

    def test_o_que_foi_salvo_volta_no_settings(self, tmp_path: Path) -> None:
        """A prova de que escritor e leitor concordam.

        Um escritor mais esperto que o leitor — com aspas, por exemplo —
        produziria arquivos que o programa não consegue reler.
        """
        env = tmp_path / ".env"
        env.write_text(EXEMPLO, encoding="utf-8")

        save_env(env, {"GT7_TELEMETRY_SOURCE": "udp", "GT7_PS_IP": "192.168.1.50"})

        settings = Settings.load(env_file=env)
        assert settings.telemetry.source == "udp"
        assert settings.telemetry.ps_ip == "192.168.1.50"

    def test_cria_o_arquivo_quando_nao_existe(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        save_env(env, {"GT7_PS_IP": "10.0.0.2"})

        assert env.is_file()
        assert Settings.load(env_file=env).telemetry.ps_ip == "10.0.0.2"


class TestAvisoDeAmbiente:
    """O pior modo de falha de uma tela de configuração."""

    def test_detecta_a_variavel_que_vence_o_arquivo(self) -> None:
        """Salvar corretamente e não surtir efeito é pior que falhar ao salvar.

        A precedência ambiente > arquivo está certa, mas sem aviso o usuário
        digita o IP, salva, nada muda, e conclui que o programa está quebrado.
        """
        os.environ["GT7_PS_IP"] = "1.2.3.4"
        try:
            assert overridden_by_environment(["GT7_PS_IP"]) == ["GT7_PS_IP"]
        finally:
            del os.environ["GT7_PS_IP"]

    def test_sem_variavel_nao_avisa(self) -> None:
        assert overridden_by_environment(["GT7_PS_IP"]) == []

    def test_variavel_vazia_nao_conta(self) -> None:
        """`export GT7_PS_IP=` não sobrepõe nada — o carregador usa `or`."""
        os.environ["GT7_PS_IP"] = ""
        try:
            assert overridden_by_environment(["GT7_PS_IP"]) == []
        finally:
            del os.environ["GT7_PS_IP"]

    def test_nome_da_chave(self) -> None:
        assert env_key_for("ps_ip") == "GT7_PS_IP"
