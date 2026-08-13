"""Contrato de uma fonte de telemetria."""

from abc import abstractmethod

from PySide6.QtCore import QObject, Signal

from . import QABCMeta


class TelemetrySource(QObject, metaclass=QABCMeta):
    """Origem de um fluxo de telemetria.

    A implementação real é o listener UDP do GT7, mas o contrato é deliberadamente
    agnóstico: um leitor de arquivo de replay ou um gerador sintético para testes
    satisfazem a mesma interface, e o `TelemetryService` não nota diferença.

    Usa `QABCMeta` porque precisa ser ABC **e** QObject ao mesmo tempo — ver o
    docstring de `interfaces/__init__.py` para o porquê da metaclasse.

    Sobre threads: a implementação real emite `telemetry_stream` de dentro de
    uma thread de rede. É por isso que o contrato exige um `Signal` e não um
    callback — o Qt entrega em conexão enfileirada e o assinante roda na thread
    dele, não na de rede.
    """

    # Emite o DTO cru da fonte (um TelemetryFrame, no caso do GT7). O domínio
    # não tipa o payload aqui de propósito: converter DTO -> TelemetryPoint é
    # trabalho da camada de aplicação, não da fonte.
    telemetry_stream = Signal(object)

    # Mudança de estado da conexão: "conectando", "recebendo", "sem_sinal", "erro".
    status_changed = Signal(str)

    # Falha não-recuperável (porta ocupada, host inalcançável...).
    error_occurred = Signal(str)

    @abstractmethod
    def start(self) -> None:
        """Começa a receber telemetria. Idempotente: chamar com a fonte já
        ativa não deve duplicar o fluxo."""

    @abstractmethod
    def stop(self) -> None:
        """Encerra a captura e libera os recursos (socket, thread). Deve ser
        seguro chamar mesmo se nunca foi iniciada."""

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """True enquanto a fonte estiver ativa."""
