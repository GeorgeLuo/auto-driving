"""Side-by-side scenarios for the selected proposal plugins. The CLI only runs them."""

from __future__ import annotations

import copy
from typing import Any

from autonomy.decision_cycle.activation import StepActivation
from autonomy.decision_cycle.proposal.runner import ProposalRunner
from implementations.decision_cycle.catalog import packaged_activation


def prepare_inspection_scenarios(
    evidence: list[dict[str, Any]],
    activation: StepActivation | None = None,
) -> dict[str, dict[str, Any]]:
    """Place recorded retained evidence on each side and name the scenarios."""

    # Reposition only evidence some selected proposal accepts.
    accepted_kinds = {
        kind
        for plugin in ProposalRunner.from_activation(
            activation or packaged_activation("proposal")
        ).plugins.values()
        for kind in getattr(plugin, "accepted_kinds", ())
    }
    scenarios: dict[str, dict[str, Any]] = {}
    for side in ("left", "right"):
        scenario_evidence = copy.deepcopy(list(evidence))
        changed: list[str] = []
        for record in scenario_evidence:
            location = record.get("location") if isinstance(record, dict) else None
            if (
                isinstance(location, dict)
                and location.get("frame") == "image"
                and record.get("kind") in accepted_kinds
            ):
                location.update(zone=side, bbox_xyxy_norm=None, polygon_xy_norm=None)
                changed.append(str(record.get("record_id")))
        if not changed:
            raise ValueError(
                "Selected frame has no supported retained image evidence to reposition."
            )
        scenarios[side] = {
            "evidence": scenario_evidence,
            "label": f"{side.capitalize()} obstruction",
            "context": f"Obstruction on the {side}",
            "obstruction_side": side,
            "changed_record_ids": changed,
        }
    return scenarios
