"""
Uma definição de setor, ancorada no asfalto.

Este arquivo existe porque o caminho dos setores **não tinha teste nenhum** —
foi possível trocar a fonte inteira dos números do Histórico sem que uma única
asserção reclamasse. Foi essa ausência que deixou três definições de "setor 2"
conviverem no mesmo programa sem ninguém notar:

- o que era gravado ancorava na mediana das 10 últimas voltas;
- a tabela do Histórico ancorava na melhor volta da pista;
- as marcas no mapa ancoravam na própria volta.

As três divisas caíam a 5 m uma da outra, e o setor 1 da mesma volta valia
31.786 ms no banco e 31.622 ms na tela.

O que se testa aqui são as **propriedades** que tornam um tempo de setor útil
para treinar, não os números de uma implementação:

1. a divisa cai no mesmo ponto físico em toda volta da pista;
2. os setores somam o tempo da volta;
3. bater um recorde não reescreve o passado;
4. o valor guardado é lido, e não recalculado — mas só enquanto a régua for a
   mesma.
"""

from __future__ import annotations

import datetime

import pytest

from gt7core.analytics.sectors import NUM_SECTORS, resolve_anchor, sector_boundaries
from gt7core.catalog.catalog import GameCatalog
from gt7core.domain.models import Lap, TelemetryPoint
from gt7core.storage.database import SqliteDatabase
from gt7core.storage.repositories import SqliteLapRepository, SqliteTrackRepository

SUZUKA_M = 5807.0
"""Comprimento oficial no `track_list.csv`. Um número do jogo, não do programa."""


def _ponto(elapsed_ms: int, distance_m: float) -> TelemetryPoint:
    return TelemetryPoint(
        elapsed_ms=elapsed_ms, distance_m=distance_m, speed_kmh=180.0, rpm=6000.0,
        gear=5, throttle=100.0, brake=0.0, fuel_level=40.0,
        tire_temp_fl=80.0, tire_temp_fr=80.0, tire_temp_rl=80.0, tire_temp_rr=80.0,
        position_x=0.0, position_z=0.0, g_lateral=0.0, g_longitudinal=0.0,
        suspension_fl=0.1, suspension_fr=0.1, suspension_rl=0.1, suspension_rr=0.1,
        tire_slip_fl=27.7, tire_slip_fr=27.7, tire_slip_rl=27.7, tire_slip_rr=27.7,
        turbo_boost=1.0, oil_temp=90.0, water_temp=85.0,
    )


def _volta(comprimento_m: float, tempo_ms: int, n: int = 900) -> list[TelemetryPoint]:
    """Volta a velocidade constante: o tempo em cada trecho é proporcional à
    distância dele, o que torna o resultado esperado calculável à mão."""
    return [
        _ponto(int(i / (n - 1) * tempo_ms), i / (n - 1) * comprimento_m)
        for i in range(n)
    ]


@pytest.fixture
def banco() -> SqliteDatabase:
    return SqliteDatabase(":memory:")


@pytest.fixture
def voltas(banco: SqliteDatabase) -> SqliteLapRepository:
    # Retenção desligada: estes testes gravam voltas propositalmente ruins, e a
    # retenção as apagaria antes da asserção.
    return SqliteLapRepository(banco, keep_recent_per_track=0, keep_best_per_track=0)


@pytest.fixture
def pistas(banco: SqliteDatabase) -> SqliteTrackRepository:
    return SqliteTrackRepository(banco)


def _grava(
    voltas: SqliteLapRepository, track_id: int, comprimento_m: float, tempo_ms: int
) -> int:
    return voltas.save(
        Lap(
            id=None, session_id=None, track_id=track_id, car_id=None, is_player=True,
            lap_time_ms=tempo_ms, start_time=datetime.datetime.now(),
            points=_volta(comprimento_m, tempo_ms),
        )
    )


class TestOndeFicaADivisa:
    def test_o_catalogo_do_jogo_traz_o_comprimento_de_todas_as_pistas(self) -> None:
        """A âncora só serve se existir para toda pista que o piloto roda.

        Uma cobertura parcial mandaria metade do acervo para o caminho de
        emergência sem nada na tela dizendo por quê.
        """
        catalogo = GameCatalog()
        sem_comprimento = [t.name for t in catalogo.tracks.values() if not t.length_m]
        assert not sem_comprimento
        assert len(catalogo.tracks) >= 100

    def test_a_divisa_nao_se_move_entre_voltas_de_comprimentos_diferentes(self) -> None:
        """A propriedade que dá sentido a comparar setores.

        O hodômetro mede a linha percorrida, que muda a cada volta; a divisa
        não pode acompanhar essa variação, ou "setor 2" vira um pedaço de
        asfalto diferente a cada tentativa.
        """
        divisas = {
            sector_boundaries(comprimento, SUZUKA_M)[0]
            for comprimento in (5790.0, 5807.0, 5824.0, 5830.0)
        }
        assert len(divisas) == 1
        assert divisas.pop() == pytest.approx(SUZUKA_M / 3)

    def test_a_ultima_divisa_e_sempre_o_fim_da_propria_volta(self) -> None:
        """A linha de chegada é um fato, não uma estimativa.

        Ancorar também a última divisa deixava os metros entre o comprimento do
        catálogo e o fim real da volta sem dono.
        """
        for comprimento in (5790.0, 5824.0):
            assert sector_boundaries(comprimento, SUZUKA_M)[-1] == comprimento

    def test_sem_catalogo_cai_para_a_propria_volta(self) -> None:
        assert sector_boundaries(4000.0, None) == [
            pytest.approx(4000.0 / 3),
            pytest.approx(4000.0 * 2 / 3),
            4000.0,
        ]

    def test_ancora_absurda_e_ignorada(self) -> None:
        """Catálogo errado ou pista trocada: 900 m de âncora para uma volta de
        5,8 km poria as duas divisas interiores no primeiro sexto da volta."""
        divisas = sector_boundaries(5824.0, 900.0)
        assert divisas == sector_boundaries(5824.0, None)

    def test_volta_sem_distancia_nao_tem_setor(self) -> None:
        assert sector_boundaries(0.0, SUZUKA_M) == []
        assert sector_boundaries(-10.0, SUZUKA_M) == []

    def test_o_catalogo_ganha_da_mediana(self) -> None:
        assert resolve_anchor(SUZUKA_M, 5820.0) == SUZUKA_M
        assert resolve_anchor(None, 5820.0) == 5820.0
        assert resolve_anchor(None, None) is None


class TestOsNumerosQueChegamNaTela:
    def test_os_setores_somam_o_tempo_da_volta(
        self, voltas: SqliteLapRepository, pistas: SqliteTrackRepository
    ) -> None:
        """A conta que qualquer piloto confere primeiro.

        Ancorar as três divisas no catálogo quebrava isto: numa volta 17 m mais
        longa que o comprimento oficial, os setores somavam 369 ms a menos que
        o tempo da volta.
        """
        track_id = pistas.get_or_create("Suzuka Circuit", length_m=SUZUKA_M)
        for comprimento, tempo in ((5824.0, 125_400), (5790.0, 124_800)):
            lap_id = _grava(voltas, track_id, comprimento, tempo)
            setores = voltas.get_sector_times(lap_id)
            assert len(setores) == NUM_SECTORS
            assert sum(setores) == tempo

    def test_um_recorde_novo_nao_reescreve_o_passado(
        self, voltas: SqliteLapRepository, pistas: SqliteTrackRepository
    ) -> None:
        """O defeito que mais importava.

        Com a âncora na melhor volta, bater um recorde mudava o tempo de setor
        de voltas antigas que o piloto não tocou — medi 52 ms. Um número que se
        move sozinho não serve para treinar.
        """
        track_id = pistas.get_or_create("Suzuka Circuit", length_m=SUZUKA_M)
        antigas = [
            _grava(voltas, track_id, 5824.0, 125_400),
            _grava(voltas, track_id, 5819.0, 125_100),
        ]
        antes = voltas.sector_times_for_track(track_id, antigas)

        _grava(voltas, track_id, 5798.0, 121_000)  # recorde, e mais curta

        assert voltas.sector_times_for_track(track_id, antigas) == antes

    def test_a_ancora_usada_fica_gravada_com_a_volta(
        self, banco: SqliteDatabase,
        voltas: SqliteLapRepository, pistas: SqliteTrackRepository,
    ) -> None:
        """Sem esse registro não há como saber se um valor guardado ainda vale,
        e a única leitura segura seria recalcular tudo a cada abertura."""
        track_id = pistas.get_or_create("Suzuka Circuit", length_m=SUZUKA_M)
        lap_id = _grava(voltas, track_id, 5824.0, 125_400)
        gravada = banco.connection.execute(
            "SELECT sector_anchor_m FROM laps WHERE id = ?", (lap_id,)
        ).fetchone()[0]
        assert gravada == pytest.approx(SUZUKA_M)

    def test_le_o_que_esta_gravado_em_vez_de_recalcular(
        self, banco: SqliteDatabase,
        voltas: SqliteLapRepository, pistas: SqliteTrackRepository,
    ) -> None:
        """A leitura tem que ser leitura de verdade.

        Se `sector_times_for_track` recalculasse por baixo, os ~1,4 s que o
        Histórico gastava continuariam lá e o teste passaria mesmo assim. A
        prova é adulterar o valor guardado: só quem lê devolve o valor
        adulterado.
        """
        track_id = pistas.get_or_create("Suzuka Circuit", length_m=SUZUKA_M)
        lap_id = _grava(voltas, track_id, 5824.0, 125_400)

        banco.connection.execute(
            "UPDATE sector_times SET time_ms = 12345 "
            "WHERE lap_id = ? AND sector_index = 0",
            (lap_id,),
        )
        banco.connection.commit()

        assert voltas.sector_times_for_track(track_id, [lap_id])[lap_id][0] == 12345

    def test_regrava_quando_a_regua_muda(
        self, banco: SqliteDatabase,
        voltas: SqliteLapRepository, pistas: SqliteTrackRepository,
    ) -> None:
        """Uma pista que ganha comprimento de catálogo depois de já ter voltas.

        O conserto acontece uma vez e fica gravado; a alternativa seria a tela
        pagar o recálculo a cada abertura, para sempre.
        """
        track_id = pistas.get_or_create("Pista sem catálogo")
        lap_id = _grava(voltas, track_id, 5824.0, 125_400)
        sem_ancora = voltas.sector_times_for_track(track_id, [lap_id])[lap_id]

        banco.connection.execute(
            "UPDATE tracks SET length_m = ? WHERE id = ?", (SUZUKA_M, track_id)
        )
        banco.connection.commit()

        com_ancora = voltas.sector_times_for_track(track_id, [lap_id])[lap_id]
        assert com_ancora != sem_ancora

        # E ficou gravado: a segunda leitura já encontra a régua atual.
        assert banco.connection.execute(
            "SELECT sector_anchor_m FROM laps WHERE id = ?", (lap_id,)
        ).fetchone()[0] == pytest.approx(SUZUKA_M)
        assert voltas.sector_times_for_track(track_id, [lap_id])[lap_id] == com_ancora

    def test_pista_desconhecida_ainda_produz_setores(
        self, voltas: SqliteLapRepository, pistas: SqliteTrackRepository
    ) -> None:
        """Sem catálogo os setores continuam existindo e somando a volta — só
        deixam de ser comparáveis a longo prazo, e o rótulo da tela diz isso."""
        track_id = pistas.get_or_create("Pista de teste do usuário")
        lap_id = _grava(voltas, track_id, 3210.0, 88_000)
        setores = voltas.get_sector_times(lap_id)
        assert len(setores) == NUM_SECTORS
        assert sum(setores) == 88_000


class TestOComprimentoSobreviveAoBanco:
    def test_o_comprimento_do_catalogo_chega_e_volta(
        self, pistas: SqliteTrackRepository
    ) -> None:
        """Existia no modelo de domínio e morria na fronteira do banco: a
        tabela não tinha a coluna, e a identificação usava o comprimento para
        achar o nome e o descartava em seguida."""
        track_id = pistas.get_or_create("Suzuka Circuit", length_m=SUZUKA_M)
        assert pistas.get_by_id(track_id).length_m == pytest.approx(SUZUKA_M)
        assert next(
            t.length_m for t in pistas.get_all() if t.id == track_id
        ) == pytest.approx(SUZUKA_M)

    def test_completa_pista_que_ja_existia_sem_comprimento(
        self, pistas: SqliteTrackRepository
    ) -> None:
        track_id = pistas.get_or_create("Suzuka Circuit")
        assert pistas.get_by_id(track_id).length_m is None
        assert pistas.get_or_create("Suzuka Circuit", length_m=SUZUKA_M) == track_id
        assert pistas.get_by_id(track_id).length_m == pytest.approx(SUZUKA_M)

    def test_nao_sobrescreve_comprimento_ja_conhecido(
        self, pistas: SqliteTrackRepository
    ) -> None:
        """Trocar o comprimento move as divisas de todas as voltas já gravadas.

        Acontece com pistas de comprimento parecido: um palpite ambíguo
        resolvido para o vizinho errado redefiniria o acervo inteiro.
        """
        track_id = pistas.get_or_create("Suzuka Circuit", length_m=SUZUKA_M)
        pistas.get_or_create("Suzuka Circuit", length_m=4023.0)
        assert pistas.get_by_id(track_id).length_m == pytest.approx(SUZUKA_M)


class TestMigracao:
    def test_banco_novo_e_banco_migrado_chegam_ao_mesmo_schema(
        self, tmp_path
    ) -> None:  # noqa: ANN001
        """As colunas podem vir da criação ou da migração, e as duas têm que
        produzir o mesmo banco — um `ALTER TABLE` esquecido só aparece no
        acervo de quem já usava o programa."""
        caminho = tmp_path / "antigo.db"
        antigo = SqliteDatabase(str(caminho))
        antigo.connection.execute("PRAGMA user_version = 8")
        antigo.connection.execute("ALTER TABLE tracks DROP COLUMN length_m")
        antigo.connection.execute("ALTER TABLE laps DROP COLUMN sector_anchor_m")
        antigo.connection.commit()
        antigo.close()

        migrado = SqliteDatabase(str(caminho))
        colunas_tracks = {
            r[1] for r in migrado.connection.execute("PRAGMA table_info(tracks)")
        }
        colunas_laps = {
            r[1] for r in migrado.connection.execute("PRAGMA table_info(laps)")
        }
        assert "length_m" in colunas_tracks
        assert "sector_anchor_m" in colunas_laps
        migrado.close()
