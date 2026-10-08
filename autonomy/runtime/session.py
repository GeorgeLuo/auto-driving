"""One run configuration for local and onboard decision hosts.

interval_s paces capture; decisions immediately consume the newest pending frame.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from autonomy.runtime.execution import require_mode

DEFAULT_INTERVAL_S = 0.25


@dataclass(frozen=True)
class RunConfiguration:
    mode: str = "autonomy"
    interval_s: float = DEFAULT_INTERVAL_S
    frames: int = 0

    def __post_init__(self) -> None:
        require_mode(self.mode)
        if not math.isfinite(self.interval_s) or self.interval_s < 0:
            raise ValueError("interval_s must be finite and nonnegative")
        if type(self.frames) is not int or self.frames < 0:
            raise ValueError("frames must be a nonnegative integer")

    def to_dict(self) -> dict:
        return asdict(self)
