"""Fabricante de um carro (montadora)."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Maker:
    """Montadora. Imutável: vem do catálogo estático do jogo e nunca muda
    durante a execução."""

    id: int
    name: str
