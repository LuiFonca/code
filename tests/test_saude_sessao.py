"""
Saúde da sessão — a medição que substituiu a ferramenta de porta compartilhada.

A versão anterior abria a porta 33740 por conta própria para medir a sessão
real. Não funciona: UDP unicast entrega o pacote a um socket só, então a
ferramenta roubava o fluxo do app em vez de observá-lo. Medir de dentro
elimina a disputa.

O relógio é injetado nos testes. Sem isso, verificar taxa e interrupções
exigiria esperar segundos de verdade — e um teste que dorme é um teste que
ninguém roda.
"""

import pytest

from src.application.events.events import (
    ConnectionStateChanged,
    LapCompleted,
    TelemetryReceived,
)
from src.application.services.session_health import (
    BURACO_S,
    SessionHealth,
    VOLTAS_MINIMAS,
)
from src.domain.models.lap import Lap
from tests.conftest import FakeFrame, make_point


class RelogioFalso:
    """Relógio controlado pelo teste."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def avancar(self, segundos: float) -> None:
        self.t += segundos


@pytest.fixture
def relogio():
    return RelogioFalso()


@pytest.fixture
def saude(bus, relogio):
    s = SessionHealth(bus, relogio=relogio)
    yield s
    s.dispose()


def _emitir(bus, relogio, quantidade: int, intervalo: float = 1 / 60, **frame_kwargs):
    ponto = make_point(0, 1, 1000, 10.0)
    for _ in range(quantidade):
        bus.publish(TelemetryReceived(point=ponto, frame=FakeFrame(**frame_kwargs)))
        relogio.avancar(intervalo)


# ----------------------------------------------------------- taxa
def test_taxa_de_sessao_saudavel(saude, bus, relogio):
    _emitir(bus, relogio, 600, intervalo=1 / 60)
    assert saude.taxa_hz == pytest.approx(60.0, rel=0.02)
    assert all(v.ok for v in saude.vereditos() if "Taxa" in v.titulo)


def test_taxa_baixa_reprova(saude, bus, relogio):
    """Metade da taxa esperada: perda de pacote em algum ponto do caminho."""
    _emitir(bus, relogio, 300, intervalo=1 / 30)
    assert saude.taxa_hz == pytest.approx(30.0, rel=0.02)
    taxa = [v for v in saude.vereditos() if "Taxa" in v.titulo][0]
    assert not taxa.ok
    assert not saude.aprovada


# ----------------------------------------------------------- interrupções
def test_fluxo_continuo_nao_registra_buraco(saude, bus, relogio):
    _emitir(bus, relogio, 200)
    assert saude.buracos == 0


def test_interrupcao_e_registrada_com_a_maior_duracao(saude, bus, relogio):
    _emitir(bus, relogio, 60)
    relogio.avancar(BURACO_S + 1.5)
    _emitir(bus, relogio, 60)
    relogio.avancar(BURACO_S + 0.2)
    _emitir(bus, relogio, 60)

    assert saude.buracos == 2
    assert saude.maior_buraco_s == pytest.approx(BURACO_S + 1.5, abs=0.05)


def test_reconexao_nao_vira_interrupcao(saude, bus, relogio):
    """Desconectar e reconectar não pode ser contado como falha de fluxo.

    Sem esta regra, uma pausa de cinco minutos entre duas sessões viraria um
    "buraco" de 300 s e afundaria a taxa média de uma sessão perfeita.
    """
    _emitir(bus, relogio, 60)
    bus.publish(ConnectionStateChanged(state="desconectado"))
    relogio.avancar(300.0)
    bus.publish(ConnectionStateChanged(state="recebendo"))
    _emitir(bus, relogio, 60)

    assert saude.buracos == 0, "a pausa entre conexões não é interrupção de fluxo"


def test_reconexao_sem_aviso_de_queda_tambem_nao_vira_interrupcao(
    saude, bus, relogio
):
    """Nem toda reconexão é precedida de um "desconectado".

    Uma queda silenciosa seguida de reconexão automática publica "conectando"
    direto. Se só o caminho do "desconectado" zerasse o relógio, esse caso
    contaria o intervalo inteiro como buraco de fluxo.
    """
    _emitir(bus, relogio, 60)
    relogio.avancar(300.0)
    bus.publish(ConnectionStateChanged(state="conectando"))
    _emitir(bus, relogio, 60)

    assert saude.buracos == 0


def test_queda_de_conexao_aparece_no_relatorio(saude, bus, relogio):
    _emitir(bus, relogio, 30)
    bus.publish(ConnectionStateChanged(state="conectando"))
    _emitir(bus, relogio, 30)
    assert saude.quedas == 1


# ----------------------------------------------------------- orientação
def test_orientacao_valida_e_contada(saude, bus, relogio):
    _emitir(bus, relogio, 100)  # FakeFrame nasce com quaternion identidade
    assert saude.orientacao_ok == 100
    assert saude.orientacao_pct == pytest.approx(100.0)


def test_orientacao_nula_reprova_o_angulo(saude, bus, relogio):
    """Quaternion zerado é campo ausente, não carro alinhado."""
    _emitir(
        bus, relogio, 100,
        rotation_i=0.0, rotation_j=0.0, rotation_k=0.0, rotation_w=0.0,
    )
    assert saude.orientacao_nula == 100
    orientacao = [v for v in saude.vereditos() if "Orientação" in v.titulo][0]
    assert not orientacao.ok


# ----------------------------------------------------------- voltas
def test_voltas_contam_para_a_validacao(saude, bus, relogio):
    _emitir(bus, relogio, 600)
    for i in range(VOLTAS_MINIMAS):
        bus.publish(LapCompleted(lap=Lap(lap_time_ms=90000), lap_id=i, is_best=False))
    assert saude.voltas == VOLTAS_MINIMAS
    assert saude.aprovada


def test_poucas_voltas_nao_validam_sessao_longa(saude, bus, relogio):
    _emitir(bus, relogio, 600)
    bus.publish(LapCompleted(lap=Lap(lap_time_ms=90000), lap_id=1, is_best=True))
    assert not saude.aprovada


# ----------------------------------------------------------- relatório
def test_relatorio_sem_dados_orienta_em_vez_de_mentir(saude):
    texto = saude.relatorio()
    assert "Nenhuma telemetria recebida" in texto
    assert saude.vereditos() == []
    assert not saude.aprovada


def test_relatorio_completo_traz_os_numeros(saude, bus, relogio):
    _emitir(bus, relogio, 600)
    for i in range(VOLTAS_MINIMAS):
        bus.publish(LapCompleted(lap=Lap(lap_time_ms=90000), lap_id=i, is_best=False))

    texto = saude.relatorio()
    assert "RELATÓRIO DE SESSÃO" in texto
    assert "taxa média" in texto
    assert "SESSÃO LONGA VALIDADA" in texto
    assert "[!]" not in texto


# ----------------------------------------------------------- custo
def test_coletor_nao_guarda_nada_por_amostra(saude, bus, relogio):
    """O coletor roda a 60 Hz; guardar por amostra o tornaria o problema.

    Verifica o tamanho do estado, não o tempo: contadores não crescem, uma
    lista cresceria.
    """
    _emitir(bus, relogio, 5000)
    tamanhos = [
        len(v) for v in vars(saude).values() if isinstance(v, (list, dict, set))
    ]
    assert not tamanhos or max(tamanhos) == 0, (
        "o coletor está acumulando estrutura por amostra"
    )


def test_reset_zera_a_sessao(saude, bus, relogio):
    _emitir(bus, relogio, 100)
    assert saude.amostras == 100
    saude.reset()
    assert saude.amostras == 0
    assert saude.duracao_s == 0.0


def test_dispose_para_de_coletar(saude, bus, relogio):
    _emitir(bus, relogio, 50)
    saude.dispose()
    _emitir(bus, relogio, 50)
    assert saude.amostras == 50


# ----------------------------------------------------------- integração
def test_app_monta_a_medicao_e_o_botao(qapp, tmp_path):
    """A medição precisa estar ligada de verdade no app montado.

    O coletor pode passar em todos os testes acima e ainda assim não medir
    nada, se o composition root esquecer de criá-lo ou a janela de recebê-lo.
    """
    import src.main as M
    from src.infrastructure.repositories.sqlite_database import SqliteDatabase

    original = M.SqliteDatabase
    M.SqliteDatabase = lambda *a, **k: SqliteDatabase(tmp_path / "app.db")
    try:
        w = M.build_application()
    finally:
        M.SqliteDatabase = original

    try:
        assert w._health is not None, "a janela precisa receber a medição"
        assert w.health_button.isEnabled()

        # Um pacote publicado no barramento tem que chegar ao coletor.
        w._bus.publish(
            TelemetryReceived(point=make_point(0, 1, 1000, 10.0), frame=FakeFrame())
        )
        assert w._health.amostras == 1
        assert "RELATÓRIO DE SESSÃO" in w._health.relatorio()
    finally:
        w._service.stop()


def test_ferramenta_externa_nao_compartilha_a_porta():
    """A ferramenta de linha de comando não pode disputar a porta com o app.

    UDP unicast entrega o pacote a um socket só; com `SO_REUSEPORT` o segundo
    `bind` é aceito e o app para de receber sem nenhum erro visível. Este teste
    trava a decisão: a ferramenta abre a porta em modo exclusivo e falha alto
    quando ela está ocupada.
    """
    import pathlib

    fonte = pathlib.Path("src/tools/soak_check.py").read_text()
    assert "SO_REUSEPORT, 1" not in fonte, (
        "a ferramenta voltou a compartilhar a porta com o app"
    )
    assert "já está em uso" in fonte, (
        "a ferramenta precisa avisar quando a porta está ocupada"
    )
