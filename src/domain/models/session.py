"""Sessão de pilotagem — agrupa as voltas rodadas com um mesmo carro/pista."""

from dataclasses import dataclass, field, replace
from datetime import datetime

from .car import Car
from .lap import Lap
from .track import Track


@dataclass(slots=True)
class Session:
    """Uma sessão: carro + pista + janela de tempo + voltas rodadas.

    Conceito novo, sem equivalente no app antigo (que só conhecia voltas
    soltas). É o que dá um lugar natural para "melhor volta desta sessão" e
    para o delta contra a volta anterior, hoje espalhados em estado mutável
    dentro do gravador.

    `end` nulo significa sessão em andamento.
    """

    id: int | None = None
    car: Car | None = None
    track: Track | None = None
    start: datetime | None = None
    end: datetime | None = None
    laps: list[Lap] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        return self.end is None

    @property
    def lap_count(self) -> int:
        return len(self.laps)

    @property
    def best_lap(self) -> Lap | None:
        """Volta mais rápida da sessão, ignorando voltas sem tempo válido."""
        timed = [lap for lap in self.laps if lap.lap_time_ms > 0]
        return min(timed, key=lambda lap: lap.lap_time_ms) if timed else None

    @property
    def last_lap(self) -> Lap | None:
        return self.laps[-1] if self.laps else None

    @property
    def duration_s(self) -> float | None:
        """Duração da sessão em segundos; None enquanto não terminou."""
        if self.start is None or self.end is None:
            return None
        return (self.end - self.start).total_seconds()

    def add_lap(self, lap: Lap) -> Lap:
        """Registra a volta na sessão e devolve o registro guardado.

        O registro é uma cópia **sem as amostras**. A sessão precisa saber
        quais voltas foram rodadas e em que tempo; as amostras pertencem ao
        repositório, e quem as quer em memória (os comparadores de delta) já
        guarda a sua própria referência.

        Guardar as amostras aqui fazia a memória crescer com o tempo de
        sessão e nunca cair: cada volta de 90 s a 60 Hz são ~5.400
        `TelemetryPoint`, e uma sessão de duas horas segurava todas elas até
        o app fechar. Numa sessão curta o efeito é invisível — foi preciso
        simular uma sessão longa para o problema aparecer.

        Devolver o registro permite a quem chamou completar o `id` quando a
        gravação terminar, sem precisar procurá-lo na lista depois.
        """
        registro = replace(lap, points=[])
        self.laps.append(registro)
        return registro
