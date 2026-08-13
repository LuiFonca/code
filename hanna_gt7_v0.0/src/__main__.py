"""
Permite executar o pacote como `python3 src`.

Precisa do mesmo ajuste de `main.py`: rodado assim, o Python coloca a própria
pasta `src/` no sys.path e executa este arquivo com `__package__` vazio, então
o import relativo abaixo falharia sem a correção.
"""

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "src"

from .main import main

if __name__ == "__main__":
    sys.exit(main())
