"""
`gt7voice` — o rádio falado (§24, parcialmente).

Plugin, não núcleo. Sem dependência nova: usa o sintetizador que o sistema
operacional já traz — `say` no macOS, SAPI no Windows, `espeak-ng` no Linux.
Instalar um motor de TTS em Python para falar uma frase por volta custaria
memória numa máquina que já tem um modelo de 4B dentro.

Ordem de leitura:

1. `speaker` — quem fala. Protocolo de um método, pelo mesmo motivo de
   `AIClient` e `MessageSink`: **áudio não é verificável em teste**, e sem a
   fronteira a política de fala seria verificável só ouvindo;
2. `system` — o sintetizador do sistema, com o comando de cada plataforma;
3. `radio` — o que vira fala e o que é engolido.

Uso típico:

    from gt7voice import VoiceRadio, build_speaker

    radio = VoiceRadio(build_speaker(settings.voice), settings.voice)
    radio.announce(advice)

O que **não** está implementado
-------------------------------
A metade de entrada do §24 — reconhecimento de fala, para o piloto perguntar
"como estou indo?" — não foi construída, e a omissão é deliberada.

Um modelo de STT decente ocupa memória da mesma ordem do modelo de linguagem, e
o alvo desta aplicação é uma máquina de 8 GB rodando um 4B em CPU. Os dois
juntos não cabem, e entregar um reconhecimento ruim seria pior que não ter: o
piloto falaria duas vezes, seria entendido errado, e desligaria.

O ponto de extensão, porém, já existe e não precisa de código novo: o registro
de comandos de `gt7discord` (`discover()` + `handle_message()`) recebe **texto**
e devolve **texto**. Um dia, qualquer transcritor que produza uma string entra
por ali sem tocar em nada disto.
"""

from gt7core.config.settings import VoiceConfig
from gt7core.observability.logging import get_logger

from .radio import SPOKEN_LEVELS, VoiceRadio
from .speaker import NullSpeaker, RecordingSpeaker, Speaker
from .system import SpeechUnavailable, SystemSpeaker, build_command, detect_engine

__all__ = [
    "SPOKEN_LEVELS",
    "NullSpeaker",
    "RecordingSpeaker",
    "Speaker",
    "SpeechUnavailable",
    "SystemSpeaker",
    "VoiceRadio",
    "build_command",
    "build_speaker",
    "detect_engine",
]

_log = get_logger(__name__)


def build_speaker(config: VoiceConfig) -> Speaker:
    """O sintetizador do sistema, ou um mudo se não houver nenhum.

    Nunca levanta: uma máquina sem TTS instalado deve rodar o programa inteiro
    em silêncio, não deixar de abrir.
    """
    if not config.enabled:
        return NullSpeaker()
    try:
        return SystemSpeaker(config)
    except SpeechUnavailable as exc:
        _log.info("voz indisponível: %s", exc)
        return NullSpeaker()
