"""Volta ideal, consistência, autonomia, resumo de sessão e detector de replay."""

from datetime import datetime, timedelta

import pytest

from src.application.events.events import ReplaySuspected
from src.application.services.session_summary import build_session_summary
from src.domain.models.lap import Lap
from src.domain.models.session import Session
from src.domain.models.track import Track
from src.domain.services.replay_detector import ReplayDetector
from src.domain.services.session_stats import (
    consistency_ms,
    fuel_estimate,
    ideal_lap,
)


# ------------------------------------------------------- volta ideal
def test_volta_ideal_junta_o_melhor_de_cada_setor():
    ideal = ideal_lap({
        1: [30000, 31000, 29000],
        2: [29500, 31500, 29200],
        3: [30200, 30800, 29100],
    })
    assert ideal.sector_ms == [29500, 30800, 29000]
    assert ideal.total_ms == 89300
    assert ideal.source_lap_ids == [2, 3, 1]


def test_volta_ideal_ignora_setor_sem_tempo():
    ideal = ideal_lap({1: [30000, None, 29000], 2: [None, 31000, 29500]})
    assert ideal.sector_ms == [30000, 31000, 29000]
    assert ideal.total_ms == 90000


def test_volta_ideal_incompleta_nao_tem_total():
    """Somar só os setores que existem daria um tempo de volta impossível."""
    ideal = ideal_lap({1: [30000, None, 29000]})
    assert ideal.total_ms is None


def test_volta_ideal_sem_voltas():
    assert ideal_lap({}).total_ms is None


# ------------------------------------------------------- consistência
def test_consistencia_de_voltas_iguais_e_zero():
    assert consistency_ms([90000, 90000, 90000]) == pytest.approx(0.0)


def test_consistencia_cresce_com_a_dispersao():
    assert consistency_ms([90000, 90200]) < consistency_ms([90000, 92000])


def test_consistencia_precisa_de_duas_voltas():
    assert consistency_ms([90000]) is None


# ------------------------------------------------------- combustível
def test_autonomia_pela_media_das_ultimas_voltas():
    est = fuel_estimate([2.0, 2.2, 1.8], current_level=20.0)
    assert est.per_lap == pytest.approx(2.0)
    assert est.laps_remaining == pytest.approx(10.0)


def test_reabastecimento_nao_entra_na_media():
    """Consumo negativo é parada de box, e zero é combustível infinito."""
    est = fuel_estimate([2.0, -30.0, 0.0, 2.0], current_level=10.0)
    assert est.per_lap == pytest.approx(2.0)
    assert est.laps_remaining == pytest.approx(5.0)


def test_sem_consumo_medido_nao_ha_estimativa():
    est = fuel_estimate([], current_level=40.0)
    assert est.per_lap is None and est.laps_remaining is None


# ------------------------------------------------------- resumo
def _sessao(tempos, pista="Interlagos"):
    inicio = datetime(2026, 1, 1, 10, 0, 0)
    return Session(
        track=Track(id=1, name=pista), start=inicio,
        end=inicio + timedelta(minutes=30),
        laps=[Lap(id=i, lap_time_ms=ms) for i, ms in enumerate(tempos, start=1)],
    )


def test_resumo_traz_melhor_media_e_consistencia():
    texto = build_session_summary(_sessao([91000, 90000, 92000]), ideal_ms=89500)
    assert "Interlagos" in texto
    assert "1:30.000" in texto          # melhor
    assert "1:31.000" in texto          # média
    assert "consistência" in texto
    assert "1:29.500" in texto          # ideal
    assert "+0.500s" in texto           # margem até a ideal


def test_resumo_de_sessao_sem_voltas_e_vazio():
    """Desconectar sem ter rodado não merece uma janela na cara."""
    assert build_session_summary(_sessao([])) == ""


def test_resumo_desconsidera_volta_parcial_e_invalida():
    sessao = _sessao([91000, 60000, 90000])
    sessao.laps[1].is_complete = False
    texto = build_session_summary(sessao)
    assert "voltas que contam: 2" in texto
    assert "1:30.000" in texto and "1:00.000" not in texto


# ------------------------------------------------------- replay
def test_pilotagem_normal_nao_levanta_suspeita():
    det = ReplayDetector()
    for volta in (1, 2, 3):
        for ms in range(0, 90000, 500):
            assert det.feed(volta, ms) is False
    assert det.evidence == 0


def test_rebobinar_duas_vezes_levanta_suspeita_uma_unica_vez():
    det = ReplayDetector()
    avisos = []
    for ms in (10000, 20000, 30000, 12000, 22000, 32000, 15000, 25000, 9000):
        avisos.append(det.feed(1, ms))
    assert avisos.count(True) == 1, "o aviso não pode se repetir a cada pacote"
    assert det.evidence >= 2


def test_um_recuo_isolado_nao_basta():
    det = ReplayDetector()
    resultados = [det.feed(1, ms) for ms in (10000, 20000, 12000, 22000, 32000)]
    assert not any(resultados)


def test_reiniciar_a_sessao_nao_e_replay():
    """Reiniciar também volta o relógio — mas volta para zero."""
    det = ReplayDetector()
    resultados = []
    for _ in range(4):
        for ms in (0, 10000, 30000, 50000):
            resultados.append(det.feed(1, ms))
    assert not any(resultados)
    assert det.evidence == 0


def test_reordenacao_de_pacote_nao_conta():
    det = ReplayDetector()
    resultados = [det.feed(1, ms) for ms in (10000, 10016, 10000, 10033, 10016, 10050)]
    assert not any(resultados)


def test_servico_publica_a_suspeita(service, source, bus, collect, flush):
    from tests.conftest import FakeFrame

    eventos = collect(ReplaySuspected)
    service.start()
    for ms in (10000, 20000, 30000, 12000, 22000, 32000, 15000):
        source.feed(FakeFrame(lap_count=1, current_lap_ms=ms))
    flush(0.1)
    assert len(eventos) == 1


def test_servico_respeita_a_preferencia_desligada(source, laps, session, bus, collect, flush, qapp):
    from src.application.services.telemetry_service import TelemetryService
    from src.domain.config import AppConfig
    from tests.conftest import FakeFrame

    eventos = collect(ReplaySuspected)
    svc = TelemetryService(
        telemetry_source=source, lap_repository=laps, session_manager=session,
        event_bus=bus, config=AppConfig(replay_detection=False),
    )
    try:
        svc.start()
        for ms in (10000, 20000, 30000, 12000, 22000, 32000, 15000):
            source.feed(FakeFrame(lap_count=1, current_lap_ms=ms))
        flush(0.1)
        assert not eventos
    finally:
        svc.stop()
