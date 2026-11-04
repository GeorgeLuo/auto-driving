"""Simulator lifecycle facts returned to command presentation."""
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class SimulatorResult:
    exit_code: int
    kind: Literal['error', 'create_error', 'created', 'deleted', 'missing', 'blocked']
    url: str = ""
    error: str | None = None
    urls: tuple[str, ...] = ()
    hosts: tuple[str, ...] = ()
    killed: bool = False
