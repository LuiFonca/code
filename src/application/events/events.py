"""
Eventos publicados no barramento.

São dataclasses imutáveis: um evento é um fato que já aconteceu, e ninguém
deve conseguir reescrever o passado a caminho do assinante.

Nomes no passado (`LapCompleted`, não `CompleteLap`) para deixar claro que
são notificações, não comandos.
"""

from dataclasses import dataclass, field

from ...domain.models.lap import Lap
from ...domain.models.telemetry_point import TelemetryPoint


@dataclass(frozen=True, slots=True)
class TelemetryReceived:
    """Uma amostra nova chegou. Publicado a ~60x/s — assinantes precisam ser
    baratos, nada de I/O ou reconstrução de layout aqui.

    Carrega o `TelemetryPoint` já normalizado e também o DTO cru (`frame`),
    porque a UI ao vivo precisa de campos que não sobrevivem à normalização
    (marcha sugerida, faixa de RPM do shift light, número da volta)."""

    point: TelemetryPoint
    frame: object = None


@dataclass(frozen=True, slots=True)
class DeltaUpdated:
    """Delta recalculado contra as referências. `None` em qualquer um dos dois
    significa "sem referência ainda", que é diferente de "delta zero"."""

    delta_best_s: float | None = None
    delta_previous_s: float | None = None


@dataclass(frozen=True, slots=True)
class LapCompleted:
    """Volta terminou e foi persistida."""

    lap: Lap
    lap_id: int
    is_best: bool = False


@dataclass(frozen=True, slots=True)
class LapDiscarded:
    """Volta terminou mas não foi salva. `reason` é texto pronto para exibir —
    normalmente pista indefinida ou modo replay/IA."""

    lap_time_ms: int
    reason: str


@dataclass(frozen=True, slots=True)
class LapSaveFailed:
    """A persistência falhou.

    Existe porque a versão antiga engolia essa falha num `print()`: o piloto
    completava a volta, via tudo normal na tela e só descobria a perda quando
    o histórico vinha vazio."""

    message: str
    lap_time_ms: int = 0


@dataclass(frozen=True, slots=True)
class LapsPurged:
    """A política de retenção descartou voltas antigas.

    Existe para que a poda deixe de ser invisível: antes, voltas simplesmente
    sumiam do histórico sem nenhum sinal na interface."""

    count: int
    track_id: int | None = None


@dataclass(frozen=True, slots=True)
class LapDeleted:
    """Volta removida pelo usuário.

    O serviço de telemetria assina isto para largar a referência de delta
    quando a volta apagada era justamente a melhor — sem o aviso, o delta
    seguiria comparando contra uma volta que não existe mais."""

    lap_id: int
    track_id: int | None = None


@dataclass(frozen=True, slots=True)
class SessionStarted:
    session_id: int | None = None
    track_name: str | None = None
    car_name: str | None = None


@dataclass(frozen=True, slots=True)
class SessionEnded:
    session_id: int | None = None
    lap_count: int = 0


@dataclass(frozen=True, slots=True)
class TrackChanged:
    """Pista trocada. `track_id` nulo = nenhuma pista válida definida, e nesse
    estado nada é persistido."""

    track_id: int | None
    track_name: str | None = None


@dataclass(frozen=True, slots=True)
class CarChanged:
    car_id: int | None
    car_name: str | None = None


@dataclass(frozen=True, slots=True)
class CarDetected:
    """Carro identificado automaticamente pelo id que vem no pacote."""

    car_name: str
    car_id: int | None = None


@dataclass(frozen=True, slots=True)
class TrackCandidatesDetected:
    """Pistas compatíveis com o comprimento da volta que acabou de fechar.

    Quase sempre vem mais de um candidato — traçados de comprimento parecido
    são indistinguíveis só pela distância. Cabe à UI deixar o usuário escolher."""

    names: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ConnectionStateChanged:
    """Estado da captura: "conectando", "recebendo", "sem_sinal", "stale",
    "desconectado", "erro"."""

    state: str
    message: str = ""
