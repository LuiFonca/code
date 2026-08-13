"""
Contrato de `LapRepository`, verificado nas duas implementações.

A mesma bateria roda contra SQLite e contra JSON. É o teste que decide se a
abstração do repositório está certa: se a implementação em arquivo precisar de
um método que o contrato não prevê, ou se comportar diferente em algum caso,
aparece aqui.

Escrever isto **antes** da implementação JSON não é formalidade — é o que
transforma "implementar depois" em uma especificação executável.
"""

import pytest

from src.domain.models.lap import Lap
from src.infrastructure.repositories.sqlite_database import SqliteDatabase
from src.infrastructure.repositories.sqlite_lap_repository import SqliteLapRepository
from src.infrastructure.repositories.sqlite_track_repository import (
    SqliteTrackRepository,
)
from src.infrastructure.storage.file_lap_storage import FileLapStorage


@pytest.fixture(params=["sqlite", "json"])
def repo(request, tmp_path):
    """Uma das duas implementações, com uma pista já criada.

    Devolve `(repositorio, track_id)`. O SQLite exige que a pista exista por
    causa da chave estrangeira; o JSON não tem essa restrição, mas recebe o
    mesmo id para os testes ficarem idênticos.
    """
    if request.param == "sqlite":
        db = SqliteDatabase(":memory:")
        track_id = SqliteTrackRepository(db).get_or_create("Pista")
        yield SqliteLapRepository(db), track_id
        db.close()
    else:
        yield FileLapStorage(tmp_path / "voltas"), 1


def _lap(track_id, lap_time_ms=90000, samples=60, **kwargs):
    from tests.conftest import make_point

    return Lap(
        track_id=track_id,
        lap_time_ms=lap_time_ms,
        points=[make_point(i, samples, lap_time_ms, 3600.0) for i in range(samples + 1)],
        **kwargs,
    )


# ------------------------------------------------------------------ escrita
def test_save_devolve_id_utilizavel(repo):
    repositorio, track_id = repo
    lap_id = repositorio.save(_lap(track_id))
    assert lap_id is not None
    assert repositorio.get_by_id(lap_id) is not None


def test_ids_sao_distintos(repo):
    repositorio, track_id = repo
    primeiro = repositorio.save(_lap(track_id))
    segundo = repositorio.save(_lap(track_id))
    assert primeiro != segundo


# ------------------------------------------------------------------ leitura
def test_get_by_id_traz_amostras(repo):
    repositorio, track_id = repo
    original = _lap(track_id, samples=80)
    lap_id = repositorio.save(original)

    lido = repositorio.get_by_id(lap_id)
    assert len(lido.points) == 81
    assert lido.points == original.points, "os 27 campos precisam sobreviver"


def test_get_by_id_inexistente_devolve_none(repo):
    repositorio, _ = repo
    assert repositorio.get_by_id(999999) is None


def test_listagem_nao_carrega_amostras(repo):
    """Histórico com 50 voltas não pode carregar centenas de milhares de pontos."""
    repositorio, track_id = repo
    repositorio.save(_lap(track_id))

    for lap in repositorio.get_by_track(track_id):
        assert not lap.has_points


def test_listagem_ordena_da_mais_recente_para_a_mais_antiga(repo):
    import time

    repositorio, track_id = repo
    primeiro = repositorio.save(_lap(track_id, lap_time_ms=90000))
    time.sleep(0.01)
    segundo = repositorio.save(_lap(track_id, lap_time_ms=91000))

    ids = [lap.id for lap in repositorio.get_by_track(track_id)]
    assert ids.index(segundo) < ids.index(primeiro)


def test_load_points_equivale_ao_get_by_id(repo):
    repositorio, track_id = repo
    lap_id = repositorio.save(_lap(track_id, samples=40))
    assert repositorio.load_points(lap_id) == repositorio.get_by_id(lap_id).points


# ------------------------------------------------------------------ recorde
def test_get_best_e_o_menor_tempo(repo):
    repositorio, track_id = repo
    repositorio.save(_lap(track_id, lap_time_ms=92000))
    rapida = repositorio.save(_lap(track_id, lap_time_ms=88000))
    repositorio.save(_lap(track_id, lap_time_ms=90000))

    assert repositorio.get_best(track_id).lap_time_ms == 88000
    assert repositorio.get_best(track_id).id == rapida


def test_get_best_ignora_incompleta(repo):
    repositorio, track_id = repo
    repositorio.save(_lap(track_id, lap_time_ms=80000, is_complete=False))
    completa = repositorio.save(_lap(track_id, lap_time_ms=95000))

    assert repositorio.get_best(track_id).id == completa


def test_get_best_ignora_invalida(repo):
    repositorio, track_id = repo
    invalida = repositorio.save(_lap(track_id, lap_time_ms=80000, is_valid=False))
    valida = repositorio.save(_lap(track_id, lap_time_ms=95000))

    melhor = repositorio.get_best(track_id)
    assert melhor.id == valida
    assert melhor.id != invalida


def test_get_best_desempata_pelo_id_mais_antigo(repo):
    repositorio, track_id = repo
    primeiro = repositorio.save(_lap(track_id, lap_time_ms=88000))
    repositorio.save(_lap(track_id, lap_time_ms=88000))

    assert repositorio.get_best(track_id).id == primeiro


def test_get_best_sem_voltas_devolve_none(repo):
    repositorio, _ = repo
    assert repositorio.get_best(12345) is None


def test_get_top_respeita_o_limite(repo):
    repositorio, track_id = repo
    for ms in (92000, 88000, 90000, 89000):
        repositorio.save(_lap(track_id, lap_time_ms=ms))

    topo = repositorio.get_top(track_id, limit=2)
    assert [lap.lap_time_ms for lap in topo] == [88000, 89000]


# ------------------------------------------------------------------ setores
def test_setores_sao_calculados_na_gravacao(repo):
    repositorio, track_id = repo
    lap_id = repositorio.save(_lap(track_id, lap_time_ms=90000, samples=120))

    setores = repositorio.get_sector_times(lap_id)
    assert len(setores) == 3
    assert sum(s for s in setores if s) == pytest.approx(90000, rel=0.05)


def test_setores_em_lote(repo):
    repositorio, track_id = repo
    ids = [repositorio.save(_lap(track_id)) for _ in range(3)]

    lote = repositorio.get_sector_times_batch(ids)
    assert set(lote) == set(ids)
    assert all(len(v) == 3 for v in lote.values())


def test_setores_em_lote_com_lista_vazia(repo):
    repositorio, _ = repo
    assert repositorio.get_sector_times_batch([]) == {}


# ------------------------------------------------------------------ exclusão
def test_delete_remove_volta_e_amostras(repo):
    repositorio, track_id = repo
    lap_id = repositorio.save(_lap(track_id))

    repositorio.delete(lap_id)
    assert repositorio.get_by_id(lap_id) is None
    assert repositorio.load_points(lap_id) == []


def test_delete_by_track_limpa_a_pista(repo):
    repositorio, track_id = repo
    for _ in range(3):
        repositorio.save(_lap(track_id))

    repositorio.delete_by_track(track_id)
    assert repositorio.get_by_track(track_id) == []


def test_delete_inexistente_nao_levanta(repo):
    repositorio, _ = repo
    repositorio.delete(999999)


# ------------------------------------------------------------------ validade
def test_set_valid_alterna_sem_apagar(repo):
    repositorio, track_id = repo
    lap_id = repositorio.save(_lap(track_id, lap_time_ms=85000))

    repositorio.set_valid(lap_id, False)
    assert repositorio.get_by_id(lap_id).is_valid is False
    assert repositorio.get_by_id(lap_id) is not None

    repositorio.set_valid(lap_id, True)
    assert repositorio.get_by_id(lap_id).is_valid is True


# ==================== exportar / importar (Fase 3) ==========================
def test_exportar_e_reimportar_preserva_tudo(tmp_path):
    """Ida e volta por arquivo não pode alterar nenhum dos 27 campos."""
    origem = FileLapStorage(tmp_path / "origem")
    lap = _lap(track_id=7, lap_time_ms=87500, samples=120)
    lap_id = origem.save(lap)
    completa = origem.get_by_id(lap_id)

    arquivo = origem.export_lap(completa, tmp_path / "saida" / "volta.json")
    assert arquivo.exists()

    relida = FileLapStorage.read_lap_file(arquivo)
    assert relida.points == completa.points, "as amostras precisam bater exatamente"
    assert relida.lap_time_ms == completa.lap_time_ms
    assert relida.is_complete == completa.is_complete
    assert relida.is_valid == completa.is_valid


def test_exportar_do_sqlite_e_importar_no_json(tmp_path):
    """O arquivo precisa atravessar implementações diferentes."""
    db = SqliteDatabase(":memory:")
    track_id = SqliteTrackRepository(db).get_or_create("Pista")
    sqlite_repo = SqliteLapRepository(db)
    lap_id = sqlite_repo.save(_lap(track_id, lap_time_ms=91234, samples=90))
    original = sqlite_repo.get_by_id(lap_id)

    destino = tmp_path / "exportada.json"
    FileLapStorage(tmp_path / "scratch").export_lap(original, destino)

    relida = FileLapStorage.read_lap_file(destino)
    assert relida.points == original.points
    assert relida.lap_time_ms == 91234
    db.close()


def test_arquivo_corrompido_da_erro_legivel(tmp_path):
    from src.infrastructure.storage.file_lap_storage import UnsupportedLapFile

    ruim = tmp_path / "ruim.json"
    ruim.write_text("{isto não é json")

    with pytest.raises(UnsupportedLapFile) as exc:
        FileLapStorage.read_lap_file(ruim)
    assert "JSON" in str(exc.value)


def test_versao_futura_e_recusada(tmp_path):
    import json as _json

    from src.infrastructure.storage.file_lap_storage import UnsupportedLapFile

    futuro = tmp_path / "futuro.json"
    futuro.write_text(_json.dumps({"format_version": 999, "points": []}))

    with pytest.raises(UnsupportedLapFile) as exc:
        FileLapStorage.read_lap_file(futuro)
    assert "999" in str(exc.value)


def test_arquivo_de_versao_anterior_completa_campos_ausentes(tmp_path):
    """Volta exportada por versão mais antiga não pode desalinhar as amostras."""
    import json as _json

    from src.domain.models.telemetry_point import TelemetryPoint

    antigo = tmp_path / "antigo.json"
    antigo.write_text(
        _json.dumps({
            "format_version": 1,
            "lap_time_ms": 90000,
            "point_fields": ["elapsed_ms", "distance_m", "speed_kmh"],
            "points": [[0, 0.0, 100.0], [1000, 50.0, 120.0]],
        })
    )

    lap = FileLapStorage.read_lap_file(antigo)
    assert len(lap.points) == 2
    assert lap.points[1].speed_kmh == 120.0
    assert lap.points[1].rpm is None, "campo ausente vira None, não lixo"
    assert isinstance(lap.points[0], TelemetryPoint)


def test_arquivo_estranho_na_pasta_nao_derruba_listagem(tmp_path):
    repo = FileLapStorage(tmp_path / "voltas")
    repo.save(_lap(track_id=1))
    (tmp_path / "voltas" / "lap-999.json").write_text("lixo")

    assert len(repo.get_by_track(1)) == 1, "a volta boa continua acessível"
