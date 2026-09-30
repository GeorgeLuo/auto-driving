"""Catalog of proposal plugins and the action composition built from them."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from autonomy.decision_cycle.proposal.values import ActionProposal
from autonomy.decision_cycle.proposal.inputs import DecisionDataSource
from autonomy.decision_cycle.action import ActionComposition, ProposalConfig
from autonomy.decision_cycle.action_gate.values import ActionGate
from implementations.runtime.engines.config import (
    ObstacleAvoidanceConfig,
    engine_config_document,
    parse_engine_config,
)
from implementations.decision_cycle.proposal.avoid_recent_obstruction.plugin import (
    PLUGIN_ID,
    propose as avoid_propose,
)

# Implementation catalog is the sole authority for known proposal plugin ids.
KNOWN_PROPOSAL_PLUGIN_IDS: frozenset[str] = frozenset({PLUGIN_ID})


def validate_engine_config(
    engine_config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the packaged proposal document, or raise if it cannot be used."""

    cfg = parse_engine_config(engine_config)
    for plugin_id in cfg.enabled_plugins:
        if plugin_id not in KNOWN_PROPOSAL_PLUGIN_IDS:
            raise ValueError(
                f"Unknown proposal plugin {plugin_id!r}. "
                f"Known: {', '.join(sorted(KNOWN_PROPOSAL_PLUGIN_IDS))}."
            )
    return engine_config_document(cfg)


def create_action_composition(
    config: ObstacleAvoidanceConfig | Mapping[str, Any] | None = None,
    *,
    gate: ActionGate | None = None,
) -> ActionComposition:
    """Bind the packaged proposal plugins to ``gate`` (the hold gate by default)."""

    cfg = parse_engine_config(config)
    # Reject unknown enabled ids at activation against this catalog (not a
    # caller-supplied known_plugins field).
    for plugin_id in cfg.enabled_plugins:
        if plugin_id not in KNOWN_PROPOSAL_PLUGIN_IDS:
            raise ValueError(f"unknown plugin_id {plugin_id!r}")

    def _bound(source: DecisionDataSource) -> ActionProposal:
        return avoid_propose(
            source,
            accepted_kinds=cfg.accepted_kinds,
            retained_max_age_ms=cfg.retained_max_age_ms,
            steer_magnitude=cfg.steer_magnitude,
        )

    plugins = {PLUGIN_ID: _bound}
    composition = ActionComposition.create(
        config=ProposalConfig(enabled_plugins=cfg.enabled_plugins),
        plugins=plugins,
        gate=gate,
    )
    composition.reported_config = engine_config_document(cfg)
    return composition
