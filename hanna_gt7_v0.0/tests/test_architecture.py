"""
Teste de arquitetura — a regra que sustenta o resto do projeto.

A auditoria registrou como P2 (crítico) que `domain/interfaces/telemetry_source.py`
importava `PySide6.QtCore`, e que essa única dependência obrigava o núcleo
inteiro a ter um event loop gráfico: bot do Discord, worker de IA, servidor
headless e teste unitário ficavam todos bloqueados por ela.

A correção não é durável se nada a vigia. Este arquivo falha o build no momento
em que alguém reintroduzir Qt (ou qualquer camada de cima) dentro de `gt7core`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

CORE_ROOT = Path(__file__).resolve().parent.parent / "gt7core"

# Nada disto pode aparecer num import de `gt7core`. Qt é o motivo original;
# os outros existem para que o núcleo não passe a depender dos plugins que
# deveriam depender dele. `gt7app` entrou na lista quando o adaptador Qt
# nasceu — é justamente o tipo de dependência invertida que se quer barrar.
FORBIDDEN_PREFIXES = ("PySide6", "PyQt5", "PyQt6", "gt7app", "gt7ai", "gt7discord")


def _core_modules() -> list[Path]:
    return sorted(p for p in CORE_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_roots(path: Path) -> set[str]:
    """Módulos-raiz importados por um arquivo, via AST.

    Análise estática e não `import` de verdade: um import dinâmico escondido
    dentro de função ainda é pego, e o teste não precisa que as dependências
    opcionais estejam instaladas para rodar.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])

    return roots


def test_existem_modulos_para_verificar() -> None:
    """Guarda contra o teste passar por não ter encontrado arquivo nenhum."""
    assert len(_core_modules()) >= 10


@pytest.mark.parametrize("module_path", _core_modules(), ids=lambda p: p.name)
def test_gt7core_nao_importa_qt_nem_camadas_superiores(module_path: Path) -> None:
    """`gt7core` depende só de stdlib + pycryptodome.

    Se este teste falhar, o núcleo deixou de rodar headless — e com ele foram
    embora os testes, o bot do Discord e o worker de IA.
    """
    offenders = {
        root
        for root in _imported_roots(module_path)
        if any(root == prefix or root.startswith(f"{prefix}.")
               for prefix in FORBIDDEN_PREFIXES)
    }

    assert not offenders, (
        f"{module_path.relative_to(CORE_ROOT.parent)} importa {sorted(offenders)}. "
        "gt7core precisa rodar sem interface gráfica."
    )


CORE_MODULES = (
    "gt7core.domain.models",
    "gt7core.events.bus",
    "gt7core.telemetry.protocol",
    "gt7core.telemetry.engine",
    "gt7core.telemetry.recording",
    "gt7core.telemetry.sources.base",
    "gt7core.telemetry.sources.mock",
    "gt7core.telemetry.sources.udp",
    "gt7core.telemetry.sources.factory",
    "gt7core.analytics.delta",
    "gt7core.analytics.series",
    "gt7core.config.settings",
    "gt7core.observability.logging",
    "gt7core.observability.metrics",
)


class _BlockImport:
    """Meta path finder que recusa um pacote, esteja ele instalado ou não."""

    def __init__(self, *blocked: str) -> None:
        self._blocked = blocked

    def find_spec(self, name: str, path: object = None, target: object = None) -> None:
        if name.split(".")[0] in self._blocked:
            raise ImportError(f"{name} bloqueado pelo teste de arquitetura")
        return None


def test_nucleo_importa_com_qt_bloqueado() -> None:
    """Prova viva: com PySide6 **impossível de importar**, o núcleo sobe.

    A versão anterior deste teste apenas confiava em o ambiente não ter PySide6
    instalado — o que deixou de ser verdade assim que passamos a testar o
    adaptador Qt. Bloquear o import ativamente prova a propriedade em qualquer
    ambiente, e continuaria valendo mesmo num container com Qt instalado.
    """
    import importlib
    import sys

    blocker = _BlockImport("PySide6", "PyQt5", "PyQt6")
    saved = {name: sys.modules.pop(name) for name in CORE_MODULES if name in sys.modules}
    sys.meta_path.insert(0, blocker)  # type: ignore[arg-type]

    try:
        for module in CORE_MODULES:
            assert importlib.import_module(module) is not None
    finally:
        sys.meta_path.remove(blocker)  # type: ignore[arg-type]
        sys.modules.update(saved)
