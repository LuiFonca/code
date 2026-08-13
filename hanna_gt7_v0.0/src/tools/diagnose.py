"""
Diagnóstico de conexão com o PlayStation.

Testa a captura de telemetria sem abrir a interface, para separar problema de
rede de problema do app. Roda sozinho — não importa nada do projeto.

    python3 src/tools/diagnose.py 192.168.15.156

Sem argumento, usa o IP padrão do app.
"""

import errno
import socket
import struct
import sys
import time

SEND_PORT = 33739
RECEIVE_PORT = 33740
MAGIC_NUMBER = 0x47375330
GT7_KEY = b"Simulator Interface Packet GT7 ver 0.0"
DEFAULT_IP = "192.168.15.156"

WAIT_SECONDS = int(__import__("os").environ.get("DIAG_WAIT", 20))
HEARTBEAT_EVERY = 1.0


def local_addresses() -> list[str]:
    """IPs locais desta máquina, para comparar com a sub-rede do console."""
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except socket.gaierror:
        pass
    # O truque do socket UDP "conectado" revela qual interface o sistema usaria
    # para sair — mais confiável que o hostname quando há várias interfaces.
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        found.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    return sorted(found)


def same_subnet(a: str, b: str) -> bool:
    """Compara os três primeiros octetos — aproximação de máscara /24, que é o
    padrão da esmagadora maioria das redes domésticas."""
    return a.split(".")[:3] == b.split(".")[:3]


def decode(data: bytes) -> bytes | None:
    try:
        from Crypto.Cipher import Salsa20
    except ImportError:
        print("  ! pycryptodome não instalado — não dá para validar o conteúdo.")
        return None
    oiv = data[0x40:0x44]
    iv1 = int.from_bytes(oiv, "little")
    iv2 = iv1 ^ 0xDEADBEAF
    nonce = iv2.to_bytes(4, "little") + iv1.to_bytes(4, "little")
    decoded = Salsa20.new(key=GT7_KEY[:32], nonce=nonce).decrypt(data)
    if struct.unpack("<I", decoded[0:4])[0] != MAGIC_NUMBER:
        return None
    return decoded


def main() -> int:
    ip = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_IP
    print(f"Diagnóstico de telemetria GT7 — alvo {ip}\n")

    # 1. Rede local
    locals_ = local_addresses()
    print("1) Endereços desta máquina:", ", ".join(locals_) or "(nenhum encontrado)")
    if locals_:
        matching = [a for a in locals_ if same_subnet(a, ip)]
        if matching:
            print(f"   OK — {matching[0]} está na mesma sub-rede que {ip}.")
        else:
            print(f"   ATENÇÃO — nenhum endereço local está na sub-rede de {ip}.")
            print("   O computador e o console parecem estar em redes diferentes")
            print("   (Wi-Fi de visitantes, duas faixas, VPN ativa...). É a causa")
            print("   mais comum de 'sem rota até o host'.")

    # 2. Porta de escuta
    print(f"\n2) Abrindo a porta de captura {RECEIVE_PORT}...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", RECEIVE_PORT))
        sock.settimeout(1.0)
        print("   OK — porta aberta.")
    except OSError as e:
        print(f"   FALHOU: {e}")
        if e.errno == errno.EADDRINUSE:
            print("   Outra cópia do app (ou outra ferramenta de telemetria) já")
            print("   está usando esta porta. Feche-a e rode de novo.")
        return 1

    # 3. Heartbeat + escuta
    print(f"\n3) Enviando toque para {ip}:{SEND_PORT} e ouvindo por {WAIT_SECONDS}s...")
    print("   (abra o GT7 numa corrida ou track day — no menu ele não transmite)\n")

    start = time.time()
    last_beat = 0.0
    sent = valid = invalid = 0
    send_errors: dict[str, int] = {}
    first_packet_at: float | None = None

    while time.time() - start < WAIT_SECONDS:
        now = time.time()
        if now - last_beat > HEARTBEAT_EVERY:
            try:
                sock.sendto(b"A", (ip, SEND_PORT))
                sent += 1
            except OSError as e:
                key = f"[{e.errno}] {e.strerror}"
                send_errors[key] = send_errors.get(key, 0) + 1
            last_beat = now

        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            continue

        if first_packet_at is None:
            first_packet_at = now - start
            print(f"   Primeiro pacote de {addr[0]} após {first_packet_at:.1f}s "
                  f"({len(data)} bytes)")
        if decode(data) is not None:
            valid += 1
        else:
            invalid += 1

    sock.close()

    # 4. Veredito
    print("\n4) Resultado")
    print(f"   toques enviados : {sent}")
    print(f"   pacotes válidos : {valid}")
    print(f"   pacotes inválidos: {invalid}")
    if send_errors:
        print("   erros de envio  :")
        for key, count in send_errors.items():
            print(f"      {key} — {count}x")

    print()
    if valid:
        print("   FUNCIONANDO. A telemetria chega e decodifica corretamente.")
        print("   Se mesmo assim o app não mostra dados, o problema é na interface,")
        print("   não na rede — me mande esta saída.")
        return 0

    if send_errors and all(
        f"[{errno.EHOSTUNREACH}]" in k or f"[{errno.ENETUNREACH}]" in k
        for k in send_errors
    ):
        print("   SEM ROTA até o console. Nenhum toque saiu da máquina.")
        print("   Verifique, nesta ordem:")
        print("     - o IP no console: Configurações > Rede > Ver status da conexão")
        print("     - se console e computador estão na MESMA rede e faixa de IP")
        print("     - VPN ligada no computador (desligue e teste de novo)")
        print("     - no macOS: Ajustes > Privacidade e Segurança > Rede Local,")
        print("       e confirme que o Terminal (ou o Python) está autorizado")
        return 1

    if sent and not valid and not invalid:
        print("   Os toques saíram, mas nada voltou.")
        print("   Isso aponta para o console não estar transmitindo:")
        print("     - o GT7 precisa estar numa sessão ativa (corrida/track day)")
        print("     - confirme que o IP é do console, e não de outro aparelho")
        print("     - firewall bloqueando entrada na porta 33740")
        return 1

    if invalid and not valid:
        print("   Chegaram pacotes, mas nenhum é do GT7 (não decodificam).")
        print("   Provavelmente outro serviço usa esta porta na sua rede.")
        return 1

    
    return 1


if __name__ == "__main__":
    sys.exit(main())
