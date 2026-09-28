"""Catalog of decision engines and proposal plugins for M006 shadow-proposals."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from autonomy.decision.action_proposal import ActionProposal
from autonomy.decision.decision_data import DecisionDataSource
from autonomy.decision.shadow_runner import (
    ENGINE_ID,
    ShadowProposalsConfig,
    ShadowProposalsEngine,
)
from autonomy.plugins import LocalPluginCatalog, PluginDefinition, PluginManager
from implementations.decision.config import (
    ObstacleAvoidanceConfig,
    engine_config_document,
    parse_engine_config,
)
from implementations.decision.proposals.avoid_recent_obstruction import (
    PLUGIN_ID,
    propose as avoid_propose,
)

# Packaged definitions resolve through the same core catalog as other steps.
PROPOSAL_PLUGIN_CATALOG = LocalPluginCatalog(
    [
        PluginDefinition(
            step="proposal",
            plugin_id=PLUGIN_ID,
            entrypoint="implementations.decision.proposals.avoid_recent_obstruction:propose",
        ),
    ]
)


def validate_engine_config(
    engine_config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the packaged proposal document, or raise if it cannot be used."""

    cfg = parse_engine_config(engine_config)
    PluginManager("proposal", PROPOSAL_PLUGIN_CATALOG).select(cfg.enabled_plugins)
    return engine_config_document(cfg)


def create_shadow_proposals_engine(
    config: ObstacleAvoidanceConfig | Mapping[str, Any] | None = None,
) -> ShadowProposalsEngine:
    cfg = parse_engine_config(config)
    manager = PluginManager("proposal", PROPOSAL_PLUGIN_CATALOG)
    manager.select(cfg.enabled_plugins)

    def _bound(source: DecisionDataSource) -> ActionProposal:
        return avoid_propose(
            source,
            accepted_kinds=cfg.accepted_kinds,
            retained_max_age_ms=cfg.retained_max_age_ms,
            steer_magnitude=cfg.steer_magnitude,
        )

    plugins = {PLUGIN_ID: _bound}
    engine = ShadowProposalsEngine.create(
        config=ShadowProposalsConfig(enabled_plugins=manager.selected_ids),
        plugins=plugins,
    )
    engine.reported_config = engine_config_document(cfg)
    return engine


KNOWN_ENGINES = {
    ENGINE_ID: create_shadow_proposals_engine,
}
