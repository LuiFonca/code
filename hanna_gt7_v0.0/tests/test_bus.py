"""
Testes do barramento de eventos.

O isolamento de handler é o comportamento que mais importa: sem ele, um bug numa
aba da interface mataria a gravação da volta que o piloto acabou de fazer.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from gt7core.events.bus import EventBus


@dataclass(frozen=True)
class Ping:
    value: int = 0


@dataclass(frozen=True)
class Pong:
    value: int = 0


class TestDespacho:
    def test_handler_recebe_o_evento(self, bus: EventBus) -> None:
        received: list[Ping] = []
        bus.subscribe(Ping, received.append)
        bus.publish(Ping(42))

        assert received == [Ping(42)]

    def test_despacho_e_por_tipo_exato(self, bus: EventBus) -> None:
        pings: list[Ping] = []
        pongs: list[Pong] = []
        bus.subscribe(Ping, pings.append)
        bus.subscribe(Pong, pongs.append)

        bus.publish(Ping(1))

        assert len(pings) == 1
        assert pongs == []

    def test_evento_sem_assinante_nao_quebra(self, bus: EventBus) -> None:
        bus.publish(Ping(1))  # não deve levantar

    def test_assinar_duas_vezes_nao_duplica(self, bus: EventBus) -> None:
        """Uma aba reconstruída não pode processar cada evento em duplicidade."""
        received: list[Ping] = []
        bus.subscribe(Ping, received.append)
        bus.subscribe(Ping, received.append)

        bus.publish(Ping(1))

        assert len(received) == 1
        assert bus.handler_count(Ping) == 1

    def test_desinscrever_para_de_entregar(self, bus: EventBus) -> None:
        received: list[Ping] = []
        bus.subscribe(Ping, received.append)
        bus.unsubscribe(Ping, received.append)
        bus.publish(Ping(1))

        assert received == []

    def test_desinscrever_o_que_nao_estava_e_silencioso(self, bus: EventBus) -> None:
        bus.unsubscribe(Ping, lambda _: None)  # não deve levantar


class TestIsolamentoDeFalha:
    def test_handler_que_levanta_nao_derruba_os_outros(self, bus: EventBus) -> None:
        """§41: nenhum módulo externo pode derrubar o núcleo."""
        survivors: list[Ping] = []

        def exploding(_: Ping) -> None:
            raise RuntimeError("assinante quebrado")

        bus.subscribe(Ping, exploding)
        bus.subscribe(Ping, survivors.append)

        bus.publish(Ping(1))  # não propaga a exceção

        assert len(survivors) == 1

    def test_falha_e_registrada_no_log(self, bus: EventBus, caplog) -> None:
        """O erro precisa virar registro consultável — a versão anterior usava
        print() e a falha sumia no terminal."""
        def exploding(_: Ping) -> None:
            raise ValueError("boom")

        bus.subscribe(Ping, exploding)
        with caplog.at_level("ERROR"):
            bus.publish(Ping(1))

        assert any("handler falhou" in record.message for record in caplog.records)


class TestThreadSafety:
    def test_publicacao_concorrente_entrega_tudo(self, bus: EventBus) -> None:
        """A captura roda numa thread própria enquanto a UI assina na dela."""
        received: list[Ping] = []
        lock = threading.Lock()

        def collect(event: Ping) -> None:
            with lock:
                received.append(event)

        bus.subscribe(Ping, collect)

        def publish_many() -> None:
            for i in range(200):
                bus.publish(Ping(i))

        threads = [threading.Thread(target=publish_many) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(received) == 800

    def test_assinar_durante_publicacao_nao_da_deadlock(self, bus: EventBus) -> None:
        """Handlers rodam fora do lock; um que assine outro evento não trava."""
        def subscribing(_: Ping) -> None:
            bus.subscribe(Pong, lambda _: None)

        bus.subscribe(Ping, subscribing)
        bus.publish(Ping(1))  # sem deadlock

        assert bus.handler_count(Pong) == 1

    def test_clear_remove_tudo(self, bus: EventBus) -> None:
        bus.subscribe(Ping, lambda _: None)
        bus.clear()

        assert bus.handler_count(Ping) == 0
