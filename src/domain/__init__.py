"""
Camada de domínio: modelos, contratos e regras de análise.

Regra da arquitetura: este pacote não importa nada de `application`,
`infrastructure` ou `presentation`. A única dependência de framework tolerada
é o `QObject`/`Signal` em `interfaces/telemetry_source.py`, e o motivo está
documentado em `interfaces/__init__.py`.
"""
