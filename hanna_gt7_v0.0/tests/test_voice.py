"""
Fase 11 — o rádio falado.

Áudio não é verificável num teste automatizado, e é exatamente por isso que a
fronteira existe. Com `Speaker` no meio, a **política** — o que vira fala, o que
é engolido, o que é cortado — é Python puro e verificável; o que sobra do outro
lado é lançar um processo com uma linha de comando, e essa linha é montada por
uma função pura que os testes conferem para os três sistemas operacionais.

O que **não** é verificado aqui, e nenhum teste poderia verificar: se a voz sai
audível, se a pronúncia do português está correta, se o volume está razoável.
Isso exige ouvir, numa máquina com placa de som.
"""

from __future__ import annotations

import pytest

from gt7core.config.settings import VoiceConfig
from gt7voice import (
    SPOKEN_LEVELS,
    NullSpeaker,
    RecordingSpeaker,
    SpeechUnavailable,
    VoiceRadio,
    build_command,
    build_speaker,
    detect_engine,
)


def radio(**overrides: object) -> tuple[VoiceRadio, RecordingSpeaker]:
    speaker = RecordingSpeaker()
    config = VoiceConfig(enabled=True)
    for name, value in overrides.items():
        setattr(config, name, value)
    return VoiceRadio(speaker, config), speaker


def advice(level: str = "quick", headline: str = "Freie mais tarde na Curva 1."):  # noqa: ANN201
    from gt7ai import Advice, AdviceLevel

    return Advice(level=AdviceLevel(level), headline=headline)


class TestComandoDoSistema:
    """A linha de comando de cada plataforma — o defeito provável aqui."""

    def test_macos_usa_say_com_ritmo_e_voz(self) -> None:
        config = VoiceConfig(rate_wpm=220, voice="Luciana")
        assert build_command("say", "olá", config) == [
            "say", "-r", "220", "-v", "Luciana", "olá",
        ]

    def test_sem_voz_escolhida_usa_a_do_sistema(self) -> None:
        """A padrão segue o idioma da máquina — numa brasileira, já é português."""
        assert "-v" not in build_command("say", "olá", VoiceConfig())

    def test_windows_escapa_apostrofo(self) -> None:
        """PowerShell escapa aspa simples duplicando-a.

        Sem isso, "não está" — ou qualquer conselho com apóstrofo — quebraria o
        comando e o rádio emudeceria justamente nas frases mais naturais.
        """
        command = build_command("sapi", "o carro n'está pronto", VoiceConfig())
        script = command[-1]
        assert "n''está" in script
        assert command[0] == "powershell"

    def test_windows_converte_o_ritmo_para_a_escala_da_sapi(self) -> None:
        """SAPI usa -10..10, não palavras por minuto."""
        lento = build_command("sapi", "x", VoiceConfig(rate_wpm=100))[-1]
        rapido = build_command("sapi", "x", VoiceConfig(rate_wpm=300))[-1]
        assert "$s.Rate = -4" in lento
        assert "$s.Rate = 4" in rapido

    def test_a_escala_da_sapi_satura_no_teto(self) -> None:
        """Só o teto é alcançável, e a assimetria é consequência da fórmula.

        `(wpm - 200) / 25` com `wpm` positivo chega no máximo a -8; o piso -10
        exigiria ritmo negativo. A primeira versão deste teste afirmava -10 para
        `rate_wpm=1` e falhou — a fórmula estava certa e a expectativa, errada.
        """
        assert "$s.Rate = -8" in build_command("sapi", "x", VoiceConfig(rate_wpm=0))[-1]
        assert "$s.Rate = 10" in build_command(
            "sapi", "x", VoiceConfig(rate_wpm=9999)
        )[-1]

    def test_linux_pede_portugues_do_brasil(self) -> None:
        """`pt` é o europeu, e a diferença é audível o bastante para incomodar."""
        assert "pt-br" in build_command("espeak-ng", "olá", VoiceConfig())

    def test_motor_desconhecido_e_erro_explicado(self) -> None:
        with pytest.raises(SpeechUnavailable):
            build_command("inexistente", "olá", VoiceConfig())

    @pytest.mark.parametrize(
        ("plataforma", "esperado"),
        [("win32", "sapi"), ("cygwin", "sapi")],
    )
    def test_deteccao_por_plataforma(self, plataforma: str, esperado: str) -> None:
        """Injetável para verificar os três sistemas a partir de um só."""
        assert detect_engine(plataforma) == esperado

    def test_sem_sintetizador_o_programa_roda_em_silencio(self) -> None:
        """Máquina sem TTS não pode deixar de abrir."""
        speaker = build_speaker(VoiceConfig(enabled=False))
        assert isinstance(speaker, NullSpeaker)
        speaker.say("nada acontece")
        speaker.stop()


class TestPoliticaDeFala:
    def test_fala_a_nota_de_radio(self) -> None:
        voz, speaker = radio()
        assert voz.announce(advice()) is True
        assert speaker.last == "Freie mais tarde na Curva 1."

    def test_nao_fala_o_debrief(self) -> None:
        """Ler quatro parágrafos em movimento é pior que silêncio.

        Ocupa o canal por meio minuto e o piloto não retém nada.
        """
        voz, speaker = radio()
        assert voz.announce(advice(level="debrief")) is False
        assert voz.announce(advice(level="session")) is False
        assert speaker.spoken == []
        assert frozenset({"quick"}) == SPOKEN_LEVELS

    def test_desligada_nao_fala(self) -> None:
        voz, speaker = radio(enabled=False)
        assert voz.announce(advice()) is False
        assert speaker.spoken == []

    def test_nao_repete_a_mesma_frase(self) -> None:
        """Repetição soa como defeito e o piloto para de escutar."""
        voz, speaker = radio()
        voz.announce(advice())
        voz.announce(advice())
        assert len(speaker.spoken) == 1

    def test_frase_diferente_volta_a_falar(self) -> None:
        voz, speaker = radio()
        voz.announce(advice())
        voz.announce(advice(headline="Abra o acelerador no ápice."))
        assert len(speaker.spoken) == 2

    def test_conselho_sem_texto_e_ignorado(self) -> None:
        voz, speaker = radio()
        assert voz.announce(advice(headline="   ")) is False
        assert speaker.spoken == []

    def test_fala_conselho_local_tambem(self) -> None:
        """A nota da análise da Fase 4 é tão falável quanto a do modelo.

        `gt7voice` lê por `getattr` e não importa `gt7ai` — a voz precisa
        funcionar num programa montado sem o plugin de IA.
        """
        voz, speaker = radio()

        class Improvisado:
            headline = "Travou de novo na Curva 1."

            def speech(self) -> str:
                return self.headline

        assert voz.announce(Improvisado()) is True
        assert speaker.last == "Travou de novo na Curva 1."


class TestCorte:
    def test_corta_ao_orcamento_de_tempo(self) -> None:
        """Uma fala longa ocupa o rádio enquanto três curvas passam."""
        voz, speaker = radio(rate_wpm=200, max_seconds=3.0)
        # 200 wpm × 3 s = 10 palavras de orçamento.
        assert voz.word_budget == pytest.approx(10.0)

        longo = " ".join(f"palavra{i}" for i in range(40))
        voz.say(longo)
        assert len(speaker.last.split()) <= 10

    def test_prefere_terminar_numa_frase(self) -> None:
        """Cortar no meio soa como falha de equipamento."""
        voz, speaker = radio(rate_wpm=200, max_seconds=3.0)
        voz.say(
            "Freie dez metros mais tarde na Curva um. "
            "Depois abra o acelerador progressivamente até a reta seguinte."
        )
        assert speaker.last.endswith(".")

    def test_texto_curto_passa_inteiro(self) -> None:
        voz, speaker = radio()
        voz.say("Freie mais tarde.")
        assert speaker.last == "Freie mais tarde."

    def test_ritmo_absurdo_nao_zera_o_orcamento(self) -> None:
        """Um `rate_wpm` mal configurado não pode emudecer o rádio."""
        voz, speaker = radio(rate_wpm=0, max_seconds=8.0)
        voz.say("Freie mais tarde na Curva 1.")
        assert speaker.last


class TestInterrupcao:
    def test_calar_interrompe_e_esquece(self) -> None:
        """Ao parar a captura, o que estava sendo dito deixa de valer."""
        voz, speaker = radio()
        voz.announce(advice())
        voz.silence()
        assert speaker.stops == 1

        # Depois do silêncio, a mesma frase volta a ser dita: o contexto mudou.
        voz.announce(advice())
        assert len(speaker.spoken) == 2

    def test_o_sintetizador_corta_antes_de_falar(self) -> None:
        """Nota nova interrompe a anterior — não há fila.

        Enfileirar significaria falar sobre a Curva 1 quando o piloto já está
        na 3, e conselho fora de hora não é neutro: manda corrigir a curva
        errada. A ordem `stop` → `Popen` é o que implementa isso.
        """
        from gt7voice.system import SystemSpeaker

        ordem: list[str] = []

        class Espiao(SystemSpeaker):
            def stop(self) -> None:
                ordem.append("stop")

        speaker = Espiao(VoiceConfig(enabled=True), engine="say")

        import subprocess

        original_popen = subprocess.Popen
        try:
            def falso_popen(*args: object, **kwargs: object) -> object:
                ordem.append("popen")

                class Processo:
                    def poll(self) -> int | None:
                        return None

                    def terminate(self) -> None:
                        return None

                return Processo()

            subprocess.Popen = falso_popen  # type: ignore[assignment,misc]
            speaker.say("Freie mais tarde.")
        finally:
            subprocess.Popen = original_popen  # type: ignore[misc]

        assert ordem == ["stop", "popen"], (
            "a fala nova foi lançada sem cortar a anterior"
        )

    def test_texto_vazio_nao_lanca_processo(self) -> None:
        from gt7voice.system import SystemSpeaker

        speaker = SystemSpeaker(VoiceConfig(enabled=True), engine="say")
        speaker.say("   ")
        assert not speaker.is_speaking
