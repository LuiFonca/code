"""
Os dois pontos de entrada que o usuário toca primeiro.

`gt7core.demo` é o comando de abertura do README, e `gt7core.tools.diagnose` é o
que ele roda quando a telemetria não chega. Os dois estavam sem teste nenhum —
`demo.py` com **0%** de cobertura —, e são justamente os que, quebrados, dão a
primeira impressão de que o programa não funciona.

O veredito do diagnóstico é o caso mais interessante: a função inteira existe
para **explicar** um problema de rede a quem não sabe depurar rede. Se ela
classificar errado, manda a pessoa mexer no firewall quando o problema é o jogo
estar no menu. É pura — recebe contagens, devolve texto e código de saída — e
portanto verificável sem tocar em socket nenhum.
"""

from __future__ import annotations

import contextlib
import errno
import io

import pytest


class TestDemo:
    """O comando de abertura do README."""

    def test_roda_uma_sessao_inteira(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Fumaça de ponta a ponta: fonte → motor → eventos → análise.

        Sem isto, uma quebra no primeiro comando que qualquer pessoa roda só
        apareceria quando ela rodasse.
        """
        from gt7core.demo import main

        assert main(["--laps", "2"]) == 0

        saida = capsys.readouterr().out
        assert "HANNA GT7" in saida
        assert "Volta" in saida
        # As quatro curvas do perfil sintético — a mesma verdade conhecida que
        # os testes da Fase 4 usam.
        assert "4 curvas detectadas" in saida

    def test_nao_carrega_qt(self) -> None:
        """A demo é a prova viva de que o núcleo roda headless.

        Ela **imprime** se o Qt está carregado, e num ambiente onde o PySide6
        está instalado (como o de teste) o valor precisa continuar sendo "não":
        é a diferença entre estar disponível e ter sido arrastado para dentro.

        Precisa ser em subprocesso. `'PySide6' in sys.modules` é global do
        processo, e a suíte tem testes de interface que carregam o Qt de
        verdade; chamando `main()` aqui dentro, o resultado dependeria da ordem
        dos arquivos — passaria sozinho e falharia na suíte inteira, que é o
        pior tipo de teste. De quebra, isto roda o comando literal que o README
        manda rodar, do jeito que a pessoa vai rodar.
        """
        import subprocess
        import sys
        from pathlib import Path

        raiz = Path(__file__).resolve().parent.parent
        resultado = subprocess.run(
            [sys.executable, "-m", "gt7core.demo", "--laps", "2"],
            cwd=raiz,
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert resultado.returncode == 0, resultado.stderr
        assert "Qt carregado   não" in resultado.stdout

    def test_o_relatorio_de_perda_aparece(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A saída que o README promete: onde a volta foi perdida.

        O bloco é condicional de propósito — se a última volta **foi** a melhor,
        não há o que explicar. O piloto sintético melhora volta a volta, então o
        número aqui não é decorativo: em 4 voltas ele deixa escapar a última e o
        bloco sai; em 3 ou 6 a última é a melhor e o bloco (corretamente) some.
        """
        from gt7core.demo import main

        main(["--laps", "4"])
        saida = capsys.readouterr().out
        assert "ONDE A VOLTA" in saida
        assert "Diferença total" in saida
        # A atribuição por curva é o produto da Fase 4; sem ela o bloco é só um
        # número de delta, que o próprio jogo já mostra.
        assert "Curva 1:" in saida

    def test_ultima_volta_melhor_nao_gera_relatorio_de_perda(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """O outro lado da condição: nada a explicar quando não se perdeu nada.

        Imprimir "onde a volta foi perdida" logo depois da melhor volta da
        sessão seria o tipo de ruído que faz a pessoa parar de ler a saída.
        """
        from gt7core.demo import main

        main(["--laps", "3"])
        assert "ONDE A VOLTA" not in capsys.readouterr().out

    def test_uma_volta_so_nao_estoura(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Sem segunda volta não há comparação — e isso não pode ser um erro."""
        from gt7core.demo import main

        assert main(["--laps", "1"]) == 0


class TestVereditoDoDiagnostico:
    """A função que explica um problema de rede a quem não depura rede.

    Classificar errado manda a pessoa mexer no firewall quando o problema é o
    jogo estar no menu — pior que não dizer nada, porque gasta o tempo dela na
    direção errada.
    """

    def _verdict(self, **kwargs: object) -> tuple[int, str]:
        from gt7core.tools.diagnose import _verdict

        defaults: dict[str, object] = {
            "sent": 0,
            "valid": 0,
            "invalid": 0,
            "send_errors": {},
        }
        defaults.update(kwargs)

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = _verdict(**defaults)  # type: ignore[arg-type]
        return code, buffer.getvalue()

    def test_pacote_valido_e_sucesso(self) -> None:
        """O único caso que devolve 0, e o único que aponta para a interface."""
        code, saida = self._verdict(sent=20, valid=1180)
        assert code == 0
        assert "FUNCIONANDO" in saida
        assert "interface e não na rede" in saida

    def test_sem_rota_ate_o_console(self) -> None:
        """Nenhum toque saiu: o problema é rede, não o jogo."""
        code, saida = self._verdict(
            sent=0, send_errors={f"[{errno.EHOSTUNREACH}] No route to host": 20}
        )
        assert code == 1
        assert "SEM ROTA" in saida
        assert "MESMA rede" in saida
        # Não deve sugerir que o jogo está no menu — a causa é outra.
        assert "track day" not in saida

    def test_rede_inalcancavel_tambem_conta(self) -> None:
        code, saida = self._verdict(
            sent=0, send_errors={f"[{errno.ENETUNREACH}] Network unreachable": 5}
        )
        assert "SEM ROTA" in saida
        assert code == 1

    def test_toques_sairam_mas_nada_voltou(self) -> None:
        """O caso mais comum de todos: o GT7 está no menu.

        A rede está boa; o jogo é que não transmite fora de uma sessão.
        """
        code, saida = self._verdict(sent=20, valid=0, invalid=0)
        assert code == 1
        assert "track day" in saida
        assert "SEM ROTA" not in saida

    def test_pacotes_de_outro_servico(self) -> None:
        """Chegou coisa na porta, mas não é do GT7."""
        code, saida = self._verdict(sent=20, valid=0, invalid=45)
        assert code == 1
        assert "outro serviço" in saida

    def test_um_erro_de_envio_que_nao_e_de_rota_nao_vira_sem_rota(self) -> None:
        """A classificação exige que **todos** os erros sejam de rota.

        Um erro de permissão misturado muda o diagnóstico: no macOS, a
        permissão de Rede Local negada não é "sem rota", e mandar a pessoa
        conferir a faixa de IP a levaria para o lado errado.
        """
        code, saida = self._verdict(
            sent=3,
            send_errors={
                f"[{errno.EHOSTUNREACH}] No route to host": 2,
                f"[{errno.EACCES}] Permission denied": 1,
            },
        )
        assert code == 1
        assert "SEM ROTA" not in saida

    def test_nada_saiu_e_nada_chegou(self) -> None:
        code, saida = self._verdict(sent=0, valid=0, invalid=0)
        assert code == 1
        assert "Confira se a máquina tem rede" in saida

    def test_as_contagens_aparecem_sempre(self) -> None:
        """Quem pede ajuda cola esta saída; ela precisa trazer os números."""
        _code, saida = self._verdict(sent=7, valid=0, invalid=2)
        assert "toques enviados  : 7" in saida
        assert "pacotes válidos  : 0" in saida
        assert "pacotes inválidos: 2" in saida
