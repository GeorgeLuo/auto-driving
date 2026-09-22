"""Configuration for the packaged obstruction proposal, shared by live and shadow execution."""

from __future__ import annotations

import math
from dataclasses import dataclass

from autonomy.decision.shadow_runner import ShadowProposalsConfig
from autonomy.decision.shadow_ids import require_ascii_id
from implementations.decision.proposals.avoid_recent_obstruction import (
    PLUGIN_ID,
    DEFAULT_ACCEPTED_KINDS,
    DEFAULT_RETAINED_MAX_AGE_MS,
    DEFAULT_STEER_MAGNITUDE,
)

DEFAULT_ENABLED_PLUGINS = (PLUGIN_ID,)


@dataclass(frozen=True)
class ObstacleAvoidanceConfig(ShadowProposalsConfig):
    """Implementation-owned defaults; core only receives the selected plugin IDs."""

    enabled_plugins: tuple[str, ...] = DEFAULT_ENABLED_PLUGINS
    accepted_kinds: tuple[str, ...] = DEFAULT_ACCEPTED_KINDS
    retained_max_age_ms: int = DEFAULT_RETAINED_MAX_AGE_MS
    steer_magnitude: float = DEFAULT_STEER_MAGNITUDE

    def __post_init__(self) -> None:
        super().__post_init__()
        if type(self.accepted_kinds) not in (list, tuple):
            raise ValueError("accepted_kinds must be a list or tuple of kind ids")
        kinds = tuple(self.accepted_kinds)
        if not kinds or len(kinds) > 8:
            raise ValueError("accepted_kinds must contain 1..8 entries")
        if len(kinds) != len(set(kinds)):
            raise ValueError("accepted_kinds must be unique")
        for kind in kinds:
            require_ascii_id(kind, field_name="accepted_kind")
        if type(self.retained_max_age_ms) is not int:
            raise ValueError("retained_max_age_ms must be a non-bool int")
        age = self.retained_max_age_ms
        if not 1 <= age <= 60_000:
            raise ValueError("retained_max_age_ms must be in 1..60000")
        object.__setattr__(self, "retained_max_age_ms", age)
        try:
            magnitude = float(self.steer_magnitude)
        except (TypeError, ValueError) as exc:
            raise ValueError("steer_magnitude must be numeric") from exc
        if not math.isfinite(magnitude) or not (0.0 < magnitude <= 1.0):
            raise ValueError("steer_magnitude must satisfy 0 < value <= 1")
        object.__setattr__(self, "steer_magnitude", magnitude)
        object.__setattr__(self, "accepted_kinds", kinds)

