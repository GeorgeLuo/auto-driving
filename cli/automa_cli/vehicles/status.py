"""``automa vehicles status``: collect each listed vehicle's state."""

from __future__ import annotations

from typing import Any

from ..providers.registry import CHASE_SIM, probe_candidates
from ..providers.models import DEFAULT_READINESS_TIMEOUT_S
from ..simulators.store import created_simulators
from .discovery import listed_vehicles


def get_vehicles_status(*, timeout_s: float = DEFAULT_READINESS_TIMEOUT_S) -> dict[str, Any]:
    """Each listed vehicle's state.

    Status looks at each simulator ``simulators create`` started that is
    still listed, then each vehicle ``vehicles discover`` listed. Nothing is
    listed implicitly. A simulator runs when its server answers; its frontend
    is connected when a browser has the page open. Every vehicle is probed in
    parallel within the one deadline.
    """

    candidates = listed_vehicles()
    vehicles = probe_candidates(candidates, timeout_s)
    created = {simulator["url"]: simulator["pid"] for simulator in created_simulators()}
    for candidate, vehicle in zip(candidates, vehicles):
        if candidate.provider == CHASE_SIM.name:
            vehicle["pid"] = created.get(candidate.url)
    return {"schema": "automa_vehicles_status", "vehicles": vehicles}
