"""Default proposal selection for the packaged hold and mode-gated engines.

An engine config is a proposal selection document: ``plugins``,
``plugin_specs`` and ``plugin_configs``, resolved by the common plugin manager
(see ``autonomy.decision_cycle.proposal.selection``). The default selects the
packaged ``avoid_recent_obstruction`` proposal with its named defaults.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from implementations.decision_cycle.proposal.avoid_recent_obstruction.plugin import (
    DEFAULT_ACCEPTED_KINDS,
    DEFAULT_RETAINED_MAX_AGE_MS,
    DEFAULT_STEER_MAGNITUDE,
    PLUGIN_ID,
    PLUGIN_SPEC,
)


def default_engine_config() -> dict[str, Any]:
    """The packaged proposal selection with its named defaults."""

    return {
        "plugins": [PLUGIN_ID],
        "plugin_specs": {PLUGIN_ID: PLUGIN_SPEC},
        "plugin_configs": {
            PLUGIN_ID: {
                "accepted_kinds": list(DEFAULT_ACCEPTED_KINDS),
                "retained_max_age_ms": DEFAULT_RETAINED_MAX_AGE_MS,
                "steer_magnitude": DEFAULT_STEER_MAGNITUDE,
            }
        },
    }


def normalize_engine_config(engine_config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a detached selection document; nothing supplied means the default."""

    if not engine_config:
        return default_engine_config()
    if not isinstance(engine_config, Mapping):
        raise TypeError("engine_config must be a mapping")
    document = deepcopy(dict(engine_config))
    document.setdefault("plugin_configs", {})
    return document
