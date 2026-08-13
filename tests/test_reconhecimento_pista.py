"""
Reconhecimento de pista pelo traçado.

O problema que isto resolve: uma volta rodada sem pista definida era
simplesmente descartada. O piloto só descobria no fim da sessão, com os dados
já perdidos — o pior momento possível para dar a notícia.

Agora a volta é sempre gravada, e o app tenta descobrir a pista comparando o
desenho do traçado com o das pistas que já conhece.

As pistas dos testes são geradas por fórmula, com raios e centros escolhidos.
Traçado sintético não é limitação aqui: o que se verifica é a decisão — casar o
parecido, separar o diferente e **recusar** quando dois candidatos disputam de
perto.
"""

import math

import pytest

from src.application.services.track_identifier import TrackIdentifier
from src.domain.models.telemetry_point import TelemetryPoint
from src.domain.services.track_fingerprint import (
    MAX_DESVIO_MEDIO_M,
    build_fingerprint,
    desvio_medio,
    identify_track,
)


def _pista_circular(raio: float, n: int = 400, centro=(0.0, 0.0), desvio_linha=0.0):
    """Volta circular. `desvio_linha` simula outra linha de pilotagem."""
    pontos = []
    perimetro = 2 * math.pi * raio
    for i in range(n + 1):
        fracao = i / n
        angulo = 2 * math.pi * fracao
        r = raio + desvio_linha * math.sin(angulo * 3)
        pontos.append(
            TelemetryPoint(
                elapsed_ms=int(fracao * 90000), distance_m=fracao * perimetro,
                speed_kmh=150.0, rpm=6000.0, gear=4, throttle=90.0, brake=0.0,
                fuel_level=50.0,
                tire_temp_fl=80.0, tire_temp_fr=80.0,
                tire_temp_rl=80.0, tire_temp_rr=80.0,
                position_x=centro[0] + r * math.cos(angulo),
                position_z=centro[1] + r * math.sin(angulo),
                g_lateral=0.5, g_longitudinal=0.0,
                suspension_fl=1.0, suspension_fr=1.0,
                suspension_rl=1.0, suspension_rr=1.0,
                tire_slip_fl=0.05, tire_slip_fr=0.05,
                tire_slip_rl=0.05, tire_slip_rr=0.05,
                turbo_boost=1.0, oil_temp=95.0, water_temp=88.0,
            )
        )
    return pontos


# ------------------------------------------------------- assinatura
def test_assinatura_tem_tamanho_fixo():
    fp = build_fingerprint(_pista_circular(600.0))
    assert fp is not None
    assert len(fp) == 64


def test_assinatura_independe_do_numero_de_amostras():
    """Volta rápida e volta lenta têm contagens diferentes de amostra.

    Se a assinatura dependesse disso, a mesma pista rodada em ritmos diferentes
    pareceria duas pistas.
    """
    rapida = build_fingerprint(_pista_circular(600.0, n=200))
    lenta = build_fingerprint(_pista_circular(600.0, n=900))
    assert desvio_medio(rapida, lenta) < 20.0


def test_volta_sem_posicao_nao_gera_assinatura(make_lap):
    """Voltas anteriores ao schema v4 não têm traçado — e isso não é erro."""
    pontos = make_lap(samples=300).points
    for p in pontos:
        p.position_x = None
        p.position_z = None
    assert build_fingerprint(pontos) is None


def test_volta_curta_demais_nao_gera_assinatura():
    """Saída de box ou abandono casaria com qualquer coisa."""
    assert build_fingerprint(_pista_circular(30.0)) is None


# ------------------------------------------------------- comparação
def test_mesma_pista_com_linha_diferente_casa():
    """Linhas de pilotagem diferentes precisam continuar sendo a mesma pista."""
    a = build_fingerprint(_pista_circular(600.0))
    b = build_fingerprint(_pista_circular(600.0, desvio_linha=8.0))
    assert desvio_medio(a, b) < MAX_DESVIO_MEDIO_M


def test_pistas_diferentes_nao_casam():
    a = build_fingerprint(_pista_circular(600.0))
    b = build_fingerprint(_pista_circular(600.0, centro=(5000.0, 3000.0)))
    assert desvio_medio(a, b) > MAX_DESVIO_MEDIO_M


# ------------------------------------------------------- decisão
def test_identifica_a_pista_certa():
    alvo = build_fingerprint(_pista_circular(600.0, desvio_linha=5.0))
    candidatas = {
        1: build_fingerprint(_pista_circular(600.0)),
        2: build_fingerprint(_pista_circular(1200.0)),
        3: build_fingerprint(_pista_circular(600.0, centro=(9000.0, 0.0))),
    }
    resultado = identify_track(alvo, candidatas)
    assert resultado is not None
    assert resultado[0] == 1


def test_sem_candidatas_nao_decide():
    alvo = build_fingerprint(_pista_circular(600.0))
    assert identify_track(alvo, {}) is None


def test_pista_desconhecida_nao_e_forcada_na_mais_parecida():
    """O erro clássico: escolher o menos ruim entre opções todas erradas.

    Errar a pista é pior que não saber — as voltas vão parar no histórico de
    outro circuito e estragam recordes e comparações em silêncio.
    """
    alvo = build_fingerprint(_pista_circular(600.0, centro=(20000.0, 20000.0)))
    candidatas = {
        1: build_fingerprint(_pista_circular(600.0)),
        2: build_fingerprint(_pista_circular(900.0)),
    }
    assert identify_track(alvo, candidatas) is None


def test_candidata_unica_distante_e_recusada():
    """Com um só candidato não há margem para avaliar — só o limiar segura.

    É o caso da primeira sessão num circuito novo tendo apenas uma pista no
    banco: sem o limiar, essa única pista ganharia por não ter concorrência.
    """
    alvo = build_fingerprint(_pista_circular(600.0, centro=(20000.0, 20000.0)))
    candidatas = {1: build_fingerprint(_pista_circular(600.0))}
    assert identify_track(alvo, candidatas) is None


def test_dois_candidatos_parecidos_nao_decidem():
    """Sem folga sobre o segundo colocado, a resposta honesta é 'não sei'."""
    alvo = build_fingerprint(_pista_circular(600.0))
    candidatas = {
        1: build_fingerprint(_pista_circular(600.0, desvio_linha=6.0)),
        2: build_fingerprint(_pista_circular(600.0, desvio_linha=-6.0)),
    }
    assert identify_track(alvo, candidatas) is None


# ------------------------------------------------------- persistência
def test_aprende_e_reconhece_pelo_banco(tracks, database):
    identificador = TrackIdentifier(tracks)
    tid = tracks.get_or_create("Circuito A")
    tracks.get_or_create("Circuito B")

    assert identificador.learn(tid, _pista_circular(600.0)) is True
    # Não sobrescreve: a referência não pode oscilar com a linha do dia.
    assert identificador.learn(tid, _pista_circular(600.0, desvio_linha=20.0)) is False

    achado = identificador.identify(_pista_circular(600.0, desvio_linha=5.0))
    assert achado is not None
    assert achado[0] == tid
    assert achado[1] == "Circuito A"


def test_assinatura_corrompida_no_banco_nao_derruba(tracks):
    """O banco é um arquivo do usuário; ler lixo não pode impedir a gravação."""
    tid = tracks.get_or_create("Pista")
    tracks._conn.execute(
        "UPDATE tracks SET map_fingerprint = ? WHERE id = ?", ("{lixo", tid)
    )
    tracks._conn.commit()

    assert tracks.get_fingerprint(tid) is None
    assert tracks.all_fingerprints() == {}
    assert TrackIdentifier(tracks).identify(_pista_circular(600.0)) is None


def test_pista_sem_assinatura_nao_participa(tracks):
    tracks.get_or_create("Sem traçado")
    assert TrackIdentifier(tracks).identify(_pista_circular(600.0)) is None


# ------------------------------------------------------- fim a fim
def _alimentar_traçado(source, lap_no, pontos, lap_ms=90000):
    """Emite uma volta inteira seguindo um traçado, com a virada que a fecha."""
    from tests.conftest import FakeFrame

    n = len(pontos) - 1
    for i, p in enumerate(pontos):
        source.feed(
            FakeFrame(
                lap_count=lap_no,
                current_lap_ms=int(i * lap_ms / n),
                position_x=p.position_x,
                position_z=p.position_z,
                speed_kmh=150.0,
            )
        )
    source.feed(
        FakeFrame(lap_count=lap_no + 1, current_lap_ms=0, last_lap_ms=lap_ms)
    )


@pytest.fixture
def servico_com_identificador(source, laps, tracks, session, bus, qapp):
    from src.application.services.telemetry_service import TelemetryService

    svc = TelemetryService(
        telemetry_source=source,
        lap_repository=laps,
        session_manager=session,
        event_bus=bus,
        track_identifier=TrackIdentifier(tracks),
    )
    yield svc
    svc.stop()


def test_volta_sem_pista_e_gravada(servico_com_identificador, source, laps, flush):
    """O comportamento que o usuário pediu: nunca mais perder a volta.

    Antes, sem pista definida a volta era descartada — e o piloto descobria
    isso depois de já ter rodado.
    """
    svc = servico_com_identificador
    svc.start()
    pontos = _pista_circular(600.0, n=200)
    _alimentar_traçado(source, 1, pontos)
    _alimentar_traçado(source, 2, pontos)
    flush(0.5)

    todas = laps.get_all()
    assert todas, "a volta precisa ser gravada mesmo sem pista definida"
    assert any(lap.track_id is None for lap in todas), (
        "a primeira volta, sem pista conhecida, fica com o campo vazio"
    )


def test_pista_e_reconhecida_pelo_tracado(
    servico_com_identificador, source, laps, tracks, session, bus, flush, collect
):
    """O caminho completo: aprender com a pista escolhida, reconhecer depois."""
    from src.application.events.events import TrackRecognized
    from src.domain.models.track import Track

    svc = servico_com_identificador
    eventos = collect(TrackRecognized)
    pontos = _pista_circular(600.0, n=200)

    # 1) Com a pista escolhida à mão, o app aprende o traçado.
    tid = tracks.get_or_create("Interlagos")
    session.set_track(Track(id=tid, name="Interlagos"))
    svc.start()
    _alimentar_traçado(source, 1, pontos)
    _alimentar_traçado(source, 2, pontos)
    flush(0.5)
    assert tracks.get_fingerprint(tid) is not None, "o traçado devia ter sido aprendido"

    # 2) Sessão nova sem pista: o reconhecimento tem que fazer o trabalho.
    session.set_track(None)
    svc.reload_reference()
    _alimentar_traçado(source, 5, _pista_circular(600.0, n=200, desvio_linha=6.0))
    _alimentar_traçado(source, 6, _pista_circular(600.0, n=200, desvio_linha=6.0))
    flush(0.5)

    assert eventos, "o app devia ter reconhecido a pista"
    assert eventos[-1].track_name == "Interlagos"
    assert eventos[-1].deviation_m < MAX_DESVIO_MEDIO_M

    reconhecidas = [lap for lap in laps.get_by_track(tid) if lap.is_complete]
    assert len(reconhecidas) >= 2, (
        "a volta reconhecida precisa ser gravada já na pista certa"
    )


def test_falha_no_reconhecimento_nao_custa_a_volta(
    source, laps, session, bus, flush, qapp
):
    """Reconhecer é conveniência; gravar é o que não pode falhar."""
    from src.application.services.telemetry_service import TelemetryService

    class IdentificadorQuebrado:
        def identify(self, points):
            raise RuntimeError("banco fora do ar")

        def learn(self, track_id, points):
            raise RuntimeError("banco fora do ar")

    svc = TelemetryService(
        telemetry_source=source, lap_repository=laps, session_manager=session,
        event_bus=bus, track_identifier=IdentificadorQuebrado(),
    )
    try:
        svc.start()
        pontos = _pista_circular(600.0, n=200)
        _alimentar_traçado(source, 1, pontos)
        _alimentar_traçado(source, 2, pontos)
        flush(0.5)
        assert laps.get_all(), "a volta tinha que ser gravada apesar da falha"
    finally:
        svc.stop()
