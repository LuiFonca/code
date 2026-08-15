"""
Diagnóstico de conexão com o PlayStation.

Testa a captura de telemetria sem abrir interface nenhuma, para separar problema
de rede de problema do programa:

    python3 -m gt7core.tools.diagnose 192.168.1.50

Sem argumento, usa `GT7_PS_IP` do ambiente ou do `.env`.

Duas mudanças em relação à versão que morava em `src/tools/`
------------------------------------------------------------
**Sem IP padrão.** A versão anterior trazia `192.168.15.156` embutido — a rede
doméstica do autor, versionada. É literalmente o P3 da auditoria, que foi
corrigido em toda a aplicação na Fase 1 e sobreviveu esquecido aqui. Agora o
alvo vem do argumento ou da configuração, e não existir é um erro explicado.

**Reaproveita o decodificador do núcleo.** A versão anterior reimplementava o
Salsa20 para não importar nada do projeto — o que fazia sentido quando importar
qualquer módulo arrastava Qt junto e um diagnóstico que não sobe é inútil. Essa
razão morreu na Fase 1: `gt7core.telemetry.protocol` é stdlib mais pycryptodome,
sobe headless e tem testes. Manter duas cópias do decodificador significaria que
uma mudança no protocolo faria esta ferramenta relatar "não é GT7" com
confiança, no exato momento em que alguém depende dela para achar o problema.
"""

from __future__ import annotations

import argparse
import errno
import os
import socket
import sys
import time

from ..config.settings import Settings
from ..telemetry.protocol import salsa20_decode

SEND_PORT = 33739
RECEIVE_PORT = 33740
HEARTBEAT_EVERY_S = 1.0
DEFAULT_WAIT_S = 20


def local_addresses() -> list[str]:
    """IPs desta máquina, para comparar com a sub-rede do console."""
    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            # `sockaddr` é uma tupla genérica na tipagem; em AF_INET o primeiro
            # elemento é sempre o endereço, mas o verificador não sabe disso.
            address = info[4][0]
            if isinstance(address, str):
                found.add(address)
    except socket.gaierror:
        pass

    # O truque do socket UDP "conectado": revela qual interface o sistema usaria
    # para sair. Mais confiável que o hostname quando há várias interfaces —
    # e é justamente a máquina com Wi-Fi + Ethernet + VPN que dá problema.
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        address = probe.getsockname()[0]
        if isinstance(address, str):
            found.add(address)
        probe.close()
    except OSError:
        pass
    return sorted(found)


def same_subnet(a: str, b: str) -> bool:
    """Compara os três primeiros octetos — aproximação de /24, que é o que a
    esmagadora maioria das redes domésticas usa."""
    return a.split(".")[:3] == b.split(".")[:3]


def resolve_target(explicit: str | None) -> str:
    """Argumento > `GT7_PS_IP` > `.env`. Nunca um literal no código."""
    if explicit:
        return explicit
    return Settings.load().telemetry.ps_ip


def _check_network(ip: str) -> None:
    addresses = local_addresses()
    print("1) Endereços desta máquina:", ", ".join(addresses) or "(nenhum encontrado)")
    if not addresses:
        return

    matching = [a for a in addresses if same_subnet(a, ip)]
    if matching:
        print(f"   OK — {matching[0]} está na mesma sub-rede que {ip}.")
        return

    print(f"   ATENÇÃO — nenhum endereço local está na sub-rede de {ip}.")
    print("   O computador e o console parecem estar em redes diferentes")
    print("   (Wi-Fi de visitantes, duas faixas, VPN ativa...). É a causa mais")
    print("   comum de 'sem rota até o host'.")


def _open_socket() -> socket.socket | None:
    print(f"\n2) Abrindo a porta de captura {RECEIVE_PORT}...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", RECEIVE_PORT))
        sock.settimeout(1.0)
    except OSError as exc:
        print(f"   FALHOU: {exc}")
        if exc.errno == errno.EADDRINUSE:
            print("   Outra cópia do programa (ou outra ferramenta de telemetria)")
            print("   já usa esta porta. Feche-a e rode de novo.")
        return None

    print("   OK — porta aberta.")
    return sock


def _listen(sock: socket.socket, ip: str, wait_s: int) -> tuple[int, int, int, dict[str, int]]:
    print(f"\n3) Enviando toque para {ip}:{SEND_PORT} e ouvindo por {wait_s}s...")
    print("   (abra o GT7 numa corrida ou track day — no menu ele não transmite)\n")

    start = time.time()
    last_beat = 0.0
    sent = valid = invalid = 0
    send_errors: dict[str, int] = {}
    first_packet_at: float | None = None

    while time.time() - start < wait_s:
        now = time.time()
        if now - last_beat > HEARTBEAT_EVERY_S:
            try:
                sock.sendto(b"A", (ip, SEND_PORT))
                sent += 1
            except OSError as exc:
                key = f"[{exc.errno}] {exc.strerror}"
                send_errors[key] = send_errors.get(key, 0) + 1
            last_beat = now

        try:
            data, addr = sock.recvfrom(4096)
        except TimeoutError:
            continue
        except OSError:
            continue

        if first_packet_at is None:
            first_packet_at = now - start
            print(
                f"   Primeiro pacote de {addr[0]} após "
                f"{first_packet_at:.1f}s ({len(data)} bytes)"
            )

        if salsa20_decode(data) is not None:
            valid += 1
        else:
            invalid += 1

    return sent, valid, invalid, send_errors


def _verdict(sent: int, valid: int, invalid: int, send_errors: dict[str, int]) -> int:
    print("\n4) Resultado")
    print(f"   toques enviados  : {sent}")
    print(f"   pacotes válidos  : {valid}")
    print(f"   pacotes inválidos: {invalid}")
    if send_errors:
        print("   erros de envio   :")
        for key, count in send_errors.items():
            print(f"      {key} — {count}x")
    print()

    if valid:
        print("   FUNCIONANDO. A telemetria chega e decodifica corretamente.")
        print("   Se mesmo assim o programa não mostra dados, o problema é na")
        print("   interface e não na rede — mande esta saída junto do relato.")
        return 0

    unreachable = {errno.EHOSTUNREACH, errno.ENETUNREACH}
    if send_errors and all(
        any(f"[{code}]" in key for code in unreachable) for key in send_errors
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

    print("   Nenhum toque saiu e nada chegou. Confira se a máquina tem rede.")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m gt7core.tools.diagnose",
        description="Testa a captura de telemetria do GT7 sem abrir a interface.",
    )
    parser.add_argument("ip", nargs="?", help="IP do PlayStation (padrão: GT7_PS_IP)")
    parser.add_argument(
        "--wait",
        type=int,
        default=int(os.environ.get("DIAG_WAIT", DEFAULT_WAIT_S)),
        help=f"segundos de escuta (padrão: {DEFAULT_WAIT_S})",
    )
    args = parser.parse_args(argv)

    ip = resolve_target(args.ip)
    if not ip:
        print("Nenhum IP informado.")
        print("Passe como argumento ou defina GT7_PS_IP no ambiente ou no .env:")
        print("    python3 -m gt7core.tools.diagnose 192.168.1.50")
        return 2

    print(f"Diagnóstico de telemetria GT7 — alvo {ip}\n")
    _check_network(ip)

    sock = _open_socket()
    if sock is None:
        return 1

    try:
        sent, valid, invalid, send_errors = _listen(sock, ip, args.wait)
    finally:
        sock.close()

    return _verdict(sent, valid, invalid, send_errors)


if __name__ == "__main__":
    sys.exit(main())
