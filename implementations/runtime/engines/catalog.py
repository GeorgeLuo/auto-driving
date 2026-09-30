"""The action composition built from an engine config's proposal selection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from autonomy.decision_cycle.action import ActionComposition
from autonomy.decision_cycle.action_gate.values import ActionGate
from autonomy.decision_cycle.proposal.selection import (
    load_proposal_plugins,
    proposal_manager_from_config,
)
from implementations.decision_cycle.memory.bounded_evidence.ledger import EVIDENCE_KEY
from implementations.runtime.engines.config import normalize_engine_config


def validate_engine_config(
    engine_config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the selection document, or raise if its plugins cannot be loaded."""

    return create_action_composition(engine_config).reported_config


def create_action_composition(
    config: Mapping[str, Any] | None = None,
    *,
    gate: ActionGate | None = None,
) -> ActionComposition:
    """Load the selected proposal plugins and bind them to ``gate`` (hold by default)."""

    document = normalize_engine_config(config)
    manager = proposal_manager_from_config(document)
    composition = ActionComposition.create(
        plugins=load_proposal_plugins(manager),
        gate=gate,
        evidence_key=EVIDENCE_KEY,
    )
    composition.reported_config = document
    return composition
