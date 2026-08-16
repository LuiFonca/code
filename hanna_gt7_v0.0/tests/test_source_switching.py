"""
Trocar a fonte de telemetria com o programa aberto.

É a operação que a tela de configuração existe para permitir: sair do gerador
sintético e apontar para o PS5 sem fechar o programa. Acertar o IP e a rede já é
a parte chata da instalação; exigir um reinício a cada tentativa tornaria isso
insuportável.

O defeito provável aqui é silencioso, e é por isso que o primeiro teste é o mais
importante: quem se inscreveu para receber quadros inscreveu-se no **objeto**
fonte. Trocar o objeto sem transferir as inscrições produz um programa que
captura corretamente, não registra erro nenhum, e não move um pixel na tela.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gt7app.application import build_core
from gt7core.config.settings import Settings
from gt7core.telemetry.engine import TelemetryReceived
from gt7core.telemetry.sources.factory import TelemetrySourceError


def make_settings(tmp_path: Path, **telemetry: object) -> Settings:
    settings = Settings()
    settings.storage.database_path = tmp_path / "t.db"
    settings.storage.telemetry_path = tmp_path / "tel"
    settings.telemetry.source = "mock"
    settings.telemetry.mock_speed_multiplier = 60.0
    for name, value in telemetry.items():
        setattr(settings.telemetry, name, value)
    return settings


class TestTrocaDeFonte:
    def test_as_inscricoes_sobrevivem_a_troca(self, tmp_path: Path) -> None:
        """O defeito que este módulo existe para impedir.

        Sem `adopt_callbacks_from`, a fonte nova nasce muda: os quadros são
        produzidos, ninguém os recebe, e não há erro em lugar nenhum. O usuário
        vê uma tela parada e nenhuma pista do motivo.
        """
        core = build_core(make_settings(tmp_path))
        try:
            recebidos: list[TelemetryReceived] = []
            core.bus.subscribe(TelemetryReceived, recebidos.append)

            core.reconfigure_source()

            core.start()
            import time

            prazo = time.monotonic() + 8.0
            while time.monotonic() < prazo and len(recebidos) < 20:
                time.sleep(0.05)
            core.stop()

            assert len(recebidos) >= 20, "a fonte nova nasceu muda"
        finally:
            core.close()

    def test_a_fonte_antiga_para_antes_de_a_nova_subir(self, tmp_path: Path) -> None:
        """Duas fontes vivas escreveriam no mesmo motor.

        O resultado seria uma volta que não aconteceu: quadros sintéticos
        intercalados com os do console, distâncias fora de ordem, e uma análise
        confiante sobre dados que nunca existiram.
        """
        core = build_core(make_settings(tmp_path))
        try:
            core.start()
            antiga = core.source
            assert antiga.is_running

            core.reconfigure_source()

            assert not antiga.is_running
            assert core.source is not antiga
            assert core.source.is_running, "a nova devia continuar de onde a antiga parou"
        finally:
            core.close()

    def test_parada_continua_parada(self, tmp_path: Path) -> None:
        """Salvar configuração não pode começar uma captura que ninguém pediu."""
        core = build_core(make_settings(tmp_path))
        try:
            assert not core.source.is_running
            core.reconfigure_source()
            assert not core.source.is_running
        finally:
            core.close()

    def test_configuracao_invalida_preserva_a_fonte_atual(self, tmp_path: Path) -> None:
        """Um erro de digitação não pode deixar o programa sem captura.

        `udp` sem IP é o caso real: a pessoa escolhe "PS5 na rede", ainda não
        digitou o endereço, e salva. Se a fonte antiga já tivesse sido
        descartada, restaria um programa vivo e surdo — pior que a mensagem de
        erro.
        """
        core = build_core(make_settings(tmp_path))
        try:
            antiga = core.source
            core.settings.telemetry.source = "udp"
            core.settings.telemetry.ps_ip = ""

            with pytest.raises(TelemetrySourceError):
                core.reconfigure_source()

            assert core.source is antiga, "a fonte válida foi descartada por um erro"
        finally:
            core.close()

    def test_troca_para_udp_com_ip_monta_a_fonte_de_rede(self, tmp_path: Path) -> None:
        """Monta, mas não conecta — não há PS5 num teste.

        Verifica o que dá para verificar sem console: que a escolha chega ao
        factory e produz a classe certa. O encontro com a rede é do usuário.
        """
        core = build_core(make_settings(tmp_path))
        try:
            core.settings.telemetry.source = "udp"
            core.settings.telemetry.ps_ip = "192.168.1.50"
            core.reconfigure_source()

            assert type(core.source).__name__ == "Gt7UdpTelemetrySource"
        finally:
            core.source.stop()
            core.close()


class TestAdocaoDeCallbacks:
    def test_nao_duplica_ao_adotar_de_si_mesma(self, tmp_path: Path) -> None:
        """Guarda contra o caso degenerado: adotar as próprias inscrições
        dobraria cada callback e o motor receberia todo quadro duas vezes."""
        from gt7core.telemetry.sources.mock import MockTelemetrySource

        recebidos: list[object] = []
        fonte = MockTelemetrySource(sample_rate_hz=60)
        fonte.on_frame(recebidos.append)

        fonte.adopt_callbacks_from(fonte)

        fonte._emit_frame(object())  # type: ignore[arg-type]  # noqa: SLF001
        assert len(recebidos) == 1

    def test_transfere_quadros_e_status(self) -> None:
        from gt7core.telemetry.sources.mock import MockTelemetrySource

        quadros: list[object] = []
        estados: list[tuple[str, str]] = []

        antiga = MockTelemetrySource(sample_rate_hz=60)
        antiga.on_frame(quadros.append)
        antiga.on_status(lambda estado, msg: estados.append((estado, msg)))

        nova = MockTelemetrySource(sample_rate_hz=60)
        nova.adopt_callbacks_from(antiga)

        nova._emit_frame(object())  # type: ignore[arg-type]  # noqa: SLF001
        nova._emit_status("conectado", "ok")  # type: ignore[arg-type]  # noqa: SLF001

        assert len(quadros) == 1
        assert estados == [("conectado", "ok")]
