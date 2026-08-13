"""
Camada de infraestrutura: adaptadores para o mundo externo.

Implementa as interfaces declaradas em `domain/interfaces` — rede (UDP do GT7),
banco (SQLite), arquivos (CSV do catálogo, JSON para exportação). Depende de
`domain`; nunca o contrário.
"""
