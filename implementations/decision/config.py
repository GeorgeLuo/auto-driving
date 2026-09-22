"""Configuration for the packaged obstruction proposal, shared by live and shadow execution."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

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


def engine_config_document(config: ObstacleAvoidanceConfig) -> dict[str, Any]:
    """JSON-ready copy of one packaged proposal configuration."""

    return {
        "enabled_plugins": list(config.enabled_plugins),
        "accepted_kinds": list(config.accepted_kinds),
        "retained_max_age_ms": config.retained_max_age_ms,
        "steer_magnitude": config.steer_magnitude,
    }


def default_engine_config() -> dict[str, Any]:
    """Named defaults for the packaged proposal engines."""

    return engine_config_document(ObstacleAvoidanceConfig())


def parse_engine_config(
    engine_config: ObstacleAvoidanceConfig | Mapping[str, Any] | None,
) -> ObstacleAvoidanceConfig:
    """Accept a mapping or return the named defaults when nothing is supplied."""

    if isinstance(engine_config, ObstacleAvoidanceConfig):
        return engine_config
    if not engine_config:
        return ObstacleAvoidanceConfig()
    if not isinstance(engine_config, Mapping):
        raise TypeError("engine_config must be a mapping")
    return ObstacleAvoidanceConfig(**dict(engine_config))

