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
# deveriam depender dele.
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


def test_nucleo_importa_sem_qt_instalado() -> None:
    """Prova viva: com PySide6 ausente do ambiente, o núcleo importa.

    Este ambiente de teste não tem PySide6 instalado, então o import abaixo só
    passa porque a extração foi feita de verdade.
    """
    import importlib

    for module in (
        "gt7core.domain.models",
        "gt7core.events.bus",
        "gt7core.telemetry.protocol",
        "gt7core.telemetry.engine",
        "gt7core.telemetry.sources.mock",
        "gt7core.analytics.delta",
        "gt7core.analytics.series",
        "gt7core.config.settings",
        "gt7core.observability.logging",
    ):
        assert importlib.import_module(module) is not None
