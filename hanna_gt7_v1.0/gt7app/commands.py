"""
Registro de comandos — a espinha da paleta (⌘K) e dos atalhos.

A ideia é a do briefing: **toda ação da aplicação tem um nome**, e existe um
lugar único onde se digita esse nome. O ganho não é conveniência, é
descobribilidade — um piloto que nunca abriu a página de comparação encontra
"comparar voltas" digitando, sem caçar num menu.

O registro é Python puro (sem Qt) de propósito: a busca e o casamento são
lógica testável, e a paleta visual em `widgets/palette.py` só a apresenta. O
mesmo registro serve, mais adiante, ao bot do Discord e ao comando por voz —
que no briefing operam sobre o mesmo vocabulário de ações.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Command:
    """Uma ação nomeada da aplicação."""

    id: str
    title: str
    run: Callable[[], None]
    category: str = "Geral"
    shortcut: str = ""
    keywords: tuple[str, ...] = field(default_factory=tuple)
    """Termos alternativos de busca — sinônimos, o nome em inglês, abreviações."""

    @property
    def haystack(self) -> str:
        return " ".join((self.title, self.category, *self.keywords)).lower()


class CommandRegistry:
    """Coleção de comandos com busca por subsequência."""

    def __init__(self) -> None:
        self._commands: list[Command] = []

    def register(self, command: Command) -> None:
        """Adiciona um comando. Registrar duas vezes o mesmo id substitui —
        assim uma página pode se recarregar sem duplicar suas ações."""
        self._commands = [c for c in self._commands if c.id != command.id]
        self._commands.append(command)

    def add(
        self,
        command_id: str,
        title: str,
        run: Callable[[], None],
        *,
        category: str = "Geral",
        shortcut: str = "",
        keywords: tuple[str, ...] = (),
    ) -> Command:
        command = Command(
            id=command_id,
            title=title,
            run=run,
            category=category,
            shortcut=shortcut,
            keywords=keywords,
        )
        self.register(command)
        return command

    def all(self) -> list[Command]:
        return list(self._commands)

    def get(self, command_id: str) -> Command | None:
        return next((c for c in self._commands if c.id == command_id), None)

    def search(self, query: str, *, limit: int = 12) -> list[Command]:
        """Comandos que casam com a consulta, do melhor para o pior.

        O casamento é por **subsequência**, não por substring: digitar `cmp`
        encontra "comparar voltas". É o comportamento que se espera de uma
        paleta de comandos, e o que a torna rápida de usar — ninguém quer
        digitar o nome inteiro.
        """
        cleaned = query.strip().lower()
        if not cleaned:
            return self._commands[:limit]

        scored: list[tuple[int, int, Command]] = []
        for index, command in enumerate(self._commands):
            score = _match_score(cleaned, command.haystack)
            if score is not None:
                # O índice entra no critério para que a ordem de registro
                # desempate de forma estável.
                scored.append((score, index, command))

        scored.sort(key=lambda item: (item[0], item[1]))
        return [command for _, _, command in scored[:limit]]


def _match_score(query: str, haystack: str) -> int | None:
    """Quão bem a consulta casa com o texto. Menor é melhor; None não casa.

    Prefixo exato ganha de substring, que ganha de subsequência espalhada. A
    distância entre os caracteres casados entra no placar para que
    "comparar" pontue melhor que um casamento que atravessa a string inteira.
    """
    if haystack.startswith(query):
        return 0
    if query in haystack:
        return 1 + haystack.index(query)

    position = 0
    spread = 0
    for character in query:
        found = haystack.find(character, position)
        if found == -1:
            return None
        spread += found - position
        position = found + 1

    return 100 + spread
