"""Values returned by provider discovery and readiness probes."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

DEFAULT_READINESS_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class Candidate:
    provider: str
    url: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProbeResult:
    active: bool
    candidate: Candidate
    vehicle: dict[str, Any] | None = None
    error: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    checked_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> dict[str, Any]:
        data = {
            "active": self.active,
            "candidate": self.candidate.to_dict(),
            "checked_at_ms": self.checked_at_ms,
        }
        if self.vehicle is not None:
            data["vehicle"] = self.vehicle
        if self.error is not None:
            data["error"] = self.error
        if self.diagnostics:
            data["diagnostics"] = self.diagnostics
        return data


def picar_state(probe: ProbeResult) -> str:
    if probe.active:
        return "answering"
    reachable = probe.diagnostics.get("runtime_state") in {
        "server_not_listening",
        "http_unhealthy",
    }
    return "reachable, no host listening" if reachable else "unreachable"
