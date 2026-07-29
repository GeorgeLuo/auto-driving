"""Catalog of decision engines and proposal plugins for M006 shadow-proposals."""

from __future__ import annotations

from autonomy.decision.action_proposal import ActionProposal
from autonomy.decision.decision_data import DecisionDataSource
from autonomy.decision.shadow_runner import (
    ENGINE_ID,
    ShadowProposalsConfig,
    ShadowProposalsEngine,
)
from implementations.decision.proposals.avoid_recent_obstruction import (
    PLUGIN_ID,
    propose as avoid_propose,
)

# Implementation catalog is the sole authority for known proposal plugin ids.
KNOWN_PROPOSAL_PLUGIN_IDS: frozenset[str] = frozenset({PLUGIN_ID})


def create_shadow_proposals_engine(
    config: ShadowProposalsConfig | None = None,
) -> ShadowProposalsEngine:
    cfg = config or ShadowProposalsConfig()
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
    return ShadowProposalsEngine.create(
        config=cfg,
        plugins=plugins,
    )


KNOWN_ENGINES = {
    ENGINE_ID: create_shadow_proposals_engine,
}
