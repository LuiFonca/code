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
from dataclasses import dataclass

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


def _open_socket(*, quiet: bool = False) -> socket.socket | None:
    """Abre a porta de recepção.

    O `quiet` existe porque esta função ganhou um segundo chamador: o botão
    "Testar conexão" da interface. Sem ele, clicar no botão cuspia `2) Abrindo a
    porta de captura 33740...` no terminal de quem abriu o programa — um passo
    numerado de um roteiro de linha de comando que ninguém estava rodando. O
    `probe()` prometia, na própria docstring, não imprimir nada; a promessa era
    falsa porque a impressão estava aqui dentro, um nível abaixo.
    """
    def say(message: str) -> None:
        if not quiet:
            print(message)

    say(f"\n2) Abrindo a porta de captura {RECEIVE_PORT}...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", RECEIVE_PORT))
        sock.settimeout(1.0)
    except OSError as exc:
        say(f"   FALHOU: {exc}")
        if exc.errno == errno.EADDRINUSE:
            say("   Outra cópia do programa (ou outra ferramenta de telemetria)")
            say("   já usa esta porta. Feche-a e rode de novo.")
        return None

    say("   OK — porta aberta.")
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


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """O veredito, separado de como ele é impresso.

    Existe porque a mesma conclusão precisa aparecer em dois lugares muito
    diferentes: no terminal, para quem rodou o diagnóstico, e na tela de
    configuração, ao lado do campo de IP, para quem nunca vai abrir um terminal.
    Duas cópias da classificação divergiriam na primeira correção — e divergir
    aqui significa a interface mandar mexer no firewall enquanto o terminal diz
    que o jogo está no menu.
    """

    ok: bool
    headline: str
    steps: tuple[str, ...] = ()

    def summary(self) -> str:
        return "\n".join((self.headline, *self.steps))


def diagnose_counts(
    sent: int, valid: int, invalid: int, send_errors: dict[str, int]
) -> Diagnosis:
    """Classifica o resultado da sondagem. Pura: contagens entram, texto sai.

    A ordem dos ramos é a ordem da certeza, do mais conclusivo para o mais
    genérico. Pacote válido encerra o assunto; depois vem a falta de rota, que
    é fato do sistema operacional; só então as hipóteses.
    """
    if valid:
        return Diagnosis(
            ok=True,
            headline="FUNCIONANDO. A telemetria chega e decodifica corretamente.",
            steps=(
                "Se mesmo assim o programa não mostra dados, o problema é na",
                "interface e não na rede — mande esta saída junto do relato.",
            ),
        )

    unreachable = {errno.EHOSTUNREACH, errno.ENETUNREACH}
    if send_errors and all(
        any(f"[{code}]" in key for code in unreachable) for key in send_errors
    ):
        return Diagnosis(
            ok=False,
            headline="SEM ROTA até o console. Nenhum toque saiu da máquina.",
            steps=(
                "Verifique, nesta ordem:",
                "  - o IP no console: Configurações > Rede > Ver status da conexão",
                "  - se console e computador estão na MESMA rede e faixa de IP",
                "  - VPN ligada no computador (desligue e teste de novo)",
                "  - no macOS: Ajustes > Privacidade e Segurança > Rede Local,",
                "    e confirme que o Terminal (ou o Python) está autorizado",
            ),
        )

    if sent and not valid and not invalid:
        return Diagnosis(
            ok=False,
            headline="Os toques saíram, mas nada voltou.",
            steps=(
                "Isso aponta para o console não estar transmitindo:",
                "  - o GT7 precisa estar numa sessão ativa (corrida/track day)",
                "  - confirme que o IP é do console, e não de outro aparelho",
                "  - firewall bloqueando entrada na porta 33740",
            ),
        )

    if invalid and not valid:
        return Diagnosis(
            ok=False,
            headline="Chegaram pacotes, mas nenhum é do GT7 (não decodificam).",
            steps=("Provavelmente outro serviço usa esta porta na sua rede.",),
        )

    return Diagnosis(
        ok=False,
        headline="Nenhum toque saiu e nada chegou. Confira se a máquina tem rede.",
    )


def probe(ip: str, *, wait_s: float = 6.0) -> Diagnosis:
    """Sonda a rede e devolve o veredito, sem imprimir nada.

    É o que a tela de configuração chama. O `wait_s` padrão é menor que o da
    linha de comando de propósito: no terminal a pessoa aceita esperar 20 s por
    um diagnóstico completo, mas um botão que trava a interface por 20 s parece
    travado — e ela clica de novo, ou fecha o programa.
    """
    sock = _open_socket(quiet=True)
    if sock is None:
        return Diagnosis(
            ok=False,
            headline="Não foi possível abrir a porta de recepção.",
            steps=(
                f"Outro programa já usa a porta {RECEIVE_PORT} — outra cópia",
                "deste programa, ou outra ferramenta de telemetria. Feche-a",
                "e teste de novo.",
            ),
        )

    start = time.time()
    last_beat = 0.0
    sent = valid = invalid = 0
    send_errors: dict[str, int] = {}

    try:
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
                data, _addr = sock.recvfrom(4096)
            except (TimeoutError, OSError):
                continue

            if salsa20_decode(data) is not None:
                valid += 1
            else:
                invalid += 1
    finally:
        sock.close()

    return diagnose_counts(sent, valid, invalid, send_errors)


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

    diagnosis = diagnose_counts(sent, valid, invalid, send_errors)
    for line in diagnosis.summary().splitlines():
        print(f"   {line}")
    return 0 if diagnosis.ok else 1


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
