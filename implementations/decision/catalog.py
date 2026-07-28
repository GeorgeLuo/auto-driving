"""Catalog of decision engines and proposal plugins for M006 shadow-proposals."""

from __future__ import annotations

from autonomy.decision.decision_data import DecisionDataSource
from autonomy.decision.action_proposal import ActionProposal
from autonomy.decision.shadow_runner import (
    ENGINE_ID,
    ShadowProposalsConfig,
    ShadowProposalsEngine,
)
from implementations.decision.proposals.avoid_recent_obstruction import (
    PLUGIN_ID,
    propose as avoid_propose,
)


def create_shadow_proposals_engine(
    config: ShadowProposalsConfig | None = None,
) -> ShadowProposalsEngine:
    cfg = config or ShadowProposalsConfig(known_plugins=frozenset({PLUGIN_ID}))

    def _bound(source: DecisionDataSource) -> ActionProposal:
        return avoid_propose(
            source,
            accepted_kinds=cfg.accepted_kinds,
            retained_max_age_ms=cfg.retained_max_age_ms,
            steer_magnitude=cfg.steer_magnitude,
        )

    return ShadowProposalsEngine.create(
        config=cfg,
        plugins={PLUGIN_ID: _bound},
    )


KNOWN_ENGINES = {
    ENGINE_ID: create_shadow_proposals_engine,
}
