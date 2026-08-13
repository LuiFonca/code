"""
Voltas sem pista: gravar, listar e resolver.

Reconhecer a pista pelo traçado (ver `test_reconhecimento_pista.py`) resolve o
caso comum, mas não todos: a primeira volta num circuito novo não tem com o que
casar. O que este arquivo protege é o resto do caminho — a volta não pode
sumir só porque o app não soube de onde ela é.

Gravar sem listar seria pior que não gravar: o piloto acharia que tinha os
dados e não teria onde vê-los.
"""

import pytest

from src.application.events.event_bus import EventBus
from src.application.viewmodels.history_viewmodel import HistoryViewModel


def test_volta_sem_pista_e_gravada_no_banco(laps, make_lap):
    lap_id = laps.save(make_lap(track_id=None, samples=50))
    assert lap_id
    assert laps.get_by_id(lap_id).track_id is None


def test_lista_de_voltas_sem_pista(laps, tracks, make_lap):
    """`get_by_track(None)` é um grupo próprio, não 'todas as voltas'."""
    tid = tracks.get_or_create("Com pista")
    laps.save(make_lap(track_id=tid, samples=30, lap_time_ms=90000))
    sem_a = laps.save(make_lap(track_id=None, samples=30, lap_time_ms=91000))
    sem_b = laps.save(make_lap(track_id=None, samples=30, lap_time_ms=92000))

    sem_pista = laps.get_by_track(None)
    assert {lap.id for lap in sem_pista} == {sem_a, sem_b}
    assert len(laps.get_by_track(tid)) == 1


def test_historico_mostra_as_voltas_sem_pista(laps, tracks, make_lap, qapp):
    """Sem pista selecionada, a tabela lista o que ficou sem pista.

    Antes esta tela ficava vazia — e a volta gravada sem pista era invisível.
    """
    laps.save(make_lap(track_id=None, samples=30))
    laps.save(make_lap(track_id=None, samples=30, lap_time_ms=91000))

    bus = EventBus()
    vm = HistoryViewModel(laps, bus, tracks)
    try:
        recebidas = []
        vm.laps_changed.connect(lambda linhas: recebidas.append(linhas))
        vm.set_track(None)
        assert recebidas, "a lista precisa ser publicada"
        assert len(recebidas[-1]) == 2
    finally:
        vm.dispose()
        bus.clear()


def test_atribuir_pista_move_a_volta(laps, tracks, make_lap, qapp):
    """A saída manual, para quando o reconhecimento não decide."""
    bus = EventBus()
    vm = HistoryViewModel(laps, bus, tracks)
    try:
        lap_id = laps.save(make_lap(track_id=None, samples=30))
        vm.set_track(None)

        assert vm.assign_track_by_name(lap_id, "Circuito Novo") is True

        assert laps.get_by_id(lap_id).track_id is not None
        assert laps.get_by_track(None) == []
        novo_id = tracks.get_or_create("Circuito Novo")
        assert [lap.id for lap in laps.get_by_track(novo_id)] == [lap_id]
    finally:
        vm.dispose()
        bus.clear()


def test_atribuir_cria_a_pista_quando_ela_nao_existe(laps, tracks, make_lap, qapp):
    """A primeira volta de um circuito novo não tem pista para escolher."""
    bus = EventBus()
    vm = HistoryViewModel(laps, bus, tracks)
    try:
        lap_id = laps.save(make_lap(track_id=None, samples=30))
        assert tracks.find_by_name("Suzuka") == []
        assert vm.assign_track_by_name(lap_id, "Suzuka") is True
        assert [t.name for t in tracks.find_by_name("Suzuka")] == ["Suzuka"]
    finally:
        vm.dispose()
        bus.clear()


def test_atribuir_sem_nome_avisa_em_vez_de_falhar(laps, make_lap, tracks, qapp):
    bus = EventBus()
    vm = HistoryViewModel(laps, bus, tracks)
    try:
        erros = []
        vm.error.connect(erros.append)
        lap_id = laps.save(make_lap(track_id=None, samples=30))

        assert vm.assign_track_by_name(lap_id, "   ") is False
        assert erros
        assert laps.get_by_id(lap_id).track_id is None
    finally:
        vm.dispose()
        bus.clear()


def test_voltas_sem_pista_nao_disputam_recorde_de_pista_alguma(
    laps, tracks, make_lap
):
    """Uma volta sem pista não pode virar recorde de uma pista qualquer."""
    tid = tracks.get_or_create("P")
    laps.save(make_lap(track_id=tid, lap_time_ms=95000, samples=30))
    laps.save(make_lap(track_id=None, lap_time_ms=80000, samples=30))

    melhor = laps.get_best(tid)
    assert melhor is not None
    assert melhor.lap_time_ms == 95000, (
        "a volta sem pista vazou para o recorde de uma pista definida"
    )


def test_aba_historico_oferece_o_botao(laps, tracks, make_lap, qapp):
    """O caminho manual precisa existir na tela, não só no ViewModel."""
    from src.presentation.tabs.history_tab import HistoryTab

    bus = EventBus()
    vm = HistoryViewModel(laps, bus, tracks)
    aba = HistoryTab(vm)
    try:
        laps.save(make_lap(track_id=None, samples=30))
        vm.set_track(None)
        assert aba._assign_button.isEnabled()
        assert "pista" in aba._assign_button.text().lower()
    finally:
        vm.dispose()
        bus.clear()
