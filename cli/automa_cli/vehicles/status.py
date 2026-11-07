"""``automa vehicles status``: collect each listed vehicle's state."""

from __future__ import annotations

from typing import Any

from ..hosts.discovery import hosts_by_vehicle
from ..providers.registry import CHASE_SIM, probe_candidates
from ..providers.models import DEFAULT_READINESS_TIMEOUT_S
from ..simulators.store import created_simulators
from .discovery import listed_vehicles, normalized_url


def vehicles_status(
    *,
    url: str | None = None,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
) -> tuple[int, dict[str, Any]]:
    """The exit and the ``automa_vehicles_status`` payload: each listed vehicle's state.

    Status looks at each simulator ``simulators create`` started that is
    still listed, then each vehicle ``vehicles discover`` listed. Nothing is
    listed implicitly. A simulator runs when its server answers; its frontend
    is connected when a browser has the page open. Each vehicle names the
    hosts created at it. Every vehicle is probed in parallel within the one
    deadline. With ``url``, in either form ``vehicles discover`` takes, only
    that vehicle is probed; one that is not listed is a "not found" entry.
    The exit is 1 when vehicles are shown and none is reachable, a "not
    found" entry included; showing none exits 0.
    """

    candidates = listed_vehicles()
    if url is not None:
        candidates = [candidate for candidate in candidates
                      if normalized_url(candidate.provider, url) == candidate.url]
    vehicles = probe_candidates(candidates, timeout_s)
    created = {simulator["url"]: simulator["pid"] for simulator in created_simulators()}
    hosts = hosts_by_vehicle()
    for candidate, vehicle in zip(candidates, vehicles):
        if candidate.provider == CHASE_SIM.name:
            vehicle["pid"] = created.get(candidate.url)
        vehicle["hosts"] = hosts.get((candidate.provider, candidate.url), [])
    if url is not None and not vehicles:
        vehicles.append({"provider": None, "url": url, "state": "not found", "error": None, "hosts": []})
    exit_code = 1 if vehicles and all(vehicle["state"] in {"unreachable", "not found"} for vehicle in vehicles) else 0
    return exit_code, {"schema": "automa_vehicles_status", "vehicles": vehicles}
