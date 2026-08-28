#!/usr/bin/env python3
"""
Descobre o que há nos 28 bytes de 0x1C a 0x38 do pacote do GT7.

Por que esta ferramenta existe
------------------------------
O leitor lê 0x04 (posição), 0x10 (velocidade) e depois **pula para 0x38**. São
sete floats no meio que ninguém nunca olhou. A hipótese — vinda da engenharia
reversa da comunidade, e que fecha exata em 28 bytes — é:

    0x1C, 0x20, 0x24   rotação do carro (i, j, k de um quaternion)
    0x28               orientação relativa ao norte (o w do quaternion)
    0x2C, 0x30, 0x34   velocidade angular (x, y, z), em rad/s

Se estiver certa, dois canais que hoje são estimados passam a ser **medidos**:
a guinada (velocidade angular em Y) e, com a orientação, o **ângulo de deriva**
— quanto o carro aponta para um lado e anda para outro, que é a informação que
falta para dizer o quanto a mão realmente girou.

Hipótese não é fato. Esta ferramenta testa, e testa de um jeito que pode
reprovar — foi a falta disso que deixou o tick em 0x70 ser lido como melhor
volta por meses, e o escorregamento ser lido de um bloco zerado.

Os três testes
--------------
1. **Norma do quaternion.** Quatro floats consecutivos que sempre somam 1,0 em
   quadrado não são coincidência: é a assinatura de uma rotação normalizada.
   Nenhum outro tipo de dado faz isso por acaso, em milhares de amostras.

2. **Correlação com a guinada da trajetória.** Já sabemos derivar guinada da
   posição; se um dos floats for a mesma grandeza medida na fonte, os dois têm
   de andar juntos. Esta é a prova forte: a referência é **independente** do
   byte sob teste.

3. **Faixa plausível.** Velocidade angular de carro vive em ±2 rad/s. Um float
   que passeia por 1e30 é outra coisa, ou é lixo.

Como usar
---------
Com o GT7 numa sessão, **rodando** (não no menu, não parado no box — o teste 2
precisa de curva):

    python3 tools/descobre_orientacao.py 192.168.15.156

Deixe rodar uns 30 segundos fazendo curvas para os dois lados e mande a saída.
"""

from __future__ import annotations

import math
import socket
import struct
import sys
import time
from dataclasses import dataclass

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from gt7core.telemetry.protocol import salsa20_decode  # noqa: E402
from gt7core.telemetry.sources.udp import (  # noqa: E402
    DEFAULT_RECEIVE_PORT,
    DEFAULT_SEND_PORT,
)

#: Início e fim do bloco desconhecido.
INICIO, FIM = 0x1C, 0x38
QUANTOS_FLOATS = (FIM - INICIO) // 4

#: Rótulos da hipótese, na ordem dos floats. Só rótulo: o teste é quem decide.
HIPOTESE = (
    "rot i", "rot j", "rot k", "norte (w)",
    "ang.vel x", "ang.vel y", "ang.vel z",
)

#: Amostras a coletar. A 60 Hz, ~30 s.
AMOSTRAS = 1800

#: Abaixo disto o carro está parado e nada aqui significa nada.
MIN_KMH = 20.0

#: Tolerância da norma do quaternion. Float de 32 bits mais arredondamento do
#: jogo não dá 1,0 exato; 0,01 é folgado o bastante para não reprovar um
#: quaternion de verdade e apertado o bastante para não aprovar acaso.
TOLERANCIA_NORMA = 0.01


@dataclass(slots=True)
class Amostra:
    tick: int
    x: float
    z: float
    speed_kmh: float
    desconhecidos: tuple[float, ...]


def coleta(ps_ip: str, quantas: int) -> list[Amostra]:
    """Escuta a porta e junta amostras, tocando o console para ele transmitir."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", DEFAULT_RECEIVE_PORT))
    sock.settimeout(3.0)

    amostras: list[Amostra] = []
    ultimo_toque = 0.0
    silencios = 0

    print(f"Escutando {ps_ip} — dirija fazendo curvas para os dois lados.")
    try:
        while len(amostras) < quantas:
            agora = time.time()
            if agora - ultimo_toque > 1.0:
                try:
                    sock.sendto(b"A", (ps_ip, DEFAULT_SEND_PORT))
                except OSError as erro:
                    print(f"  não consegui tocar o console: {erro}")
                ultimo_toque = agora

            try:
                dados, _ = sock.recvfrom(4096)
            except TimeoutError:
                silencios += 1
                print(f"  sem pacote há {silencios * 3}s…")
                if silencios >= 4:
                    print("  desistindo — o console não está transmitindo.")
                    break
                continue

            claro = salsa20_decode(dados)
            if claro is None:
                continue
            silencios = 0

            amostras.append(
                Amostra(
                    tick=struct.unpack("<i", claro[0x70:0x74])[0],
                    x=struct.unpack("<f", claro[0x04:0x08])[0],
                    z=struct.unpack("<f", claro[0x0C:0x10])[0],
                    speed_kmh=struct.unpack("<f", claro[0x4C:0x50])[0] * 3.6,
                    desconhecidos=struct.unpack(
                        f"<{QUANTOS_FLOATS}f", claro[INICIO:FIM]
                    ),
                )
            )
            if len(amostras) % 300 == 0:
                print(f"  {len(amostras)} amostras…")
    finally:
        sock.close()

    return amostras


def guinada_da_trajetoria(amostras: list[Amostra]) -> list[tuple[int, float]]:
    """Guinada em rad/s derivada da posição — a referência independente.

    Mesma conta de `gt7core.analytics.steering`: a direção em que o carro anda é
    `atan2(dz, dx)`, e a derivada dela no tempo é a taxa de guinada. Vem da
    posição, que é um campo **já validado**, e não do bloco sob teste — é isso
    que dá ao teste 2 o direito de reprovar.
    """
    janela = 3

    def direcao(i: int) -> float:
        """Direção do carro na amostra `i`, medida sobre a janela inteira.

        Sobre a janela, e não entre vizinhos: a 60 Hz e 200 km/h dois pontos
        consecutivos distam menos de um metro, e o arredondamento do próprio
        pacote vira uma serra que enterra a curva de verdade.
        """
        return math.atan2(
            amostras[i + janela].z - amostras[i - janela].z,
            amostras[i + janela].x - amostras[i - janela].x,
        )

    saida: list[tuple[int, float]] = []
    # Começa em 2×janela: `direcao(i - janela)` alcança `i - 2×janela`, e um
    # índice negativo em Python **não estoura** — ele dá a volta na lista e
    # devolve a última amostra. A primeira versão desta função fazia isso, e o
    # resultado foi uma guinada média 5,7% alta, com aparência de estar certa.
    for i in range(2 * janela, len(amostras) - 2 * janela):
        if amostras[i].speed_kmh < MIN_KMH:
            continue
        dt = (amostras[i + janela].tick - amostras[i - janela].tick) / 60.0
        if dt <= 0:
            continue
        delta = direcao(i + janela) - direcao(i - janela)
        saida.append((i, math.remainder(delta, math.tau) / dt))
    return saida


def correlacao(a: list[float], b: list[float]) -> float:
    """Pearson. Zero quando um dos lados é constante — sem correlação definida."""
    n = len(a)
    if n < 3:
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((v - ma) ** 2 for v in a)
    vb = sum((v - mb) ** 2 for v in b)
    if va <= 0 or vb <= 0:
        return 0.0
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True))
    return cov / math.sqrt(va * vb)


def testa_quaternion(amostras: list[Amostra]) -> None:
    """Procura quatro floats consecutivos cuja norma seja sempre 1."""
    print("\n1. NORMA DO QUATERNION")
    print("   Quatro floats que sempre somam 1,0 em quadrado são uma rotação")
    print("   normalizada. Acaso não faz isso em milhares de amostras.\n")

    achou = False
    for inicio in range(QUANTOS_FLOATS - 3):
        normas = [
            math.sqrt(sum(a.desconhecidos[inicio + k] ** 2 for k in range(4)))
            for a in amostras
        ]
        media = sum(normas) / len(normas)
        desvio = max(abs(n - 1.0) for n in normas)
        offset = INICIO + inicio * 4
        veredito = "◄ É UM QUATERNION" if desvio < TOLERANCIA_NORMA else ""
        if veredito:
            achou = True
        print(
            f"   0x{offset:03X}–0x{offset + 16:03X}  norma média {media:8.5f}  "
            f"pior desvio {desvio:8.5f}  {veredito}"
        )

    if not achou:
        print("\n   Nenhum grupo passou. A hipótese da rotação está ERRADA,")
        print("   ou o quaternion não está normalizado.")


def testa_guinada(amostras: list[Amostra]) -> None:
    """Compara cada float com a guinada derivada da trajetória."""
    print("\n2. CORRELAÇÃO COM A GUINADA DA TRAJETÓRIA")
    print("   A referência vem da posição — campo já validado, independente")
    print("   destes bytes. Correlação perto de ±1,00 identifica a guinada;")
    print("   o sinal negativo só diz que a convenção de eixo é oposta.\n")

    referencia = guinada_da_trajetoria(amostras)
    if len(referencia) < 60:
        print("   Amostras de curva insuficientes — dirija fazendo curvas.")
        return

    indices = [i for i, _ in referencia]
    esperado = [v for _, v in referencia]
    print(f"   ({len(referencia)} amostras em movimento, pico "
          f"{max(abs(v) for v in esperado):.2f} rad/s)\n")

    for f in range(QUANTOS_FLOATS):
        candidato = [amostras[i].desconhecidos[f] for i in indices]
        r = correlacao(esperado, candidato)
        offset = INICIO + f * 4
        marca = "◄ É A GUINADA" if abs(r) > 0.90 else ""
        print(
            f"   0x{offset:03X}  {HIPOTESE[f]:>10}   r = {r:+.3f}   {marca}"
        )


def testa_faixa(amostras: list[Amostra]) -> None:
    """Faixa de cada float — o teste que pega lixo e unidade errada."""
    print("\n3. FAIXA DE CADA FLOAT")
    print("   Velocidade angular de carro vive em ±2 rad/s; rotação, em ±1.")
    print("   Valor gigante ou sempre igual é outra coisa, ou é lixo.\n")

    for f in range(QUANTOS_FLOATS):
        valores = [a.desconhecidos[f] for a in amostras]
        menor, maior = min(valores), max(valores)
        media = sum(valores) / len(valores)
        offset = INICIO + f * 4
        nota = ""
        if menor == maior:
            nota = "◄ CONSTANTE — não é canal"
        elif max(abs(menor), abs(maior)) > 1e6:
            nota = "◄ absurdo — não é o que se supõe"
        print(
            f"   0x{offset:03X}  {HIPOTESE[f]:>10}   "
            f"[{menor:9.4f} , {maior:9.4f}]  média {media:8.4f}  {nota}"
        )


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        print("Falta o IP:  python3 tools/descobre_orientacao.py 192.168.15.156")
        return 2

    ps_ip = sys.argv[1]
    quantas = int(sys.argv[2]) if len(sys.argv) > 2 else AMOSTRAS

    amostras = coleta(ps_ip, quantas)
    if len(amostras) < 120:
        print(f"\nSó {len(amostras)} amostras — pouco para concluir.")
        print("O GT7 precisa estar numa sessão, com o carro andando.")
        return 1

    andando = sum(1 for a in amostras if a.speed_kmh >= MIN_KMH)
    print(f"\n{'=' * 68}")
    print(f"{len(amostras)} amostras, {andando} com o carro em movimento")
    print(f"{'=' * 68}")

    testa_quaternion(amostras)
    testa_guinada(amostras)
    testa_faixa(amostras)

    print(f"\n{'=' * 68}")
    print("Mande esta saída inteira. Com os três testes concordando, estes")
    print("bytes deixam de ser hipótese e viram canal medido no programa.")
    print(f"{'=' * 68}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
