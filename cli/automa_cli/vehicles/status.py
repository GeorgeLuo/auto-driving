"""``automa vehicles status``: collect each listed vehicle's state."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..providers.registry import CHASE_SIM, PROVIDERS
from ..providers.models import DEFAULT_READINESS_TIMEOUT_S
from ..providers.urls import require_positive_timeout
from ..simulators.store import created_simulators
from .discovery import listed_vehicles


def vehicles_status(*, timeout_s: float = DEFAULT_READINESS_TIMEOUT_S) -> tuple[int, dict[str, Any]]:
    """The exit and the ``automa_vehicles_status`` payload: each listed vehicle's state.

    Status looks at the default simulator, each simulator ``simulators
    create`` started, then each vehicle ``vehicles discover`` listed. A
    simulator runs when its server answers; its frontend is connected when a
    browser has the page open. Every vehicle is probed in parallel within the
    one deadline. The exit is 1 when every listed vehicle is unreachable.
    """

    deadline = time.monotonic() + require_positive_timeout(timeout_s)

    def remaining() -> float:
        # The client refuses a zero timeout; a spent budget times out at once instead.
        return max(deadline - time.monotonic(), 1e-3)

    candidates = listed_vehicles()
    with ThreadPoolExecutor() as pool:
        probes = [
            pool.submit(PROVIDERS[candidate.provider].status, candidate, remaining)
            for candidate in candidates
        ]
        vehicles = [probe.result() for probe in probes]

    created = {simulator["url"]: simulator["pid"] for simulator in created_simulators()}
    for candidate, vehicle in zip(candidates, vehicles):
        if candidate.provider == CHASE_SIM.name:
            vehicle["pid"] = created.get(candidate.url)
    exit_code = 1 if all(vehicle["state"] == "unreachable" for vehicle in vehicles) else 0
    return exit_code, {"schema": "automa_vehicles_status", "vehicles": vehicles}
