"""
Estatísticas de captura — §35 do briefing.

O briefing pede que o programa consiga informar pacotes recebidos, perdidos,
pacotes/s e latência de processamento. Nada disso existia: a auditoria registrou
como P11 que não havia contagem de nada.

Tudo aqui é thread-safe porque quem incrementa é a thread de rede e quem lê é a
interface. Os contadores usam um lock em vez de `itertools.count` porque o
snapshot precisa ser coerente entre campos — ler "recebidos" e "descartados" em
momentos diferentes produziria taxas impossíveis na tela.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TelemetryStats:
    """Fotografia dos contadores num instante."""

    packets_received: int
    packets_invalid: int
    packets_dropped: int
    frames_emitted: int
    bytes_received: int
    uptime_s: float
    packets_per_second: float
    last_packet_age_s: float | None

    @property
    def loss_ratio(self) -> float:
        """Fração de pacotes que chegaram mas não viraram quadro.

        Não é perda de rede — UDP perdido nunca chega para ser contado. É a
        proporção de pacotes que foram descartados por serem inválidos (outra
        origem, corrompidos) ou por contrapressão do buffer.
        """
        total = self.packets_received
        return 0.0 if total == 0 else (self.packets_invalid + self.packets_dropped) / total

    def format_summary(self) -> str:
        age = "—" if self.last_packet_age_s is None else f"{self.last_packet_age_s:.1f}s"
        return (
            f"{self.packets_per_second:.0f} pkt/s | "
            f"recebidos {self.packets_received} | "
            f"inválidos {self.packets_invalid} | "
            f"descartados {self.packets_dropped} | "
            f"último há {age}"
        )


class TelemetryMetrics:
    """Contadores da captura. Seguro para incrementar de qualquer thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_at = time.monotonic()
        self._packets_received = 0
        self._packets_invalid = 0
        self._packets_dropped = 0
        self._frames_emitted = 0
        self._bytes_received = 0
        self._last_packet_at: float | None = None

    def reset(self) -> None:
        with self._lock:
            self._started_at = time.monotonic()
            self._packets_received = 0
            self._packets_invalid = 0
            self._packets_dropped = 0
            self._frames_emitted = 0
            self._bytes_received = 0
            self._last_packet_at = None

    def record_packet(self, size_bytes: int) -> None:
        with self._lock:
            self._packets_received += 1
            self._bytes_received += size_bytes
            self._last_packet_at = time.monotonic()

    def record_invalid(self) -> None:
        """Pacote que chegou mas não decodificou — outra origem ou corrompido."""
        with self._lock:
            self._packets_invalid += 1

    def record_dropped(self) -> None:
        """Quadro descartado por contrapressão: o consumidor não acompanhou."""
        with self._lock:
            self._packets_dropped += 1

    def record_frame(self) -> None:
        with self._lock:
            self._frames_emitted += 1

    def snapshot(self) -> TelemetryStats:
        with self._lock:
            now = time.monotonic()
            uptime = max(1e-9, now - self._started_at)
            return TelemetryStats(
                packets_received=self._packets_received,
                packets_invalid=self._packets_invalid,
                packets_dropped=self._packets_dropped,
                frames_emitted=self._frames_emitted,
                bytes_received=self._bytes_received,
                uptime_s=uptime,
                packets_per_second=self._packets_received / uptime,
                last_packet_age_s=(
                    None if self._last_packet_at is None else now - self._last_packet_at
                ),
            )
