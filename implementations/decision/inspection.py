"""Side-by-side scenarios for the packaged proposal. The CLI only runs them."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from implementations.decision.config import parse_engine_config


def prepare_inspection_scenarios(
    memory: dict[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Place image evidence from shared_memory["decision.snapshot"] on each side and name the scenarios."""

    cfg = parse_engine_config(config)
    scenarios: dict[str, dict[str, Any]] = {}
    for side in ("left", "right"):
        scenario_memory = copy.deepcopy(memory)
        changed: list[str] = []
        for record in scenario_memory.get("records", []):
            location = record.get("location") if isinstance(record, dict) else None
            if (
                isinstance(location, dict)
                and location.get("frame") == "image"
                and record.get("kind") in cfg.accepted_kinds
            ):
                location.update(zone=side, bbox_xyxy_norm=None, polygon_xy_norm=None)
                changed.append(str(record.get("record_id")))
        if not changed:
            raise ValueError(
                "Selected frame has no supported retained image evidence to reposition."
            )
        scenarios[side] = {
            "memory": scenario_memory,
            "label": f"{side.capitalize()} obstruction",
            "context": f"Obstruction on the {side}",
            "obstruction_side": side,
            "changed_record_ids": changed,
        }
    return scenarios
