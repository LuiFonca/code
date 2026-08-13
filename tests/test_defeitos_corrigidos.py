"""
Um teste por defeito da auditoria.

Estes casos são a rede de proteção do projeto: cada um trava o comportamento
de uma correção específica. Se uma mudança futura desfizer qualquer uma delas,
o teste correspondente fica vermelho e diz qual defeito voltou.

A numeração segue a da auditoria, de propósito — quando um destes falhar, dá
para ir direto ao relatório entender o que era o problema original.
"""

import errno

import pytest

from src.application.events.events import (
    LapCompleted,
    LapDeleted,
    LapsPurged,
    TelemetryReceived,
)
from src.application.viewmodels.history_viewmodel import HistoryViewModel
from src.application.viewmodels.telemetry_viewmodel import (
    SLIP_SATURATION,
    slip_index_pct,
)
from src.domain.services.lap_analysis import LapSeries, compute_delta_series


# ------------------------------------------------------------------ 01
def test_01_volta_parcial_nao_vira_recorde(service, source, laps, on_track, flush):
    """Conectar no meio de uma volta não pode produzir um recorde falso.

    O tempo vem do jogo e é da volta inteira; as amostras cobrem só o pedaço
    observado. Aceitá-la como recorde daria uma referência de delta que morre
    no meio da pista.
    """
    track_id, _ = on_track
    concluidas = []
    service._bus.subscribe(LapCompleted, concluidas.append)
    service.start()

    # Sem virada de volta presenciada: o serviço só vê o fim desta volta.
    source.feed_lap(lap_no=1, lap_ms=60000)
    flush()

    gravadas = laps.get_by_track(track_id)
    assert len(gravadas) == 1, "a volta parcial deve ser gravada"
    assert gravadas[0].is_complete is False
    assert laps.get_best(track_id) is None, "parcial não pode virar recorde"

    # Duas camadas independentes precisam recusar a parcial: o filtro do
    # repositório (acima) e a decisão do serviço (aqui). Verificar só uma
    # deixaria a outra livre para regredir sem ninguém notar.
    assert concluidas, "a volta deve gerar evento"
    assert concluidas[-1].is_best is False, "o serviço não pode marcá-la como recorde"

    # A volta seguinte é observada desde o início, mesmo sendo mais lenta.
    source.feed_lap(lap_no=2, lap_ms=95000)
    flush()

    melhor = laps.get_best(track_id)
    assert melhor is not None
    assert melhor.lap_time_ms == 95000
    assert melhor.is_complete is True
    assert concluidas[-1].is_best is True


# ------------------------------------------------------------------ 02
@pytest.mark.parametrize(
    "bruto,esperado",
    [(0.0, 0.0), (0.05, 5.0), (0.5, 50.0), (1.0, 100.0), (2.0, 100.0)],
)
def test_02_indice_deslizamento_sem_unidade_falsa(bruto, esperado):
    """O campo de slip do GT7 é razão, não ângulo.

    A versão antiga multiplicava por 12 e rotulava como graus — unidade que
    não existe. O índice satura em 100 % e não afirma unidade física.
    """
    assert slip_index_pct(bruto) == pytest.approx(esperado)
    assert SLIP_SATURATION == 1.0


# ------------------------------------------------------------------ 03
def test_03_excluir_melhor_volta_recarrega_delta(
    service, source, laps, tracks, bus, on_track, flush
):
    """Excluir o recorde não pode deixar o delta comparando contra ele."""
    track_id, _ = on_track
    service.start()
    source.feed_lap(lap_no=1, lap_ms=90000)  # parcial, descartada como recorde
    # Distâncias diferentes de propósito: é o que permite distinguir qual volta
    # o comparador está usando. Verificar só `has_reference` não serve — ele
    # continua verdadeiro apontando para a volta apagada.
    source.feed_lap(lap_no=2, lap_ms=88000, speed_kmh=200.0)
    source.feed_lap(lap_no=3, lap_ms=92000, speed_kmh=100.0)
    flush()

    melhor = laps.get_best(track_id)
    assert melhor.lap_time_ms == 88000
    distancia_da_melhor = service._comparator_best._distances[-1]

    history = HistoryViewModel(laps, bus)
    history.set_track(track_id)
    history.delete_lap(melhor.id)
    flush()

    nova = laps.get_best(track_id)
    assert nova.id != melhor.id
    assert nova.lap_time_ms == 92000

    # A referência precisa ter TROCADO de conteúdo, não apenas continuar existindo.
    assert service._comparator_best.has_reference
    distancia_agora = service._comparator_best._distances[-1]
    assert distancia_agora != pytest.approx(distancia_da_melhor), (
        "o comparador ainda aponta para a volta excluída"
    )
    esperada = laps.load_points(nova.id)[-1].distance_m
    assert distancia_agora == pytest.approx(esperada)


# ------------------------------------------------------------------ 04
def test_04_poda_avisa_quantas_voltas_saiu(database, tracks, make_lap, collect):
    """A retenção não pode descartar voltas em silêncio."""
    from src.infrastructure.repositories.sqlite_lap_repository import (
        SqliteLapRepository,
    )

    repo = SqliteLapRepository(database, keep_best=2, keep_recent=3)
    track_id = tracks.get_or_create("T")

    for i in range(6):
        repo.save(make_lap(track_id=track_id, lap_time_ms=95000 - i * 100, samples=20))

    restantes = repo.get_by_track(track_id)
    assert len(restantes) < 6, "a política de retenção deve ter podado"
    assert repo.last_purged_count > 0, "a poda precisa ser contável"


def test_04_evento_de_poda_chega_na_interface(
    service, source, laps, database, tracks, session, bus, collect, flush
):
    """O aviso de poda precisa trafegar pelo barramento."""
    from src.domain.models.track import Track
    from src.infrastructure.repositories.sqlite_lap_repository import (
        SqliteLapRepository,
    )

    apertado = SqliteLapRepository(database, keep_best=1, keep_recent=1)
    service._laps = apertado
    service._writer._laps = apertado
    track_id = tracks.get_or_create("T")
    session.set_track(Track(id=track_id, name="T"))

    eventos = collect(LapsPurged)
    service.start()
    for lap_no, ms in ((1, 90000), (2, 89000), (3, 88000), (4, 87000)):
        source.feed_lap(lap_no=lap_no, lap_ms=ms)
    flush(0.6)

    assert eventos, "a poda deve publicar LapsPurged"
    assert eventos[0].count > 0


# ------------------------------------------------------------------ 05
def test_05_comparacao_usa_cache_de_arrays(make_lap):
    """O gargalo era refazer zip() sobre a série inteira a cada consulta."""
    a = LapSeries(make_lap(samples=3000, lap_time_ms=90000).points)
    b = LapSeries(make_lap(samples=3000, lap_time_ms=92000).points)

    import time

    inicio = time.perf_counter()
    delta = compute_delta_series(a, b)
    duracao_ms = (time.perf_counter() - inicio) * 1000

    assert len(delta) > 0
    # Antes do cache, 402 chamadas sobre 10.800 pares levavam ~460 ms.
    assert duracao_ms < 150, f"comparação lenta demais: {duracao_ms:.0f} ms"

    # O cache precisa devolver o mesmo objeto, não uma cópia recalculada.
    assert a._channel_arrays("speed_kmh") is a._channel_arrays("speed_kmh")


# ------------------------------------------------------------------ 06
def test_06_gravacao_fora_da_thread_da_interface(
    service, source, laps, on_track, flush
):
    """Escrever no SQLite não pode acontecer na thread que pinta a tela."""
    import threading

    thread_principal = threading.get_ident()
    threads_de_escrita = []
    original = laps.save

    def espiao(lap):
        threads_de_escrita.append(threading.get_ident())
        return original(lap)

    laps.save = espiao
    service.start()
    source.feed_lap(lap_no=1, lap_ms=30000)
    source.feed_lap(lap_no=2, lap_ms=30000)
    flush(0.6)

    assert threads_de_escrita, "nenhuma gravação aconteceu"
    assert all(t != thread_principal for t in threads_de_escrita)


# ------------------------------------------------------------------ 07
def test_07_forca_g_saturada(service, source, on_track, flush):
    """Um salto de velocidade não pode virar força G absurda no banco."""
    from src.application.services.telemetry_service import MAX_G
    from tests.conftest import FakeFrame

    service.start()
    # Salto violento entre dois pacotes consecutivos.
    source.feed(FakeFrame(lap_count=1, current_lap_ms=0, velocity_x=10.0, speed_kmh=36))
    source.feed(
        FakeFrame(lap_count=1, current_lap_ms=16, velocity_x=200.0, speed_kmh=720)
    )
    flush(0.1)

    for ponto in service._buffer:
        assert abs(ponto.g_lateral) <= MAX_G
        assert abs(ponto.g_longitudinal) <= MAX_G


def test_07_intervalo_invalido_nao_deriva(service, source, on_track, flush):
    """Intervalo fora da faixa de 60 Hz significa pacote perdido."""
    from tests.conftest import FakeFrame

    service.start()
    source.feed(FakeFrame(lap_count=1, current_lap_ms=0, velocity_x=10.0))
    # 5 segundos entre pacotes: muito além do intervalo esperado.
    source.feed(FakeFrame(lap_count=1, current_lap_ms=5000, velocity_x=60.0))
    flush(0.1)

    assert service._buffer[-1].g_longitudinal == 0.0


# ------------------------------------------------------------------ 08
def test_08_fora_da_pista_suspende_gravacao_mas_nao_a_exibicao(
    service, source, on_track, collect, flush
):
    """A flag não pode apagar o painel ao vivo — só suspender o acúmulo.

    Esta é a regressão que apareceu na validação: gatear o processamento
    inteiro deixava o dashboard vazio sempre que a flag viesse marcada.
    """
    from tests.conftest import FakeFrame

    exibidos = collect(TelemetryReceived)
    service.start()

    for i in range(20):
        source.feed(FakeFrame(lap_count=1, current_lap_ms=i * 16, is_on_track=True))
    flush(0.1)
    na_pista_exibidos = len(exibidos)
    na_pista_buffer = len(service._buffer)

    for i in range(20):
        source.feed(
            FakeFrame(lap_count=1, current_lap_ms=400 + i * 16, is_on_track=False)
        )
    flush(0.1)

    assert len(exibidos) == na_pista_exibidos + 20, "o painel deve continuar recebendo"
    assert len(service._buffer) == na_pista_buffer, "o buffer não deve crescer"


# ------------------------------------------------------------------ 09
def test_09_combustivel_em_percentual_do_tanque(laps, tracks, make_lap, bus, qapp):
    """Combustível precisa ter unidade explícita e consistente."""
    from src.application.viewmodels.telemetry_viewmodel import TelemetryViewModel

    track_id = tracks.get_or_create("T")
    lap_id = laps.save(make_lap(track_id=track_id, samples=100))

    vm = TelemetryViewModel(laps, bus, tracks)
    vm.set_track(track_id)
    vm.load_lap(lap_id)

    assert vm.fuel_used_pct() is None, "sem capacidade do tanque, não há percentual"

    vm.set_tank_capacity(60.0)
    pct = vm.fuel_used_pct()
    assert pct is not None
    assert pct == pytest.approx(5.0, abs=0.1)  # 3 L de 60 L


# ------------------------------------------------------------------ 10
def test_10_empate_de_recorde_desempata_pelo_mais_antigo(
    laps, tracks, make_lap, bus, qapp
):
    """Duas voltas com o mesmo tempo não podem receber dois troféus."""
    track_id = tracks.get_or_create("T")
    primeira = laps.save(make_lap(track_id=track_id, lap_time_ms=88000, samples=50))
    segunda = laps.save(make_lap(track_id=track_id, lap_time_ms=88000, samples=50))

    melhor = laps.get_best(track_id)
    assert melhor.id == primeira, "desempate pelo id mais antigo"

    history = HistoryViewModel(laps, bus)
    linhas = []
    history.laps_changed.connect(lambda rows: linhas.append(rows))
    history.set_track(track_id)

    marcadas = [r.lap.id for r in linhas[-1] if r.is_best]
    assert marcadas == [primeira], f"troféu único, veio {marcadas} (outra: {segunda})"


# ------------------------------------------------------------------ 11
def test_11_dispose_libera_inscricoes(laps, tracks, bus, qapp):
    """ViewModel descartado não pode continuar assinando o barramento."""
    from src.application.viewmodels.comparison_viewmodel import ComparisonViewModel
    from src.application.viewmodels.live_viewmodel import LiveViewModel
    from src.application.viewmodels.telemetry_viewmodel import TelemetryViewModel

    antes = sum(len(v) for v in bus._handlers.values())
    vms = [
        HistoryViewModel(laps, bus),
        ComparisonViewModel(laps, bus),
        TelemetryViewModel(laps, bus),
        LiveViewModel(bus),
    ]
    assert sum(len(v) for v in bus._handlers.values()) > antes

    for vm in vms:
        vm.dispose()
    assert sum(len(v) for v in bus._handlers.values()) == antes


# ------------------------------------------------------------------ 12
def test_12_setores_configuraveis_por_pista(laps, tracks, make_lap):
    """Sem os pontos oficiais do GT7, ao menos o corte deve ser ajustável."""
    track_id = tracks.get_or_create("T")

    padrao_id = laps.save(make_lap(track_id=track_id, lap_time_ms=90000, samples=300))
    padrao = laps.get_sector_times(padrao_id)
    assert len(padrao) == 3
    assert padrao[0] == pytest.approx(padrao[1], rel=0.05), "terços quase iguais"

    tracks.set_sector_fractions(track_id, [0.25, 0.55, 1.0])
    ajustado_id = laps.save(make_lap(track_id=track_id, lap_time_ms=90000, samples=300))
    ajustado = laps.get_sector_times(ajustado_id)
    assert ajustado[0] < padrao[0], "primeiro setor deve encurtar"
    assert sum(ajustado) == pytest.approx(90000, rel=0.02)

    # Configuração inválida cai no padrão em vez de produzir setor sem sentido.
    tracks.set_sector_fractions(track_id, [0.9, 0.2])
    invalido_id = laps.save(make_lap(track_id=track_id, lap_time_ms=90000, samples=300))
    assert laps.get_sector_times(invalido_id)[0] == pytest.approx(padrao[0], rel=0.05)


# ------------------------------------------------------------------ 13
def test_13_track_id_tem_chave_estrangeira(database, laps, make_lap):
    """Gravar volta apontando para pista inexistente deve falhar."""
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        laps.save(make_lap(track_id=99999, samples=10))

    # E a transação inteira precisa ter sido revertida.
    orfas = database.connection.execute(
        "SELECT COUNT(*) FROM lap_frames WHERE lap_id NOT IN (SELECT id FROM laps)"
    ).fetchone()[0]
    assert orfas == 0


def test_13_migracao_v6_zera_orfas_em_vez_de_apagar(tmp_path):
    """Voltas órfãs de bancos antigos são preservadas, com pista nula."""
    import sqlite3
    import time

    from src.infrastructure.repositories.sqlite_database import SqliteDatabase

    caminho = tmp_path / "antigo.db"
    conn = sqlite3.connect(caminho)
    conn.execute(
        "CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT NOT NULL UNIQUE, created_at REAL NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE laps (id INTEGER PRIMARY KEY AUTOINCREMENT, track_id INTEGER, "
        "lap_time_ms INTEGER NOT NULL, recorded_at REAL NOT NULL, "
        "frame_count INTEGER NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE lap_frames (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "lap_id INTEGER NOT NULL REFERENCES laps(id) ON DELETE CASCADE, "
        "seq INTEGER NOT NULL, elapsed_ms INTEGER NOT NULL, distance_m REAL NOT NULL, "
        "speed_kmh REAL NOT NULL, rpm REAL NOT NULL, gear INTEGER NOT NULL, "
        "throttle REAL NOT NULL, brake REAL NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE sector_times (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "lap_id INTEGER NOT NULL REFERENCES laps(id) ON DELETE CASCADE, "
        "sector_index INTEGER NOT NULL, time_ms INTEGER NOT NULL)"
    )
    conn.execute("INSERT INTO tracks (name, created_at) VALUES ('X', ?)", (time.time(),))
    conn.execute(
        "INSERT INTO laps (track_id, lap_time_ms, recorded_at, frame_count) "
        "VALUES (1, 90000, ?, 0)", (time.time(),)
    )
    conn.execute(
        "INSERT INTO laps (track_id, lap_time_ms, recorded_at, frame_count) "
        "VALUES (999, 88000, ?, 0)", (time.time(),)
    )
    conn.execute("PRAGMA user_version = 5")
    conn.commit()
    conn.close()

    db = SqliteDatabase(caminho)
    total = db.connection.execute("SELECT COUNT(*) FROM laps").fetchone()[0]
    orfas = db.connection.execute(
        "SELECT COUNT(*) FROM laps WHERE track_id IS NULL"
    ).fetchone()[0]
    assert total == 2, "nenhuma volta pode ser apagada na migração"
    assert orfas == 1, "a órfã deve ter o track_id zerado"
    assert db.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    db.close()


# ------------------------------- extras da validação ------------------------
def test_extra_erro_de_rede_vira_mensagem_acionavel():
    """Errno cru não diz o que fazer nem para qual endereço."""
    from src.infrastructure.telemetry.listener_thread import _ListenerThread

    thread = _ListenerThread("192.168.1.50")
    msg = thread._describe_send_error(OSError(errno.EHOSTUNREACH, "No route to host"))
    assert "192.168.1.50" in msg
    assert "rota" in msg.lower()


def test_extra_pausa_nao_infla_distancia(service, source, on_track, flush):
    """Tempo parado não pode virar distância percorrida."""
    from tests.conftest import FakeFrame

    service.start()
    for i in range(30):
        source.feed(FakeFrame(lap_count=1, current_lap_ms=i * 16, speed_kmh=200))
    flush(0.1)
    distancia_antes = service._cumulative_distance

    for i in range(30):
        source.feed(
            FakeFrame(lap_count=1, current_lap_ms=480, speed_kmh=200, is_paused=True)
        )
    flush(0.1)

    assert service._cumulative_distance == pytest.approx(distancia_antes)


def test_extra_amostras_sobrevivem_ida_e_volta(laps, tracks, make_lap):
    """Gravar e reler não pode alterar nenhum dos 27 campos."""
    track_id = tracks.get_or_create("T")
    lap = make_lap(track_id=track_id, samples=150)
    originais = list(lap.points)

    lap_id = laps.save(lap)
    lidos = laps.load_points(lap_id)

    assert lidos == originais
