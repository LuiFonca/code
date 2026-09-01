"""
A volta deu a volta na pista?

Até a Fase 3 o programa aceitava qualquer volta que o jogo fechasse. Cortar uma
chicane, sair da pista e voltar com o *reset*, ou abandonar no meio produzia uma
volta gravada igual às outras — que entrava no recorde, na mediana e no perfil
do piloto sem nada dizendo que ela não valia.

O que estes testes travam é a assimetria que define a regra: a volta **curta** é
excluída porque produz um tempo **rápido** que é falso, e tempo falso vira
recorde — que é a referência do delta, do alvo e da comparação. A volta **longa**
fica: sair da pista acrescenta distância *e* tempo, então ela é mais lenta e
nunca disputaria o recorde. Excluí-la seria rigor sem efeito, e apagaria uma
volta que o piloto realmente deu.
"""

from __future__ import annotations

import datetime

import pytest

from gt7core.domain.models import Lap, TelemetryPoint
from gt7core.domain.validity import (
    MAX_COMPLETE_RATIO,
    MIN_COMPLETE_RATIO,
    LapValidity,
    classify_lap,
    describe_coverage,
    lap_coverage,
)
from gt7core.storage.database import SqliteDatabase
from gt7core.storage.repositories import SqliteLapRepository, SqliteTrackRepository

SUZUKA_M = 5807.0


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


def _volta(comprimento_m: float, tempo_ms: int, n: int = 400) -> list[TelemetryPoint]:
    return [
        _ponto(int(i / (n - 1) * tempo_ms), i / (n - 1) * comprimento_m)
        for i in range(n)
    ]


@pytest.fixture
def banco() -> SqliteDatabase:
    return SqliteDatabase(":memory:")


@pytest.fixture
def voltas(banco: SqliteDatabase) -> SqliteLapRepository:
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


class TestAClassificacao:
    @pytest.mark.parametrize(
        ("distancia", "esperado"),
        [
            (5807.0, LapValidity.COMPLETE),
            (5750.0, LapValidity.COMPLETE),
            (5900.0, LapValidity.COMPLETE),
            (4000.0, LapValidity.INCOMPLETE),
            (7000.0, LapValidity.LONG),
        ],
    )
    def test_compara_a_distancia_com_o_tracado(
        self, distancia: float, esperado: LapValidity
    ) -> None:
        assert classify_lap(distancia, SUZUKA_M) is esperado

    def test_sem_comprimento_de_pista_nao_se_afirma_nada(self) -> None:
        """`UNKNOWN` não é sinônimo de válida.

        Pista fora do catálogo não tem com o que comparar, e dizer "completa"
        aí seria inventar uma verificação que não aconteceu.
        """
        assert classify_lap(5807.0, None) is LapValidity.UNKNOWN
        assert classify_lap(None, SUZUKA_M) is LapValidity.UNKNOWN
        assert lap_coverage(5807.0, None) is None
        assert describe_coverage(None) == "—"

    def test_so_a_incompleta_perde_o_direito_ao_recorde(self) -> None:
        """A assimetria inteira do módulo, numa asserção.

        Curta é excluída porque mente para menos. Longa fica porque se pune
        sozinha: mais distância significa mais tempo.
        """
        assert not LapValidity.INCOMPLETE.counts_as_record
        assert LapValidity.COMPLETE.counts_as_record
        assert LapValidity.LONG.counts_as_record
        assert LapValidity.UNKNOWN.counts_as_record

    def test_os_limiares_deixam_passar_a_variacao_normal_da_linha(self) -> None:
        """A folga é deliberada e assimétrica.

        Marcar uma volta limpa como suja custa mais caro que deixar passar uma
        suja: a limpa some do recorde sem explicação, e o piloto não tem como
        saber por quê. Um corte marginal de chicane fica dentro da faixa — de
        propósito, até haver telemetria real com que calibrar.
        """
        assert MIN_COMPLETE_RATIO <= 0.95
        assert MAX_COMPLETE_RATIO >= 1.05
        assert classify_lap(SUZUKA_M * 0.98, SUZUKA_M) is LapValidity.COMPLETE

    def test_a_volta_em_memoria_ja_sabe_dizer(self) -> None:
        """A queda para as amostras é o que faz a validade valer na volta que
        acabou de fechar e ainda não foi para o banco."""
        lap = Lap(track_length_m=SUZUKA_M, points=_volta(4000.0, 100_000))
        assert lap.distance_m is None
        assert lap.measured_distance_m == pytest.approx(4000.0)
        assert lap.validity is LapValidity.INCOMPLETE


class TestOQueOAcervoFazComElas:
    def test_a_volta_incompleta_nao_vira_recorde(
        self, voltas: SqliteLapRepository, pistas: SqliteTrackRepository
    ) -> None:
        """O defeito que a Fase 3 fecha.

        Um tempo rápido falso aqui contamina tudo o que depende do recorde: o
        delta na tela, o alvo da sessão e a referência da Comparação.
        """
        track_id = pistas.get_or_create("Suzuka Circuit", length_m=SUZUKA_M)
        limpa = _grava(voltas, track_id, 5820.0, 125_000)
        _grava(voltas, track_id, 4100.0, 90_000)  # cortou meia pista

        melhor = voltas.get_best(track_id)
        assert melhor is not None
        assert melhor.id == limpa
        assert melhor.lap_time_ms == 125_000

    def test_a_volta_longa_continua_elegivel(
        self, voltas: SqliteLapRepository, pistas: SqliteTrackRepository
    ) -> None:
        """Ela é uma volta que o piloto deu; e sendo mais lenta, não ganha nada
        por ser elegível."""
        track_id = pistas.get_or_create("Suzuka Circuit", length_m=SUZUKA_M)
        longa = _grava(voltas, track_id, 6900.0, 118_000)
        _grava(voltas, track_id, 5820.0, 125_000)

        melhor = voltas.get_best(track_id)
        assert melhor is not None
        assert melhor.id == longa
        assert melhor.validity is LapValidity.LONG

    def test_sem_catalogo_ninguem_e_excluido(
        self, voltas: SqliteLapRepository, pistas: SqliteTrackRepository
    ) -> None:
        """Não se acusa de incompleta uma volta que ninguém teve como medir."""
        track_id = pistas.get_or_create("Pista sem catálogo")
        curta = _grava(voltas, track_id, 2000.0, 45_000)
        _grava(voltas, track_id, 5820.0, 125_000)

        melhor = voltas.get_best(track_id)
        assert melhor is not None
        assert melhor.id == curta
        assert melhor.validity is LapValidity.UNKNOWN

    def test_a_distancia_e_gravada_e_volta_na_leitura(
        self, voltas: SqliteLapRepository, pistas: SqliteTrackRepository
    ) -> None:
        """Ela precisa viver na linha da volta: a listagem do Histórico não
        carrega amostras, e sem a coluna a validade só existiria depois de ler
        milhares de linhas por volta."""
        track_id = pistas.get_or_create("Suzuka Circuit", length_m=SUZUKA_M)
        lap_id = _grava(voltas, track_id, 5820.0, 125_000)

        lap = next(x for x in voltas.get_by_track(track_id) if x.id == lap_id)
        assert not lap.has_points
        assert lap.distance_m == pytest.approx(5820.0)
        assert lap.track_length_m == pytest.approx(SUZUKA_M)
        assert lap.coverage == pytest.approx(5820.0 / SUZUKA_M)

    def test_a_pista_ganhando_catalogo_reclassifica_o_acervo(
        self, banco: SqliteDatabase,
        voltas: SqliteLapRepository, pistas: SqliteTrackRepository,
    ) -> None:
        """A validade é derivada na leitura, e não congelada na gravação.

        É o que faz o acervo inteiro se corrigir quando a pista é finalmente
        identificada, sem precisar reprocessar volta nenhuma.
        """
        track_id = pistas.get_or_create("Pista ainda sem nome")
        lap_id = _grava(voltas, track_id, 4100.0, 90_000)
        assert voltas.get_by_id(lap_id).validity is LapValidity.UNKNOWN

        banco.connection.execute(
            "UPDATE tracks SET length_m = ? WHERE id = ?", (SUZUKA_M, track_id)
        )
        banco.connection.commit()

        assert voltas.get_by_id(lap_id).validity is LapValidity.INCOMPLETE
        assert voltas.get_best(track_id) is None


class TestMigracaoDaDistancia:
    def test_o_acervo_antigo_e_preenchido_a_partir_das_amostras(
        self, tmp_path
    ) -> None:  # noqa: ANN001
        """Sem o preenchimento, toda volta gravada antes da v10 mostraria
        travessão numa coluna cujo dado está no banco — só que espalhado por
        milhares de linhas de `lap_frames`."""
        caminho = tmp_path / "antigo.db"
        antigo = SqliteDatabase(str(caminho))
        pistas = SqliteTrackRepository(antigo)
        voltas = SqliteLapRepository(
            antigo, keep_recent_per_track=0, keep_best_per_track=0
        )
        track_id = pistas.get_or_create("Suzuka Circuit", length_m=SUZUKA_M)
        lap_id = _grava(voltas, track_id, 5820.0, 125_000)

        # Volta ao estado anterior: coluna zerada e versão do schema recuada.
        antigo.connection.execute("UPDATE laps SET distance_m = NULL")
        antigo.connection.execute("PRAGMA user_version = 9")
        antigo.connection.commit()
        antigo.close()

        migrado = SqliteDatabase(str(caminho))
        distancia = migrado.connection.execute(
            "SELECT distance_m FROM laps WHERE id = ?", (lap_id,)
        ).fetchone()[0]
        assert distancia == pytest.approx(5820.0)
        migrado.close()
