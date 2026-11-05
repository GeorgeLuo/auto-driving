"""Results of vehicle discovery, removal, and status."""
from dataclasses import dataclass
from typing import Any, Literal

@dataclass(frozen=True)
class VehicleResult:
    exit_code: int
    kind: Literal['error', 'listed', 'unready', 'discovered', 'default', 'missing', 'removed']
    url: str = ""
    provider: str = ""
    ready: bool = True
    error: str | None = None
    vehicles: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class VehiclesStatus:
    """The exit code and ``automa_vehicles_status`` document for ``vehicles status``.

    ``payload`` is what ``--json`` prints. The exit is 1 when every listed vehicle is unreachable.
    """

    exit_code: int
    payload: dict[str, Any]
