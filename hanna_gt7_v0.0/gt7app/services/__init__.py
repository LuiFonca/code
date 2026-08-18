"""
Serviços da casca: o que não é widget nem estado de tela.

Por ora um só — a ponte que roda o Race Engineer fora da thread da interface.
"""

from .engineer import EngineerService

__all__ = ["EngineerService"]
