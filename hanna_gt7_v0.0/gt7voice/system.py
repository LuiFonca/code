"""
O sintetizador do sistema operacional — sem dependência nova.

Os três sistemas que este projeto precisa atender já trazem um TTS:

| Sistema  | Programa                    | Vem de fábrica |
|----------|-----------------------------|----------------|
| macOS    | `say`                       | sim            |
| Windows  | SAPI, via PowerShell        | sim            |
| Linux    | `espeak-ng` / `spd-say`     | quase sempre   |

Instalar um motor de TTS em Python para falar uma frase por volta seria pagar
uma dependência — e memória, numa máquina de 8 GB que já tem um modelo de 4B
dentro — por conveniência nenhuma. O `say` do macOS ainda tem vozes de português
melhores que a maioria das alternativas offline.

O que **não** é feito aqui
--------------------------
Nada de fila, nada de política, nada de decidir se vale falar. Esta classe
recebe uma string e a entrega ao sistema. Quem decide o que dizer e o que
descartar é `radio.VoiceRadio`, que é Python puro e testável.

O processo é lançado e **não** esperado: `say()` retorna imediatamente, porque
quem chama pode ser a thread da interface e uma frase leva segundos.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import sys
import threading

from gt7core.config.settings import VoiceConfig
from gt7core.observability.logging import get_logger

_log = get_logger(__name__)


class SpeechUnavailable(RuntimeError):
    """Nenhum sintetizador encontrado. **Nunca deve derrubar nada.**"""


def detect_engine(platform: str | None = None) -> str:
    """Qual motor usar nesta máquina. String vazia se não houver nenhum.

    `platform` é injetável para o teste conseguir verificar os três sistemas
    a partir de um só — verificar apenas o do container seria testar um terço
    do código e chamar de cobertura.
    """
    name = platform or sys.platform

    if name == "darwin":
        return "say" if shutil.which("say") else ""
    # `cygwin` não começa com "win" e é, ainda assim, Windows com PowerShell
    # disponível. A alternativa seria cair no ramo do Linux e procurar um
    # `espeak` que não existe ali.
    if name.startswith("win") or name == "cygwin":
        return "sapi"
    for candidate in ("espeak-ng", "espeak", "spd-say"):
        if shutil.which(candidate):
            return candidate
    return ""


def build_command(engine: str, text: str, config: VoiceConfig) -> list[str]:
    """A linha de comando para falar `text`. Pura — não executa nada.

    Separada da execução para ser verificável: montar o comando errado é o
    defeito provável aqui, e é o único que dá para pegar sem placa de som.
    """
    if engine == "say":
        command = ["say", "-r", str(config.rate_wpm)]
        if config.voice:
            command += ["-v", config.voice]
        return [*command, text]

    if engine == "sapi":
        # PowerShell é o caminho sem dependência no Windows. As aspas simples
        # do PowerShell escapam duplicando-as — sem isso, um apóstrofo em
        # "não está" quebraria o comando.
        escaped = text.replace("'", "''")
        rate = _sapi_rate(config.rate_wpm)
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Rate = {rate}; "
        )
        if config.voice:
            script += f"$s.SelectVoice('{config.voice}'); "
        script += f"$s.Speak('{escaped}')"
        return ["powershell", "-NoProfile", "-Command", script]

    if engine in ("espeak-ng", "espeak"):
        command = [engine, "-s", str(config.rate_wpm)]
        # `pt-br` e não `pt`: a diferença entre as duas é audível o bastante
        # para o piloto notar, e o padrão do espeak é o europeu.
        command += ["-v", config.voice or "pt-br"]
        return [*command, text]

    if engine == "spd-say":
        # `spd-say` usa uma escala de -100 a 100, não palavras por minuto.
        return ["spd-say", "-r", str(_spd_rate(config.rate_wpm)), "-w", text]

    raise SpeechUnavailable(f"motor de voz desconhecido: {engine}")


def _sapi_rate(wpm: int) -> int:
    """SAPI usa -10..10 em vez de palavras por minuto.

    ~200 wpm é o normal de fala; a escala é aproximadamente logarítmica, mas
    linearizar em torno do centro é bom o bastante para a faixa útil.
    """
    return max(-10, min(10, round((wpm - 200) / 25)))


def _spd_rate(wpm: int) -> int:
    return max(-100, min(100, round((wpm - 200) / 2)))


class SystemSpeaker:
    """Fala usando o sintetizador do sistema."""

    def __init__(self, config: VoiceConfig | None = None, *, engine: str = "") -> None:
        self._config = config or VoiceConfig()
        self._engine = engine or detect_engine()
        if not self._engine:
            raise SpeechUnavailable(
                "nenhum sintetizador encontrado — no Linux, instale espeak-ng"
            )
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    @property
    def engine(self) -> str:
        return self._engine

    def say(self, text: str) -> None:
        """Lança a fala e volta. Nunca espera, nunca levanta."""
        clean = " ".join(text.split())
        if not clean:
            return

        self.stop()
        try:
            with self._lock:
                self._process = subprocess.Popen(  # noqa: S603
                    build_command(self._engine, clean, self._config),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except (OSError, SpeechUnavailable) as exc:
            # Um sintetizador que sumiu no meio da sessão silencia o rádio; não
            # pode derrubar a captura nem a interface por causa disso.
            _log.warning("não foi possível falar: %s", exc)

    def stop(self) -> None:
        """Corta a fala em andamento.

        Chamado antes de cada fala nova, e é o que implementa "a nota atual
        vale mais que a anterior" — ver a política em `radio.VoiceRadio`.
        """
        with self._lock:
            process, self._process = self._process, None
        if process is None or process.poll() is not None:
            return
        # O processo pode ter terminado entre o `poll()` acima e aqui.
        with contextlib.suppress(OSError):
            process.terminate()

    @property
    def is_speaking(self) -> bool:
        with self._lock:
            process = self._process
        return process is not None and process.poll() is None
