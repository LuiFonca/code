"""
Fase 7 — sessão longa.

A fase pedia validação em sessão real de várias horas, o que exige PS5, GT7 e
alguém pilotando. Essa parte não dá para automatizar. O que **dá** é separar os
defeitos que só aparecem em sessão longa daqueles que dependem do hardware:
crescimento de memória, acúmulo de estado e vazamento de referências não têm
nada a ver com o PS5 — dependem apenas de muitas voltas passarem pelo serviço.

Este arquivo comprime uma sessão de horas em segundos: as mesmas voltas, os
mesmos caminhos de código, sem as pausas entre elas. O que ele não cobre — e
que continua sendo tarefa do usuário — é a fidelidade dos dados vindos do jogo
e a estabilidade da rede ao longo do tempo.
"""

import gc
import threading

import pytest

from src.application.events.events import LapCompleted
from src.domain.config import AppConfig


def _rodar_voltas(source, quantidade: int, samples: int = 120, base_ms: int = 90000):
    """Emite N voltas completas em sequência, como numa sessão contínua."""
    for n in range(1, quantidade + 1):
        # Tempos ligeiramente diferentes: voltas idênticas esconderiam
        # problemas na troca de referência de recorde.
        source.feed_lap(n, base_ms + (n % 7) * 250, samples=samples)


def test_sessao_longa_nao_acumula_amostras_na_memoria(
    service, source, laps, session, on_track, flush
):
    """O acúmulo que só uma sessão longa revela.

    A sessão guarda as voltas rodadas, e a `Lap` carrega todas as suas
    amostras. Guardar as amostras de cada volta rodada faz a memória crescer
    linearmente com o tempo de sessão e nunca cair: numa sessão de duas horas
    são dezenas de voltas de milhares de amostras cada, todas vivas até fechar
    o app. Em teste curto isso é invisível — que é exatamente o motivo de esta
    fase existir.

    O que a sessão precisa saber é *quais* voltas foram rodadas e seus tempos.
    As amostras pertencem ao repositório, e quem precisa delas em memória (os
    comparadores de delta) já mantém a sua própria referência.
    """
    service.start()
    _rodar_voltas(source, 12, samples=200)
    flush(0.5)

    rodadas = session.session.laps
    assert len(rodadas) == 12, "as voltas precisam continuar registradas na sessão"

    amostras_retidas = sum(len(lap.points) for lap in rodadas)
    assert amostras_retidas == 0, (
        f"a sessão está segurando {amostras_retidas} amostras de voltas já "
        "gravadas — em sessão longa isso cresce sem teto"
    )


def test_sessao_longa_preserva_o_que_a_sessao_precisa(
    service, source, laps, session, on_track, flush
):
    """Contraprova: soltar as amostras não pode apagar o resto.

    Tempo, completude e identificação continuam sendo usados por
    `best_lap`, `lap_count` e pelo resumo da sessão.
    """
    service.start()
    _rodar_voltas(source, 5, samples=80)
    flush(0.4)

    rodadas = session.session.laps
    assert all(lap.lap_time_ms > 0 for lap in rodadas)
    # A primeira fica de fora da checagem de completude: entramos no meio dela.
    assert all(lap.is_complete for lap in rodadas[1:])
    assert all(lap.id is not None for lap in rodadas), (
        "o id da gravação precisa chegar ao registro da sessão"
    )
    assert session.session.best_lap is not None
    assert session.session.best_lap.lap_time_ms == min(
        lap.lap_time_ms for lap in rodadas
    )
    assert session.session.last_lap is rodadas[-1]


def test_sessao_longa_mantem_o_buffer_limpo(service, source, on_track, flush):
    """O buffer da volta corrente não pode carregar sobras da anterior."""
    service.start()
    _rodar_voltas(source, 8, samples=100)
    flush(0.4)

    # Depois da última virada, o buffer tem apenas as amostras da volta nova.
    assert len(service._buffer) <= 2, (
        f"buffer com {len(service._buffer)} amostras logo após a virada"
    )


def test_sessao_longa_respeita_a_retencao(
    source, laps, session, bus, on_track, flush, qapp
):
    """Com muitas voltas, o banco não pode crescer indefinidamente.

    Retenção configurada baixa para o teste ser rápido; o que se verifica é
    que o teto existe e é respeitado ao longo de muitas voltas, não o valor.
    """
    from src.application.services.telemetry_service import TelemetryService
    from src.infrastructure.repositories.sqlite_lap_repository import (
        SqliteLapRepository,
    )

    track_id, _ = on_track
    repo = SqliteLapRepository(laps._db, keep_best=3, keep_recent=3)
    svc = TelemetryService(
        telemetry_source=source,
        lap_repository=repo,
        session_manager=session,
        event_bus=bus,
    )
    try:
        svc.start()
        _rodar_voltas(source, 20, samples=40)
        flush(0.8)

        gravadas = repo.get_by_track(track_id)
        assert 0 < len(gravadas) <= 6, (
            f"{len(gravadas)} voltas no banco — a retenção não segurou"
        )
    finally:
        svc.stop()


def test_sessao_longa_nao_vaza_threads(service, source, on_track, flush):
    """A thread de gravação é uma só, por mais voltas que passem."""
    antes = threading.active_count()
    service.start()
    _rodar_voltas(source, 15, samples=60)
    flush(0.5)
    depois = threading.active_count()

    assert depois - antes <= 1, f"threads passaram de {antes} para {depois}"


def test_sessao_longa_nao_acumula_inscricoes_no_barramento(
    service, source, bus, on_track, flush
):
    """Nenhum caminho de volta pode inscrever handlers repetidamente.

    Uma inscrição por volta seria imperceptível em teste curto e, em sessão
    longa, faria cada evento ser entregue dezenas de vezes.
    """
    service.start()
    source.feed_lap(1, 90000, samples=40)
    flush(0.2)
    inscritos_depois_de_uma = sum(len(h) for h in bus._handlers.values())

    _rodar_voltas(source, 10, samples=40)
    flush(0.5)
    inscritos_depois_de_onze = sum(len(h) for h in bus._handlers.values())

    assert inscritos_depois_de_onze == inscritos_depois_de_uma


def test_sessao_longa_mantem_o_delta_correto(
    service, source, laps, session, bus, on_track, flush, collect
):
    """O recorde precisa acompanhar a sessão inteira, não só as primeiras voltas.

    Vinte voltas com a mais rápida no meio: se a referência de melhor volta se
    perder ou congelar, isto aparece.
    """
    eventos = collect(LapCompleted)
    service.start()

    tempos = [92000, 91500, 91000, 88000, 90500, 90000, 89500]
    for n, ms in enumerate(tempos, start=1):
        source.feed_lap(n, ms, samples=60)
    flush(0.6)

    # A primeira volta fica de fora por não ter sido observada desde a largada
    # — entramos no meio dela. É a regra que impede uma volta parcial de virar
    # recorde, e vale igual aqui.
    melhores = [e.lap.lap_time_ms for e in eventos if e.is_best]
    assert melhores == [91500, 91000, 88000], (
        f"sequência de recordes inesperada: {melhores}"
    )
    assert service._best_lap_time_ms == 88000


@pytest.mark.parametrize("voltas", [30])
def test_sessao_longa_memoria_estavel(service, source, on_track, flush, voltas):
    """Medida direta: objetos vivos não podem crescer com o número de voltas.

    Compara a contagem de `TelemetryPoint` vivos depois de poucas voltas e
    depois de muitas. Um crescimento proporcional ao número de voltas é o
    sintoma que uma sessão de horas transforma em gigabytes.
    """
    from src.domain.models.telemetry_point import TelemetryPoint

    def _pontos_vivos() -> int:
        gc.collect()
        return sum(1 for o in gc.get_objects() if isinstance(o, TelemetryPoint))

    service.start()
    _rodar_voltas(source, 3, samples=100)
    flush(0.3)
    poucos = _pontos_vivos()

    _rodar_voltas(source, voltas, samples=100)
    flush(1.0)
    muitos = _pontos_vivos()

    # Tolerância generosa: os comparadores de melhor e anterior seguram duas
    # voltas por definição, e a volta corrente está no buffer. O que não pode
    # é o total crescer com o número de voltas rodadas.
    assert muitos <= poucos + 500, (
        f"pontos vivos: {poucos} após 3 voltas, {muitos} após mais {voltas} — "
        "a memória cresce com a sessão"
    )
