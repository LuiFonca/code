"""
Analytics — tudo o que transforma amostras em respostas.

Python puro: nenhum módulo daqui conhece Qt, banco ou rede. É o que permite a
mesma análise rodar na interface, num teste, no bot do Discord e no Race
Engineer sem duplicação.

A ordem de leitura, se o objetivo for entender o conjunto:

1. `series` — acesso a canais de uma volta, interpolado por distância;
2. `delta` — o comparador ao vivo contra uma referência;
3. `corners` — detecção de curvas, que ancora tudo o que vem depois;
4. `braking`, `throttle`, `tyres` — o que o piloto fez em cada trecho;
5. `timeloss` — onde a volta foi perdida, combinando os anteriores;
6. `driver` — o que se repete volta após volta e portanto descreve o piloto.
"""

from .braking import (
    BrakingComparison,
    BrakingZone,
    braking_consistency,
    compare_braking,
    detect_braking_zones,
)
from .corners import Corner, corner_at, detect_corners, match_corners
from .delta import LapComparator
from .driver import DriverProfile, build_profile
from .matching import match_by_distance
from .series import (
    LapSeries,
    best_combined_sectors,
    compute_delta_series,
    sector_boundaries_m,
    sector_times_from_series,
)
from .throttle import (
    ThrottleApplication,
    ThrottleComparison,
    analyse_throttle,
    compare_throttle,
)
from .timeloss import SegmentLoss, TimeLossReport, analyse_time_loss
from .tyres import (
    SlipConvention,
    StintDegradation,
    TyreBalance,
    TyreEvent,
    detect_tyre_events,
    infer_slip_convention,
    slip_ratio,
    stint_degradation,
    temperature_balance,
)

__all__ = [
    "BrakingComparison",
    "BrakingZone",
    "Corner",
    "DriverProfile",
    "LapComparator",
    "LapSeries",
    "SegmentLoss",
    "SlipConvention",
    "StintDegradation",
    "ThrottleApplication",
    "ThrottleComparison",
    "TimeLossReport",
    "TyreBalance",
    "TyreEvent",
    "analyse_throttle",
    "analyse_time_loss",
    "best_combined_sectors",
    "braking_consistency",
    "build_profile",
    "compare_braking",
    "compare_throttle",
    "compute_delta_series",
    "corner_at",
    "detect_braking_zones",
    "detect_corners",
    "detect_tyre_events",
    "infer_slip_convention",
    "match_by_distance",
    "match_corners",
    "sector_boundaries_m",
    "sector_times_from_series",
    "slip_ratio",
    "stint_degradation",
    "temperature_balance",
]
