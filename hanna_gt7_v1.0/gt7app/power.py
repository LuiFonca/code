"""
Manter o computador acordado enquanto o programa está em primeiro plano.

Pilotando, ninguém toca no teclado do computador — o volante e os pedais estão
no console. Para o sistema operacional isso é ociosidade, e o protetor de tela
entra no meio da sessão, ou a máquina suspende e a captura UDP morre com ela.
A telemetria de uma volta são ~6.000 amostras que só existem porque alguém
pilotou; perdê-las porque o Mac achou que ninguém estava ali é o pior modo de
falha que este programa tem.

O gatilho é **primeiro plano**, e não "capturando". Quem deixa o programa aberto
olhando o histórico não quer a tela apagando na cara; quem alterna para outro
aplicativo devolveu a máquina ao comportamento normal, e segurá-la acordada a
partir do segundo plano seria um programa mal-educado.

Cada sistema tem o seu mecanismo, e todos falham em silêncio
------------------------------------------------------------
Não há API portátil para isto. Cada plataforma usa a sua, e **nenhuma falha é
fatal**: sem o inibidor o programa continua inteiro, só volta a deixar a
máquina dormir. Derrubar a aplicação porque `caffeinate` não existe seria trocar
um inconveniente por uma perda de dados.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from gt7core.observability.logging import get_logger

_log = get_logger(__name__)


class KeepAwake:
    """Inibidor de suspensão, ligado e desligado conforme o foco.

    Idempotente nos dois sentidos: chamar `acquire()` duas vezes não abre dois
    processos, e `release()` sem `acquire()` não faz nada. Isso importa porque o
    Qt emite mudança de estado de aplicação com mais frequência do que se
    imagina — alternar para outra janela e voltar dispara vários eventos.
    """

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._windows_held = False

    @property
    def is_active(self) -> bool:
        if self._process is not None:
            return self._process.poll() is None
        return self._windows_held

    def acquire(self) -> None:
        if self.is_active:
            return
        try:
            if sys.platform == "darwin":
                self._process = self._spawn_caffeinate()
            elif sys.platform == "win32":
                self._windows_held = self._hold_windows()
            else:
                self._process = self._spawn_systemd_inhibit()
        except OSError as exc:
            # Sem inibidor o programa continua inteiro — só volta a deixar a
            # máquina dormir. Não é motivo para derrubar nada.
            _log.info("não foi possível impedir a suspensão", extra={"erro": str(exc)})
            self._process = None

    def release(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensivo
                process.kill()

        if self._windows_held:
            self._release_windows()
            self._windows_held = False

    # ---------- por plataforma ----------

    def _spawn_caffeinate(self) -> subprocess.Popen[bytes] | None:
        """macOS: `caffeinate -d -i`, o utilitário do próprio sistema.

        `-d` segura o display (protetor de tela) e `-i` segura a suspensão por
        ociosidade. `-w` com o nosso PID é o cinto de segurança: se o programa
        morrer sem passar pelo `release()`, o `caffeinate` morre junto em vez de
        ficar segurando a máquina acordada para sempre.
        """
        caminho = shutil.which("caffeinate") or "/usr/bin/caffeinate"
        if not os.path.exists(caminho):
            return None
        return subprocess.Popen(
            [caminho, "-d", "-i", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _spawn_systemd_inhibit(self) -> subprocess.Popen[bytes] | None:
        """Linux: `systemd-inhibit`, quando existe."""
        caminho = shutil.which("systemd-inhibit")
        if caminho is None:
            return None
        return subprocess.Popen(
            [
                caminho,
                "--what=idle:sleep",
                "--who=Hanna GT7",
                "--why=Capturando telemetria",
                "--mode=block",
                "sleep",
                "infinity",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _hold_windows(self) -> bool:
        """Windows: `SetThreadExecutionState`, sem processo auxiliar."""
        try:
            import ctypes

            # ES_CONTINUOUS mantém o pedido até ser revogado; sem ele o efeito
            # dura um único ciclo e a tela apaga logo em seguida.
            resultado = ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
                0x80000000 | 0x00000002 | 0x00000001
            )
        except (AttributeError, OSError) as exc:  # pragma: no cover - só no Windows
            _log.info("SetThreadExecutionState falhou", extra={"erro": str(exc)})
            return False
        return bool(resultado)

    def _release_windows(self) -> None:  # pragma: no cover - só no Windows
        try:
            import ctypes

            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass
