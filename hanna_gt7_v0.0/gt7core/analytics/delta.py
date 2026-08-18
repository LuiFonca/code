"""
Delta ao vivo contra uma volta de referência.
"""

import bisect

from ..domain.models import TelemetryPoint


class LapComparator:
    """Compara a volta em andamento com uma de referência, alinhando por
    **distância percorrida** — não por tempo.

    O alinhamento por distância não é detalhe de implementação, é o ponto todo:
    comparando por tempo, um trecho onde o piloto freia mais cedo desalinharia
    toda a comparação dali em diante, e o delta viraria ruído.

    Roda a cada frame recebido (~60x/s), então usa busca binária em vez de
    varrer a lista — daí a exigência de que a referência esteja ordenada por
    distância crescente (o que a distância acumulada garante naturalmente).
    """

    def __init__(self, reference_points: list[TelemetryPoint]):
        # Duas listas paralelas em vez dos objetos: `bisect` precisa de uma
        # sequência ordenada de escalares, e isso evita indireção no laço quente.
        self._distances: list[float] = [p.distance_m for p in reference_points]
        self._elapsed_ms: list[int] = [p.elapsed_ms for p in reference_points]

    @property
    def has_reference(self) -> bool:
        return len(self._distances) > 1

    def delta_ms_at(
        self, current_distance_m: float, current_elapsed_ms: int
    ) -> float | None:
        """Delta em ms na distância informada.

        Positivo = mais devagar que a referência neste ponto; negativo = mais
        rápido. None quando não há com o que comparar — sem referência, ou o
        piloto já passou da distância que a referência cobre.
        """
        if not self.has_reference:
            return None

        if current_distance_m <= self._distances[0]:
            reference_ms: float = self._elapsed_ms[0]
        elif current_distance_m >= self._distances[-1]:
            return None
        else:
            index = bisect.bisect_left(self._distances, current_distance_m)
            d0, d1 = self._distances[index - 1], self._distances[index]
            t0, t1 = self._elapsed_ms[index - 1], self._elapsed_ms[index]

            if d1 == d0:
                reference_ms = t0
            else:
                ratio = (current_distance_m - d0) / (d1 - d0)
                reference_ms = t0 + ratio * (t1 - t0)

        return current_elapsed_ms - reference_ms
