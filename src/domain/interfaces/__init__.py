"""
Contratos (ABCs) que o domínio expõe para as camadas de fora.

As implementações concretas vivem em `infrastructure/`; o domínio só conhece
estas interfaces. Isso é o que permite trocar SQLite por JSON, ou o listener
UDP real por um replay de arquivo, sem tocar em regra de negócio.

Sobre `QABCMeta` — por que ele existe
-------------------------------------
`TelemetrySource` precisa ser ao mesmo tempo uma ABC (contrato) e um QObject
(para expor um `Signal`). Em PySide6 isso não sai de graça:

1. `class X(QObject, ABC)` falha **no import** com "metaclass conflict": a
   metaclasse de QObject (`Shiboken.ObjectType`) não é compatível com `ABCMeta`.

2. Combinar as duas na ordem intuitiva — `class QABCMeta(type(QObject), ABCMeta)`
   — resolve o import mas cria uma ABC **falsa**: como `ObjectType` vem antes de
   `ABCMeta` no MRO, o `ABCMeta.__new__` nunca roda e `__abstractmethods__`
   sequer é populado. A classe abstrata instancia numa boa e o contrato vira
   decoração.

3. Mesmo com `ABCMeta` primeiro (populando `__abstractmethods__`), o Shiboken
   ainda ignora a checagem de abstração que o `type.__call__` normalmente faz.

Daí as duas medidas abaixo: `ABCMeta` na frente **e** um `__call__` explícito.
Só `TelemetrySource` usa isto. Os repositórios são `ABC` puro, sem Qt — manter o
domínio livre de framework onde dá é justamente o ponto da arquitetura.
"""

from abc import ABCMeta

from PySide6.QtCore import QObject


class QABCMeta(ABCMeta, type(QObject)):
    """Metaclasse para ABCs que também precisam ser QObject.

    `ABCMeta` vem primeiro de propósito: é o `__new__` dele que calcula
    `__abstractmethods__`. Ver explicação no docstring do módulo.
    """

    def __call__(cls, *args, **kwargs):
        # O Shiboken não aplica a checagem padrão de classe abstrata, então
        # ela é feita aqui na mão — sem isto, uma subclasse que esqueceu de
        # implementar um método abstrato só quebraria lá na frente, em runtime.
        missing = getattr(cls, "__abstractmethods__", frozenset())
        if missing:
            raise TypeError(
                f"Can't instantiate abstract class {cls.__name__} without an "
                f"implementation for abstract method(s): {', '.join(sorted(missing))}"
            )
        return super().__call__(*args, **kwargs)


__all__ = ["QABCMeta"]
