"""
O que é falado, e o que é engolido.

A voz tem uma restrição que nenhuma tela tem: **é serial e não dá para
reler.** Um cartão pode ficar ali até o piloto ter tempo; uma frase falada
acontece uma vez, ocupa alguns segundos, e durante esses segundos nada mais
pode ser dito.

Daí as três regras deste módulo.

**Só o nível 1.** Debrief e relatório de sessão não são falados. Ler quatro
parágrafos em voz alta com o carro em movimento é pior que silêncio: ocupa o
canal por meio minuto e o piloto não retém nada. `Advice.speech()` devolve só o
título justamente para isto — foi projetado na Fase 7 com este momento em mente.

**Nota nova interrompe a anterior.** Não há fila. Se uma nota chega enquanto
outra é falada, a antiga é cortada — ela já perdeu para a mais recente, e
enfileirar significaria falar sobre a Curva 1 quando o piloto já está na 3.
Conselho fora de hora não é neutro: ele manda corrigir a curva errada.

**Não repete.** A mesma frase duas vezes seguidas soa como defeito, e o piloto
para de escutar. Duas notas idênticas em sequência viram uma.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from gt7core.config.settings import VoiceConfig
from gt7core.observability.logging import get_logger

from .speaker import Speaker

_log = get_logger(__name__)

# Níveis que a voz diz. Os outros existem para ser lidos.
SPOKEN_LEVELS = frozenset({"quick"})

# Uma frase de rádio raramente passa disto. O corte é por palavra e não por
# caractere porque o orçamento real é de tempo, e tempo de fala é proporcional a
# palavras.
WORDS_PER_MINUTE_FLOOR = 60


@dataclass
class VoiceRadio:
    """Decide se um conselho vira fala, e entrega ao sintetizador."""

    speaker: Speaker
    config: VoiceConfig
    clock: Callable[[], float] = time.monotonic

    _last_text: str = ""
    _last_at: float = 0.0

    def announce(self, advice: object) -> bool:
        """Fala o conselho, se ele merecer voz. Devolve se falou.

        Aceita `object` e lê por `getattr` porque `gt7voice` **não importa
        `gt7ai`**: a voz precisa funcionar num programa montado sem o plugin de
        IA, e a nota local da Fase 4 é tão falável quanto a do modelo.
        """
        if not self.config.enabled:
            return False

        level = str(getattr(getattr(advice, "level", ""), "value", ""))
        if level and level not in SPOKEN_LEVELS:
            return False

        speech = getattr(advice, "speech", None)
        text = speech() if callable(speech) else str(getattr(advice, "headline", ""))
        return self.say(text)

    def say(self, text: str) -> bool:
        """Fala um texto cru. Aplica corte e supressão de repetição."""
        if not self.config.enabled:
            return False

        clean = " ".join(text.split())
        if not clean:
            return False

        if clean == self._last_text:
            _log.debug("nota idêntica à anterior, não repetida")
            return False

        clean = self.trim(clean)
        self._last_text = clean
        self._last_at = self.clock()
        self.speaker.say(clean)
        return True

    def trim(self, text: str) -> str:
        """Corta ao orçamento de tempo, preferindo terminar numa frase.

        Um conselho longo demais ocuparia o rádio enquanto três curvas passam.
        Cortar numa fronteira de frase soa como brevidade; cortar no meio soa
        como falha de equipamento.
        """
        budget = max(1, int(self.word_budget))
        words = text.split()
        if len(words) <= budget:
            return text

        head = " ".join(words[:budget])
        for mark in (". ", "; ", ", "):
            cut = head.rfind(mark)
            if cut > len(head) // 2:
                return head[: cut + 1].rstrip()
        return head

    @property
    def word_budget(self) -> float:
        rate = max(WORDS_PER_MINUTE_FLOOR, self.config.rate_wpm)
        return rate * self.config.max_seconds / 60.0

    def silence(self) -> None:
        """Cala agora. Usado ao parar a captura e ao fechar o programa."""
        self.speaker.stop()
        self._last_text = ""
