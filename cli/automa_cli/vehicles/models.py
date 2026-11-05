"""Results of vehicle discovery and removal."""
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class VehicleResult:
    exit_code: int
    kind: Literal['error', 'listed', 'unready', 'discovered', 'default', 'missing', 'removed']
    url: str = ""
    provider: str = ""
    ready: bool = True
    error: str | None = None
    vehicles: tuple[dict[str, str], ...] = ()
