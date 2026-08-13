"""Volta completa e suas métricas derivadas."""

from dataclasses import dataclass, field
from datetime import datetime

from .telemetry_point import TelemetryPoint


@dataclass(slots=True)
class Lap:
    """Uma volta gravada.

    `points` é opcional de propósito: listar o histórico não deve carregar
    milhares de amostras por volta só para mostrar uma tabela de tempos. Os
    repositórios devolvem a volta com `points` vazio nas listagens e preenchem
    sob demanda (ver `LapRepository.get_by_id` / `load_points`). Use
    `has_points` antes de calcular qualquer métrica derivada.

    `lap_time_ms` é o tempo **oficial** reportado pelo jogo, não a soma dos
    intervalos das amostras — os dois podem divergir alguns milissegundos e o
    do jogo é a fonte de verdade para recordes.
    """

    id: int | None = None
    car_id: int | None = None
    track_id: int | None = None
    lap_time_ms: int = 0
    start_time: datetime | None = None
    end_time: datetime | None = None
    is_player: bool = True
    points: list[TelemetryPoint] = field(default_factory=list)

    @property
    def has_points(self) -> bool:
        """False quando a volta veio de uma listagem (sem amostras carregadas)."""
        return len(self.points) > 0

    @property
    def duration_ms(self) -> int:
        """Tempo da volta. Prefere o valor oficial do jogo; cai para o tempo
        decorrido da última amostra quando a volta ainda está em andamento."""
        if self.lap_time_ms > 0:
            return self.lap_time_ms
        return self.points[-1].elapsed_ms if self.points else 0

    @property
    def distance_m(self) -> float:
        return self.points[-1].distance_m if self.points else 0.0

    @property
    def avg_speed(self) -> float:
        if not self.points:
            return 0.0
        return sum(p.speed_kmh for p in self.points) / len(self.points)

    @property
    def max_speed(self) -> float:
        return max((p.speed_kmh for p in self.points), default=0.0)

    @property
    def fuel_used(self) -> float | None:
        """Combustível consumido na volta. None quando a volta não tem amostras
        ou veio de um schema antigo sem essa coluna."""
        if not self.points:
            return None
        used = self.points[0].fuel_level - self.points[-1].fuel_level
        return used if used >= 0 else None
