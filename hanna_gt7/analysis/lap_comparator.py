"""
Compara a volta em andamento com uma volta de referência (normalmente a
melhor volta salva), ponto a ponto, alinhando por DISTÂNCIA percorrida —
não por tempo. Isso é essencial: se comparássemos por tempo, um trecho
onde você está mais lento (ex: freando mais cedo) desalinharia toda a
comparação daí para frente.

Fica isolado de Qt/interface — só recebe listas de (elapsed_ms, distance_m)
e devolve números. Roda a cada frame (~60x/s), então precisa ser rápido:
usa busca binária em vez de percorrer a lista inteira a cada chamada.
"""

import bisect


class LapComparator:
    def __init__(self, reference_frames: list):
        """reference_frames: lista de tuplas retornadas por
        lap_storage.get_lap_frames() — (elapsed_ms, distance_m, speed_kmh, ...).
        Precisa estar ordenada por distância crescente (já vem assim, pois
        a distância é sempre acumulada durante a volta)."""
        self._distances = [row[1] for row in reference_frames]
        self._elapsed_ms = [row[0] for row in reference_frames]

    @property
    def has_reference(self) -> bool:
        return len(self._distances) > 1

    def delta_ms_at(self, current_distance_m: float, current_elapsed_ms: int):
        """Retorna o delta em milissegundos no ponto de distância informado:
        positivo = você está mais devagar que a referência nesse ponto;
        negativo = você está mais rápido.
        Retorna None se não houver referência suficiente ainda (ex: você já
        percorreu mais distância do que a referência tem registrada)."""
        if not self.has_reference:
            return None

        if current_distance_m <= self._distances[0]:
            reference_ms = self._elapsed_ms[0]
        elif current_distance_m >= self._distances[-1]:
            return None  # já passamos do fim da referência, não há com o que comparar
        else:
            # Busca binária: acha o ponto da referência com distância mais
            # próxima da distância atual, e interpola linearmente entre os
            # dois frames vizinhos para um valor mais suave.
            index = bisect.bisect_left(self._distances, current_distance_m)
            d0, d1 = self._distances[index - 1], self._distances[index]
            t0, t1 = self._elapsed_ms[index - 1], self._elapsed_ms[index]

            if d1 == d0:
                reference_ms = t0
            else:
                ratio = (current_distance_m - d0) / (d1 - d0)
                reference_ms = t0 + ratio * (t1 - t0)

        return current_elapsed_ms - reference_ms
