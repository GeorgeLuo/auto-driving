from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from autonomy.perception.interface import PerceivedThing


@dataclass(frozen=True)
class PerceptionPluginResult:
    lines: tuple[str, ...] = ()
    things: tuple[PerceivedThing, ...] = ()
    observations: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    limits: tuple[str, ...] = ()
