"""Pista/circuito."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Track:
    """Uma pista.

    `length_m` é o que viabiliza a auto-identificação: o GT7 não informa qual
    pista está em uso, então o comprimento medido da volta é comparado com o
    catálogo (ver `TrackRepository.guess_by_length`).
    """

    id: int
    name: str
    length_m: float | None = None
    # Frações de distância onde cada setor termina (ex.: [0.31, 0.68, 1.0]).
    # None = divisão padrão em partes iguais. O GT7 não transmite os pontos
    # oficiais de setor, então isto é o que permite alinhar os cortes ao
    # traçado real em vez de assumir terços.
    sector_fractions: list[float] | None = None
