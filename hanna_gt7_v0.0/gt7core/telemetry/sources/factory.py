"""
Escolha da fonte de telemetria a partir da configuração.

É aqui — e só aqui — que o sistema decide entre ao vivo, sintético e replay.
Todo o resto recebe um `TelemetrySource` e não pergunta qual é: é o que torna
o §40 (replay) e o §42 (outros simuladores) mudanças de configuração em vez de
mudanças de código.

Um simulador novo entra adicionando um ramo aqui e uma classe que satisfaça o
contrato. Nada a jusante muda.
"""

from __future__ import annotations

from pathlib import Path

from ...config.settings import Settings
from ...observability.logging import get_logger
from ...observability.metrics import TelemetryMetrics
from .base import TelemetrySource

_log = get_logger(__name__)


class TelemetrySourceError(Exception):
    """Configuração de fonte inválida ou incompleta."""


def create_telemetry_source(
    settings: Settings,
    *,
    replay_path: str | Path | None = None,
    metrics: TelemetryMetrics | None = None,
) -> TelemetrySource:
    """Monta a fonte descrita em `settings.telemetry.source`.

    Valores aceitos: `mock`, `udp`, `replay`. Um valor desconhecido falha na
    hora com a lista de opções — em vez de cair silenciosamente num padrão e
    deixar o usuário se perguntando por que não chega telemetria.
    """
    kind = settings.telemetry.source.strip().lower()

    if kind == "mock":
        from .mock import MockTelemetrySource

        _log.info(
            "fonte de telemetria: sintética",
            extra={"speed": settings.telemetry.mock_speed_multiplier},
        )
        return MockTelemetrySource(
            sample_rate_hz=settings.telemetry.sample_rate_hz,
            speed_multiplier=settings.telemetry.mock_speed_multiplier,
        )

    if kind == "udp":
        from .udp import Gt7UdpTelemetrySource

        ip = settings.telemetry.ps_ip.strip()
        if not ip:
            # Sem IP não há como capturar, e não existe padrão razoável: um IP
            # inventado tentaria falar com a máquina de outra pessoa na rede.
            raise TelemetrySourceError(
                "Fonte 'udp' exige o IP do PlayStation. "
                "Defina GT7_PS_IP no ambiente ou no arquivo .env."
            )

        _log.info("fonte de telemetria: UDP", extra={"ps_ip": ip})
        return Gt7UdpTelemetrySource(
            ip,
            send_port=settings.telemetry.send_port,
            receive_port=settings.telemetry.receive_port,
            metrics=metrics,
        )

    if kind == "replay":
        from ..recording import ReplayTelemetrySource

        if replay_path is None:
            raise TelemetrySourceError(
                "Fonte 'replay' exige o caminho de uma sessão gravada."
            )
        path = Path(replay_path)
        if not path.is_file():
            raise TelemetrySourceError(f"Gravação não encontrada: {path}")

        _log.info("fonte de telemetria: replay", extra={"path": str(path)})
        return ReplayTelemetrySource(path)

    raise TelemetrySourceError(
        f"Fonte de telemetria desconhecida: {settings.telemetry.source!r}. "
        "Use 'mock', 'udp' ou 'replay'."
    )
