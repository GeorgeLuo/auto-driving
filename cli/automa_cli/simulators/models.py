"""Simulator lifecycle facts returned to command presentation."""
from dataclasses import dataclass
from typing import Any, Literal

@dataclass(frozen=True)
class SimulatorResult:
    exit_code: int
    kind: Literal['error', 'create_error', 'created', 'deleted', 'missing', 'blocked']
    url: str = ""
    error: str | None = None
    urls: tuple[str, ...] = ()
    hosts: tuple[str, ...] = ()
    killed: bool = False
    exited: bool = False


@dataclass(frozen=True)
class SimulatorsStatus:
    """``simulators status``: its exit code and the ``automa_simulators_status`` document.

    ``payload`` is what ``--json`` prints, and what any other reader serves.
    ``created`` is every record's operator URL, which a missing ``--url`` names.
    """

    exit_code: int
    payload: dict[str, Any]
    created: tuple[str, ...] = ()
