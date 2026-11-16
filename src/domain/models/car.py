"""Carro do catálogo do GT7."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Car:
    """Um carro.

    `id` e `name` vêm do catálogo do jogo (cars.csv) ou do banco, quando o
    usuário digitou o nome à mão.
    """

    id: int
    name: str
    maker_id: int | None = None
