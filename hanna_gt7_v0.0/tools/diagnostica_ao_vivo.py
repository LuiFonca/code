#!/usr/bin/env python3
"""
Onde os quadros morrem entre o socket e a tela.

O sintoma que gerou esta ferramenta: o diagnóstico de rede diz "FUNCIONANDO, a
telemetria chega e decodifica corretamente", e a aba Ao vivo continua vazia.
Os dois podem ser verdade ao mesmo tempo, porque entre o socket e o cartão de
velocidade há seis etapas, e cada uma sabe descartar em silêncio.

Esta ferramenta monta o **núcleo de verdade** — a mesma configuração, a mesma
fonte, o mesmo motor, o mesmo barramento — e conta quantos quadros sobrevivem a
cada etapa. Onde o número cai para zero está o defeito.

    socket        pacote UDP chegou na porta 33740
      ↓
    decodifica    Salsa20 abriu e o magic bateu
      ↓
    motor         `TelemetryEngine.on_frame` aceitou (não descartou)
      ↓
    barramento    virou um `TelemetryReceived` publicado
      ↓
    interface     um assinante recebeu

Uma etapa que zera com a anterior cheia é uma resposta, não um palpite.

Como usar
---------
Com o GT7 numa sessão e o carro **andando**, e com o HANNA GT7 **fechado**:

    python3 tools/diagnostica_ao_vivo.py

Fechado importa: dois processos na mesma porta UDP disputam os pacotes, e o
sistema entrega para um só. Rodar isto com o programa aberto mede a disputa,
não o programa.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gt7core.config.settings import Settings  # noqa: E402
from gt7core.events.bus import EventBus  # noqa: E402
from gt7core.observability.metrics import TelemetryMetrics  # noqa: E402
from gt7core.telemetry.engine import TelemetryEngine, TelemetryReceived  # noqa: E402
from gt7core.telemetry.protocol import TelemetryFrame  # noqa: E402
from gt7core.telemetry.sources.factory import create_telemetry_source  # noqa: E402

#: Quanto tempo observar, em segundos.
JANELA_S = 12.0


class Contagem:
    """Contadores de cada etapa, mais o motivo do descarte no motor."""

    def __init__(self) -> None:
        self.quadros_da_fonte = 0
        self.no_barramento = 0
        self.pausado = 0
        self.carregando = 0
        self.fora_da_pista = 0
        self.parado = 0
        self.ultimo: TelemetryFrame | None = None


def main() -> int:
    settings = Settings.load()
    telemetria = settings.telemetry

    print("=" * 70)
    print("DIAGNÓSTICO DO CAMINHO ATÉ A TELA")
    print("=" * 70)
    print(f"  configuração lida de : {settings.env_path}")
    print(f"  fonte                : {telemetria.source}")
    print(f"  IP do PS5            : {telemetria.ps_ip or '(vazio)'}")
    print(f"  porta de recepção    : {telemetria.receive_port}")

    if telemetria.source != "udp":
        print()
        print("  ►► ACHADO: a fonte não é o PS5, é o gerador sintético.")
        print("     Ao vivo mostraria dados inventados, com o selo amarelo.")
        print("     Configurações → Fonte: 'PS5 na rede', e salve.")
        return 1

    if not telemetria.ps_ip.strip():
        print()
        print("  ►► ACHADO: não há IP configurado. O programa abre o socket e")
        print("     escuta, mas nunca toca o console — e o GT7 só transmite")
        print("     para quem o tocou. Configurações → IP do PlayStation.")
        return 1

    contagem = Contagem()
    bus = EventBus()
    motor = TelemetryEngine(
        bus, sample_rate_hz=settings.telemetry.sample_rate_hz
    )

    def no_bus(evento: TelemetryReceived) -> None:
        contagem.no_barramento += 1
        del evento

    bus.subscribe(TelemetryReceived, no_bus)

    def ao_receber(frame: TelemetryFrame) -> None:
        """Conta o quadro e classifica **antes** de entregá-lo ao motor.

        A classificação repete as regras de descarte do motor de propósito: é o
        que transforma "sumiu" em "foi descartado porque o jogo estava pausado".
        """
        contagem.quadros_da_fonte += 1
        contagem.ultimo = frame
        if frame.is_paused:
            contagem.pausado += 1
        if frame.is_loading:
            contagem.carregando += 1
        if not frame.is_on_track:
            contagem.fora_da_pista += 1
        if frame.speed_kmh < 1.0:
            contagem.parado += 1
        motor.on_frame(frame)

    metricas = TelemetryMetrics()
    fonte = create_telemetry_source(settings, metrics=metricas)
    fonte.on_frame(ao_receber)
    print(f"\nObservando por {JANELA_S:.0f} s — dirija.\n")

    fonte.start()
    try:
        time.sleep(JANELA_S)
    finally:
        fonte.stop()

    recebidos = metricas.snapshot()

    print("=" * 70)
    print("ETAPA POR ETAPA")
    print("=" * 70)
    linhas = [
        ("socket      ", recebidos.packets_received, "pacotes UDP na porta"),
        ("decodifica  ", recebidos.frames_emitted, "abriram e o magic bateu"),
        ("motor       ", contagem.quadros_da_fonte, "chegaram ao motor"),
        ("barramento  ", contagem.no_barramento, "viraram evento para a tela"),
    ]
    anterior = None
    for nome, valor, texto in linhas:
        marca = ""
        if anterior is not None and anterior > 0 and valor == 0:
            marca = "  ◄◄ MORREU AQUI"
        print(f"  {nome} {valor:6d}  {texto}{marca}")
        anterior = valor

    print()
    if recebidos.packets_received == 0:
        print("  ►► ACHADO: nenhum pacote chegou na porta.")
        print("     O HANNA GT7 está aberto? Dois processos na mesma porta UDP")
        print("     disputam os pacotes e o sistema entrega para um só — feche")
        print("     o programa e rode isto de novo.")
        print("     Se não estiver aberto: confira o IP e se o GT7 está numa")
        print("     sessão (o menu não transmite).")
        return 1

    if recebidos.packets_invalid:
        print(f"  {recebidos.packets_invalid} pacotes não decodificaram —")
        print("  outra origem na mesma porta, ou versão de jogo diferente.")

    if contagem.quadros_da_fonte and not contagem.no_barramento:
        print("  ►► ACHADO: o motor recebeu tudo e não publicou nada.")
        print(f"     pausado: {contagem.pausado}   carregando: {contagem.carregando}")
        print("     O motor descarta quadro com o jogo pausado ou carregando —")
        print("     é o comportamento correto, e explica a tela vazia se o jogo")
        print("     estiver pausado ou numa tela de carregamento.")
        return 1

    print("=" * 70)
    if contagem.no_barramento:
        por_segundo = contagem.no_barramento / JANELA_S
        print(f"  O caminho está inteiro: {por_segundo:.0f} quadros/s até a tela.")
        print("  Se a aba Ao vivo continua vazia com isto passando, o defeito")
        print("  está na interface — mande esta saída.")
        if contagem.fora_da_pista == contagem.quadros_da_fonte:
            print()
            print("  Nota: o bit 'carro na pista' esteve desligado o tempo todo.")
        if contagem.parado == contagem.quadros_da_fonte:
            print()
            print("  Nota: velocidade zero em todos os quadros — carro parado.")
    ultimo = contagem.ultimo
    if ultimo is not None:
        print()
        print("  Orientação (os 28 bytes que passaram a ser lidos):")
        norma = (
            ultimo.rotation_i ** 2 + ultimo.rotation_j ** 2
            + ultimo.rotation_k ** 2 + ultimo.rotation_w ** 2
        ) ** 0.5
        print(f"    norma do quaternion : {norma:.5f}"
              f"   {'◄ CONFIRMADO' if ultimo.orientation_is_valid else '◄ NÃO CONFERE'}")
        guinada = ultimo.yaw_rate_deg_s
        print(f"    guinada medida      : "
              f"{'(descartada)' if guinada is None else f'{guinada:7.2f} °/s'}")
        print(f"    embreagem           : {ultimo.clutch_pedal:.2f}"
              f"   engate {ultimo.clutch_engagement:.2f}")
        print()
        print("  Último quadro recebido:")
        print(f"    velocidade {ultimo.speed_kmh:6.1f} km/h   rpm {ultimo.rpm:7.0f}"
              f"   marcha {ultimo.gear}")
        print(f"    volta {ultimo.lap_count}   flags 0x{ultimo.flags:04X}"
              f"   na pista: {ultimo.is_on_track}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
