"""Pista/circuito."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Track:
    """Uma pista.

    `length_m` é o que viabiliza a auto-identificação: o GT7 não informa qual
    pista está em uso, então o comprimento medido da volta é comparado com o
    catálogo (ver `TrackRepository.guess_by_length`).

    `location`/`corners` são os nomes pedidos pela arquitetura; o catálogo
    atual traz `country_id` e `num_corners`, mapeados aqui. Ambos opcionais —
    pistas criadas pelo usuário (digitadas à mão) só têm id e nome.
    """

    id: int
    name: str
    length_m: float | None = None
    location: str | None = None
    corners: int | None = None
    is_oval: bool = False
    is_reverse: bool = False
    # Frações de distância onde cada setor termina (ex.: [0.31, 0.68, 1.0]).
    # None = divisão padrão em partes iguais. O GT7 não transmite os pontos
    # oficiais de setor, então isto é o que permite alinhar os cortes ao
    # traçado real em vez de assumir terços.
    sector_fractions: list[float] | None = None
