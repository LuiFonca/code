"""
A volta em andamento não pode ser destruída por eventos de interface.

Estes três testes vieram de sintomas relatados numa sessão real: delta que só
funcionava no começo da volta, gráficos com um pedaço da volta, e voltas que
não viravam recorde. Sintomas diferentes, mesma causa — o estado da volta em
curso era zerado por um evento de foco da interface.

`editingFinished` de um `QLineEdit` dispara a cada **perda de foco**, não a
cada edição. Clicar em outra aba, no gráfico, ou em qualquer outro campo
disparava a troca de pista, e a troca de pista zerava o acúmulo da volta.
"""

import pytest

from src.application.events.events import LapCompleted


@pytest.fixture
def janela(qapp, tmp_path):
    """App montado sobre um banco temporário."""
    import src.main as M
    from src.infrastructure.repositories.sqlite_database import SqliteDatabase

    original = M.SqliteDatabase
    M.SqliteDatabase = lambda *a, **k: SqliteDatabase(tmp_path / "app.db")
    try:
        w = M.build_application()
    finally:
        M.SqliteDatabase = original
    yield w
    w._service.stop()


def _meia_volta(source, lap_no, ate, total=120, lap_ms=90000):
    from tests.conftest import FakeFrame

    for i in range(ate):
        source.feed(
            FakeFrame(lap_count=lap_no, current_lap_ms=int(i * lap_ms / total),
                      speed_kmh=180.0)
        )


def test_perder_o_foco_do_campo_de_pista_nao_zera_a_volta(
    service, source, on_track, session, flush, collect
):
    """O caso que quebrava tudo: um evento de foco no meio da volta.

    A interface reaplica a pista a cada perda de foco. Quando isso recarregava
    a referência, o serviço zerava buffer e distância — e a volta seguia como
    se tivesse acabado de começar.
    """
    from tests.conftest import FakeFrame

    eventos = collect(LapCompleted)
    service.start()

    for i in range(60):
        source.feed(FakeFrame(lap_count=1, current_lap_ms=i * 500, speed_kmh=180.0))
    amostras_antes = len(service._buffer)
    distancia_antes = service._cumulative_distance
    assert amostras_antes > 50

    # A interface reaplica a MESMA pista — é o que um clique fora do campo faz.
    service.reload_reference()

    assert len(service._buffer) == amostras_antes, (
        "reaplicar a mesma pista descartou as amostras da volta em andamento"
    )
    assert service._cumulative_distance == pytest.approx(distancia_antes), (
        "a distância acumulada foi zerada no meio da volta"
    )


def test_trocar_de_pista_de_verdade_ainda_zera(service, source, tracks, session, flush):
    """Contraprova: mudar de pista precisa continuar descartando.

    A volta em curso pertence à pista anterior; carregá-la na nova produziria
    um recorde falso e um traçado misturado.
    """
    from src.domain.models.track import Track
    from tests.conftest import FakeFrame

    service.start()
    session.set_track(Track(id=tracks.get_or_create("A"), name="A"))
    for i in range(60):
        source.feed(FakeFrame(lap_count=1, current_lap_ms=i * 500, speed_kmh=180.0))
    assert len(service._buffer) > 50

    session.set_track(Track(id=tracks.get_or_create("B"), name="B"))
    service.reload_reference()

    assert service._buffer == [], "trocar de pista precisa descartar a volta em curso"
    assert service._cumulative_distance == 0.0


def test_volta_inteira_chega_ao_banco_apos_evento_de_foco(
    service, source, laps, on_track, flush
):
    """O efeito visível: a volta gravada precisa ter a volta inteira.

    Era isto que deixava os gráficos pela metade — a volta salva começava no
    ponto em que o último evento de foco zerou o buffer.
    """
    from tests.conftest import FakeFrame

    track_id, _ = on_track
    service.start()

    # Uma virada primeiro: só a partir dela a volta é observada desde a largada
    # e pode disputar recorde.
    source.feed(FakeFrame(lap_count=1, current_lap_ms=89000, speed_kmh=180.0))
    source.feed(FakeFrame(lap_count=2, current_lap_ms=0, last_lap_ms=90000))

    for i in range(120):
        source.feed(FakeFrame(lap_count=2, current_lap_ms=i * 750, speed_kmh=180.0))
        if i == 40:
            service.reload_reference()   # perda de foco no meio da volta
    source.feed(FakeFrame(lap_count=3, current_lap_ms=0, last_lap_ms=90000))
    flush(0.4)

    completas = [lap for lap in laps.get_by_track(track_id) if lap.is_complete]
    assert completas, "a volta observada desde a largada precisa contar como completa"
    pontos = laps.load_points(completas[0].id)
    assert len(pontos) >= 115, (
        f"a volta chegou ao banco com {len(pontos)} amostras de 120 — "
        "o evento de foco cortou o começo"
    )
