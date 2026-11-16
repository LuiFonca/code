"""
Bases dos recursos ainda sem tela: gravação de sessão, coach e overlay.

Estão testadas justamente por não terem tela — código que ninguém exercita
apodrece em silêncio, e "terreno preparado" que não funciona é dívida.
"""

import struct

import pytest

from src.domain.services.corner_analysis import compare_corners
from src.labs.coach import advice, top_losses
from src.labs.overlay import OverlayWindow
from src.labs.session_recording import (
    RecordingError,
    ReplayTelemetrySource,
    SessionRecorder,
    read_recording,
)
from tests.test_analise_de_curvas import PISTA, _volta


def _pacote(velocidade_ms: float, volta: int = 1) -> bytes:
    """Pacote decifrado mínimo: 296 bytes com velocidade e volta nos offsets reais."""
    dados = bytearray(296)
    dados[0x4C:0x50] = struct.pack("<f", velocidade_ms)
    dados[0x74:0x76] = struct.pack("<h", volta)
    return bytes(dados)


class _Relogio:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


# ------------------------------------------------------- gravação
def test_gravar_e_ler_devolve_os_mesmos_pacotes(tmp_path):
    relogio = _Relogio()
    gravador = SessionRecorder(tmp_path / "s.gt7rec", clock=relogio)
    pacotes = [_pacote(10.0), _pacote(20.0), _pacote(30.0)]
    for p in pacotes:
        gravador.write(p)
        relogio.t += 0.016
    gravador.close()

    lidos = read_recording(tmp_path / "s.gt7rec")
    assert [p for _, p in lidos] == pacotes
    assert [t for t, _ in lidos] == [0, 16, 32]
    assert gravador.packets == 3


def test_gravacao_cortada_no_fim_ainda_e_lida(tmp_path):
    """App fechado no meio da escrita não pode custar a sessão inteira."""
    caminho = tmp_path / "s.gt7rec"
    gravador = SessionRecorder(caminho)
    for v in (10.0, 20.0):
        gravador.write(_pacote(v))
    gravador.close()
    caminho.write_bytes(caminho.read_bytes()[:-50])

    assert len(read_recording(caminho)) == 1


def test_arquivo_estranho_e_recusado_com_mensagem(tmp_path):
    caminho = tmp_path / "nao.gt7rec"
    caminho.write_bytes(b"isto nao e uma gravacao de sessao")
    with pytest.raises(RecordingError):
        read_recording(caminho)


def test_gravacao_de_versao_futura_e_recusada(tmp_path):
    caminho = tmp_path / "futuro.gt7rec"
    caminho.write_bytes(struct.pack("<6sH", b"GT7REC", 99))
    with pytest.raises(RecordingError, match="mais novo"):
        read_recording(caminho)


def test_fonte_de_replay_entrega_os_quadros_ao_servico(
    qapp, tmp_path, laps, session, bus, on_track, flush
):
    """O contrato é o mesmo do listener: o serviço não nota a diferença."""
    from src.application.events.events import TelemetryReceived
    from src.application.services.telemetry_service import TelemetryService

    caminho = tmp_path / "s.gt7rec"
    gravador = SessionRecorder(caminho)
    for v in (10.0, 20.0, 30.0):
        gravador.write(_pacote(v))
    gravador.close()

    fonte = ReplayTelemetrySource(caminho)
    recebidos = []
    bus.subscribe(TelemetryReceived, lambda e: recebidos.append(e.point.speed_kmh))
    servico = TelemetryService(
        telemetry_source=fonte, lap_repository=laps,
        session_manager=session, event_bus=bus,
    )
    try:
        assert fonte.play_all() == 3
        flush(0.05)
        assert recebidos == pytest.approx([36.0, 72.0, 108.0])
    finally:
        servico.stop()


def test_replay_no_ritmo_original_termina_e_avisa(qapp, tmp_path, flush):
    relogio = _Relogio()
    caminho = tmp_path / "s.gt7rec"
    gravador = SessionRecorder(caminho, clock=relogio)
    for v in (10.0, 20.0, 30.0):
        gravador.write(_pacote(v))
        relogio.t += 0.02
    gravador.close()

    fonte = ReplayTelemetrySource(caminho, speed=4.0)
    quadros, estados = [], []
    fonte.telemetry_stream.connect(quadros.append)
    fonte.status_changed.connect(estados.append)
    fonte.start()
    flush(0.3)

    assert len(quadros) == 3
    assert estados == ["recebendo", "desconectado"]
    assert not fonte.is_running


def test_fonte_real_expoe_o_fluxo_de_pacotes_brutos():
    """A tomada que o gravador usa precisa existir na fonte de verdade."""
    from src.infrastructure.telemetry.listener_thread import Gt7TelemetrySource

    assert hasattr(Gt7TelemetrySource, "raw_packet_stream")


# ------------------------------------------------------- coach
def _deltas(trecho_curva_1, trecho_curva_2=None):
    volta = list(PISTA)
    volta[2] = trecho_curva_1
    if trecho_curva_2 is not None:
        volta[5] = trecho_curva_2
    return compare_corners(_volta(PISTA), _volta(volta))


def test_coach_aponta_a_curva_onde_mais_se_perdeu():
    deltas = _deltas((100.0, 75.0, 1.2, 0.0), (120.0, 100.0, -1.0, 0.0))
    piores = top_losses(deltas)
    assert [d.corner.index for d in piores] == [1, 2], "a maior perda vem primeiro"

    frases = advice(deltas)
    assert frases[0].startswith("Curva 1 (direita)")
    assert "15 km/h mais devagar" in frases[0]
    assert "somam" in frases[-1]


def test_coach_nao_inventa_problema_em_volta_igual():
    frases = advice(compare_corners(_volta(PISTA), _volta(PISTA)))
    assert frases == ["Nenhuma curva com perda relevante contra a referência."]


def test_coach_sem_curvas_fica_em_silencio():
    assert advice([]) == []


# ------------------------------------------------------- overlay
def test_overlay_mostra_o_que_o_viewmodel_publica(qapp, bus):
    from src.application.events.events import DeltaUpdated, TelemetryReceived
    from src.application.viewmodels.live_viewmodel import LiveViewModel
    from tests.conftest import FakeFrame, make_point

    vm = LiveViewModel(bus)
    janela = OverlayWindow(vm)
    try:
        ponto = make_point(1, 10, 90000, 3600.0)
        ponto.speed_kmh, ponto.gear = 187.0, 4
        bus.publish(TelemetryReceived(point=ponto, frame=FakeFrame()))
        bus.publish(DeltaUpdated(delta_best_s=-0.31, delta_previous_s=0.12))
        vm._emit_latest()

        assert janela.speed.text() == "187"
        assert janela.gear.text() == "4"
        assert janela.delta_best.text() == "-0.31"
        assert janela.delta_previous.text() == "+0.12"

        vm.stale_entered.emit()
        assert janela.speed.text() == "--", "sem sinal não é carro parado"
    finally:
        vm.dispose()


def test_nada_fora_de_labs_depende_de_labs():
    """`labs` é terreno preparado: o produto não pode depender dele."""
    import pathlib

    for arquivo in pathlib.Path("src").rglob("*.py"):
        if "labs" in arquivo.parts:
            continue
        assert "labs" not in arquivo.read_text(encoding="utf-8").replace(
            "src/labs/session_recording.py", ""
        ), f"{arquivo} importa ou cita labs"
