"""Carro do catálogo do GT7."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Car:
    """Um carro.

    `id` e `name` vêm do catálogo do jogo (cars.csv) ou do banco, quando o
    usuário digitou o nome à mão. Os demais campos são especificação técnica
    que o CSV atual **não** carrega — ficam `None` até existir uma fonte para
    eles. Nada no domínio deve assumir que estão preenchidos.
    """

    id: int
    name: str
    maker_id: int | None = None
    year: int | None = None
    power: int | None = None
    weight: int | None = None
    drivetrain: str | None = None

    def display_name(self, maker_name: str | None = None) -> str:
        """Nome para exibir, prefixado com a montadora quando conhecida."""
        if maker_name and not self.name.startswith(maker_name):
            return f"{maker_name} {self.name}"
        return self.name
