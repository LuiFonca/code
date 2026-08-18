"""
O caminho que faltava: pacote real → motor → o que aparece na tela.

Relato do usuário, com um PS5 de verdade:

    "o programa não atualiza o delta em tempo real a cada segundo, os gráficos
    não estão sendo construídos em nenhuma das abas, aparece apenas o primeiro
    segundo de cada gráfico e nada mais"

Três sintomas, uma causa: dois offsets trocados no protocolo faziam a distância
ficar em 0,0 m para sempre. **O eixo horizontal dos gráficos é distância, não
tempo** — então todo ponto ia para x=0 e o traço virava um risco vertical no
canto esquerdo, que é como "só o primeiro instante" se parece. E o delta, que
alinha a volta corrente contra a referência por distância, lia eternamente a
mesma posição.

Nenhum teste pegou isso porque nenhum ligava as duas pontas: os de protocolo
conferiam campos isolados, os de motor construíam quadros à mão, e os de
interface alimentavam a página com pontos já prontos. Cada camada estava certa
sozinha. Este arquivo percorre bytes → `from_bytes` → motor → página, que é o
percurso onde o defeito morava.

É síncrono de propósito: sem thread, sem `sleep`, sem esperar o relógio. O
defeito não tinha nada a ver com concorrência, e um teste que dependesse de
tempo real seria lento e intermitente por razões que não interessam.
"""

from __future__ import annotations

import pytest

from gt7core.events.bus import EventBus
from gt7core.telemetry.engine import TelemetryEngine, TelemetryReceived
from gt7core.telemetry.protocol import TelemetryFrame

pytest.importorskip("PySide6", reason="a página é Qt")

from tests.conftest import build_plaintext_packet  # noqa: E402

#: Perfil de velocidade de uma freada: entra rápido, desacelera, sai acelerando.
#: Velocidade constante esconderia um defeito de eixo, porque um traço reto
#: parece plausível mesmo comprimido.
PERFIL_KMH = [220.0, 210.0, 180.0, 140.0, 110.0, 95.0, 110.0, 150.0, 190.0, 215.0]


#: 900 quadros a 60 Hz = 15 s. A ~162 km/h médios do perfil dá ~675 m —
#: perto da janela de rastro de 800 m sem estourá-la, para que nada seja
#: descartado por poda e o teste meça o que acumulou de verdade.
QUADROS = 900


def capturar_pontos(quadros: int = QUADROS) -> list[TelemetryReceived]:
    """Roda pacotes reais pelo motor e devolve os eventos publicados."""
    bus = EventBus()
    engine = TelemetryEngine(bus, sample_rate_hz=60)
    recebidos: list[TelemetryReceived] = []
    bus.subscribe(TelemetryReceived, recebidos.append)

    for tick in range(quadros):
        speed_kmh = PERFIL_KMH[tick % len(PERFIL_KMH)]
        packet = build_plaintext_packet(speed_ms=speed_kmh / 3.6, packet_id=tick)
        frame = TelemetryFrame.from_bytes(bytes(packet))
        assert frame is not None, "o pacote de teste não decodificou"
        engine.on_frame(frame)

    return recebidos


class TestDistanciaChegaNaTela:
    """O sintoma, dito na linguagem do que o usuário vê."""

    def test_a_distancia_anda_ao_longo_da_captura(self) -> None:
        eventos = capturar_pontos()
        distancias = [e.point.distance_m for e in eventos]

        assert distancias[0] == 0.0
        assert distancias[-1] > 500.0, (
            "a distância não andou — é o defeito que achatava todos os gráficos"
        )
        assert distancias == sorted(distancias), "distância precisa ser monotônica"

    def test_cada_amostra_cai_num_ponto_diferente_do_eixo(self) -> None:
        """O coração do sintoma.

        Com a distância travada, as 300 amostras compartilhavam **um** valor de
        x. O gráfico recebia 300 pontos e desenhava um risco vertical — dados
        presentes, traço ausente. Contar valores distintos é o que separa "o
        gráfico está vazio" de "o gráfico está empilhado".
        """
        eventos = capturar_pontos()
        distintas = {round(e.point.distance_m, 2) for e in eventos}

        assert len(distintas) > QUADROS * 0.9, (
            f"só {len(distintas)} posições distintas em {len(eventos)} amostras: "
            "os pontos estão empilhados no mesmo x"
        )

    def test_o_tempo_tambem_anda(self) -> None:
        """O tempo vinha do mesmo campo errado, e travava junto."""
        eventos = capturar_pontos()
        tempos = [e.point.elapsed_ms for e in eventos]

        assert tempos[0] == 0
        assert tempos[-1] == pytest.approx((QUADROS - 1) * 1000 / 60, abs=2)
        assert len(set(tempos)) > QUADROS * 0.9


class TestGraficoAoVivo:
    """A página, alimentada pelo motor real."""

    def test_o_traco_cobre_uma_faixa_de_pista(self, tmp_path) -> None:  # noqa: ANN001
        """Da amostra ao pixel: a série precisa ter largura.

        Verifica o contrato que o usuário enxerga — o traço cobre um trecho de
        pista — em vez de detalhes internos do widget. Com o defeito, `min` e
        `max` do eixo coincidiam e a largura era zero.
        """
        from PySide6.QtWidgets import QApplication

        from gt7app.application import build_core, build_gui
        from gt7core.config.settings import Settings

        settings = Settings()
        settings.storage.database_path = tmp_path / "t.db"
        settings.storage.telemetry_path = tmp_path / "tel"
        settings.env_path = tmp_path / ".env"

        app = QApplication.instance() or QApplication([])
        core = build_core(settings)
        try:
            window = build_gui(core)
            live = window._pages[0]  # noqa: SLF001

            # Entrega os eventos direto ao handler da página: é o mesmo que o
            # adaptador Qt faria, sem precisar de thread nem de laço de eventos.
            for evento in capturar_pontos():
                live._on_frame(evento)  # noqa: SLF001
            live._repaint_traces()  # noqa: SLF001
            app.processEvents()

            trilha = live._trail  # noqa: SLF001
            assert len(trilha) > QUADROS * 0.9, "o rastro não acumulou amostras"

            largura = trilha[-1][0] - trilha[0][0]
            assert largura > 500.0, (
                f"o traço cobre apenas {largura:.1f} m — os pontos estão "
                "empilhados no mesmo ponto do eixo"
            )

            serie = live._speed_chart._series  # noqa: SLF001
            assert serie, "o gráfico de velocidade ficou sem série"
            xs = [x for x, _ in serie[0].points]
            assert max(xs) - min(xs) > 500.0, "a série tem largura zero no eixo"
            window.close()
        finally:
            core.close()
