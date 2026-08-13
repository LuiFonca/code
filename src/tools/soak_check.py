"""
Validação de sessão longa com o console de verdade.

A Fase 7 pede o que nenhum teste automatizado alcança: horas de telemetria
real, com o jogo pausando, carregando, entrando e saindo de pista, e a rede
oscilando. O que dá para automatizar — memória, acúmulo de estado, vazamento
de thread — já está em `tests/test_sessao_longa.py`, com voltas sintéticas.

O que falta é medir a *realidade*: a taxa de pacotes se mantém? há buracos?
o quaternion de orientação chega válido o tempo todo, ou zera em algumas
situações (replay, câmera de fora, pausa)? Essas perguntas não têm resposta
sem o PS5, e olhar a tela do app não responde nenhuma delas.

Esta ferramenta faz a medição no lugar do olho. Rode em paralelo com o jogo:

    python3 src/tools/soak_check.py 192.168.15.156 --minutos 60

Ela não grava nada, não abre a interface e não disputa nada com o app — usa a
mesma porta em modo compartilhado, então pode rodar com o app aberto.

Ao final imprime um relatório com o que passou e o que não passou. Me mande a
saída: é o dado que fecha a Fase 7.
"""

import argparse
import socket
import struct
import sys
import time

SEND_PORT = 33739
RECEIVE_PORT = 33740
MAGIC_NUMBER = 0x47375330
GT7_KEY = b"Simulator Interface Packet GT7 ver 0.0"
DEFAULT_IP = "192.168.15.156"

# O GT7 transmite a ~60 Hz. Abaixo disto há perda de pacote em algum ponto do
# caminho — Wi-Fi congestionado é a causa mais comum.
TAXA_ESPERADA_HZ = 60.0
TAXA_MINIMA_ACEITAVEL_HZ = 50.0

# Silêncio acima disto conta como buraco. Meio segundo é muito mais que o
# intervalo normal (~16 ms) e curto o bastante para o piloto sentir.
BURACO_S = 0.5


def decodificar(data: bytes):
    try:
        from Crypto.Cipher import Salsa20
    except ImportError:
        return None
    oiv = data[0x40:0x44]
    iv1 = int.from_bytes(oiv, "little")
    nonce = (iv1 ^ 0xDEADBEAF).to_bytes(4, "little") + iv1.to_bytes(4, "little")
    decoded = Salsa20.new(key=GT7_KEY[:32], nonce=nonce).decrypt(data)
    if struct.unpack("<I", decoded[0:4])[0] != MAGIC_NUMBER:
        return None
    return decoded


class Medidas:
    """Acumuladores da sessão. Nada é guardado por pacote — só agregados.

    De propósito: a ferramenta não pode ser ela mesma a fonte de crescimento
    de memória que veio medir.
    """

    def __init__(self):
        self.validos = 0
        self.invalidos = 0
        self.buracos = 0
        self.maior_buraco_s = 0.0
        self.quaternion_ok = 0
        self.quaternion_nulo = 0
        self.na_pista = 0
        self.pausado = 0
        self.voltas_vistas = set()
        self.velocidade_max = 0.0
        self.primeira_amostra: float | None = None
        self.ultima_amostra: float | None = None

    def registrar(self, d: bytes, agora: float) -> None:
        if self.ultima_amostra is not None:
            intervalo = agora - self.ultima_amostra
            if intervalo > BURACO_S:
                self.buracos += 1
                self.maior_buraco_s = max(self.maior_buraco_s, intervalo)
        else:
            self.primeira_amostra = agora
        self.ultima_amostra = agora

        self.validos += 1

        # Os mesmos offsets que o app usa. Se divergirem, é aqui que aparece.
        rot = struct.unpack("<ffff", d[0x1C:0x2C])
        norma = sum(c * c for c in rot) ** 0.5
        if norma > 0.5:
            self.quaternion_ok += 1
        else:
            self.quaternion_nulo += 1

        velocidade = struct.unpack("<f", d[0x4C:0x50])[0] * 3.6
        self.velocidade_max = max(self.velocidade_max, velocidade)

        volta = struct.unpack("<h", d[0x74:0x76])[0]
        if volta > 0:
            self.voltas_vistas.add(volta)

        flags = struct.unpack("<H", d[0x8E:0x90])[0]
        if flags & 1:
            self.na_pista += 1
        if flags & 2:
            self.pausado += 1

    @property
    def duracao_s(self) -> float:
        if self.primeira_amostra is None or self.ultima_amostra is None:
            return 0.0
        return self.ultima_amostra - self.primeira_amostra

    @property
    def taxa_hz(self) -> float:
        return self.validos / self.duracao_s if self.duracao_s > 0 else 0.0


def relatorio(m: Medidas, minutos: float) -> int:
    print("\n" + "=" * 62)
    print("RELATÓRIO DE SESSÃO LONGA")
    print("=" * 62)
    print(f"  duração medida     : {m.duracao_s / 60:.1f} min (pedido: {minutos:.0f})")
    print(f"  pacotes válidos    : {m.validos}")
    print(f"  pacotes inválidos  : {m.invalidos}")
    print(f"  taxa média         : {m.taxa_hz:.1f} Hz")
    print(f"  buracos (> {BURACO_S}s)   : {m.buracos} "
          f"(maior: {m.maior_buraco_s:.1f}s)")
    print(f"  voltas distintas   : {len(m.voltas_vistas)}")
    print(f"  velocidade máxima  : {m.velocidade_max:.0f} km/h")
    print(f"  amostras em pista  : {m.na_pista}")
    print(f"  amostras pausado   : {m.pausado}")
    print(f"  orientação válida  : {m.quaternion_ok}")
    print(f"  orientação nula    : {m.quaternion_nulo}")

    print("\n  VEREDITO")
    falhas = []

    if m.validos == 0:
        print("    NADA CHEGOU. Rode src/tools/diagnose.py antes deste.")
        return 1

    if m.taxa_hz >= TAXA_MINIMA_ACEITAVEL_HZ:
        print(f"    [ok]  taxa de {m.taxa_hz:.0f} Hz — dentro do esperado (~60 Hz)")
    else:
        print(f"    [!]   taxa de {m.taxa_hz:.0f} Hz — abaixo de "
              f"{TAXA_MINIMA_ACEITAVEL_HZ:.0f} Hz")
        print("          Há perda de pacote no caminho. Cabo em vez de Wi-Fi")
        print("          resolve na maioria dos casos.")
        falhas.append("taxa")

    if m.buracos == 0:
        print("    [ok]  nenhuma interrupção no fluxo")
    else:
        print(f"    [!]   {m.buracos} interrupções, a maior de "
              f"{m.maior_buraco_s:.1f}s")
        print("          Buraco durante uma volta a corrompe: as amostras")
        print("          pulam um trecho e a distância acumulada sai errada.")
        falhas.append("buracos")

    total_orient = m.quaternion_ok + m.quaternion_nulo
    pct_ok = m.quaternion_ok / total_orient * 100 if total_orient else 0.0
    if pct_ok >= 95.0:
        print(f"    [ok]  orientação válida em {pct_ok:.1f}% das amostras —")
        print("          o ângulo de deriva é confiável nesta sessão")
    else:
        print(f"    [!]   orientação válida em apenas {pct_ok:.1f}% das amostras")
        print("          O ângulo de deriva vai aparecer com lacunas. Me mande")
        print("          este relatório: pode ser offset errado no parser.")
        falhas.append("orientação")

    if len(m.voltas_vistas) >= 5:
        print(f"    [ok]  {len(m.voltas_vistas)} voltas cobertas — amostra "
              "suficiente")
    else:
        print(f"    [!]   apenas {len(m.voltas_vistas)} volta(s) cobertas. Para")
        print("          valer como validação de sessão longa, rode ao menos 5.")
        falhas.append("voltas")

    print()
    if not falhas:
        print("    SESSÃO LONGA VALIDADA.")
        return 0
    print(f"    PENDÊNCIAS: {', '.join(falhas)}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ip", nargs="?", default=DEFAULT_IP)
    parser.add_argument("--minutos", type=float, default=30.0)
    args = parser.parse_args()

    print(f"Sessão longa — alvo {args.ip}, por {args.minutos:.0f} minutos.")
    print("Pode deixar o app aberto: esta ferramenta divide a porta com ele.")
    print("Ctrl-C encerra e imprime o relatório do que já foi medido.\n")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        # Sem isto, rodar junto com o app dá "endereço em uso" — e o ponto da
        # ferramenta é justamente medir a sessão real, com o app funcionando.
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    try:
        sock.bind(("0.0.0.0", RECEIVE_PORT))
    except OSError as e:
        print(f"Não foi possível abrir a porta {RECEIVE_PORT}: {e}")
        return 1
    sock.settimeout(1.0)

    medidas = Medidas()
    inicio = time.time()
    ultimo_toque = 0.0
    ultimo_aviso = 0.0
    limite = args.minutos * 60

    try:
        while time.time() - inicio < limite:
            agora = time.time()
            if agora - ultimo_toque > 1.0:
                try:
                    sock.sendto(b"A", (args.ip, SEND_PORT))
                except OSError:
                    pass
                ultimo_toque = agora

            # Progresso a cada minuto: uma hora de silêncio no terminal faz
            # qualquer um achar que travou.
            if agora - ultimo_aviso > 60:
                decorrido = (agora - inicio) / 60
                print(f"  [{decorrido:5.1f} min] {medidas.validos} pacotes, "
                      f"{medidas.taxa_hz:.0f} Hz, "
                      f"{len(medidas.voltas_vistas)} voltas")
                ultimo_aviso = agora

            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue

            decodificado = decodificar(data)
            if decodificado is None:
                medidas.invalidos += 1
            else:
                medidas.registrar(decodificado, time.time())
    except KeyboardInterrupt:
        print("\n  interrompido — relatório do que foi medido até aqui:")
    finally:
        sock.close()

    return relatorio(medidas, args.minutos)


if __name__ == "__main__":
    sys.exit(main())
