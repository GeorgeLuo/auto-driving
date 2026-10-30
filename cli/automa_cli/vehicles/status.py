"""``automa vehicles status``: each listed vehicle's state."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .discovery import created_simulators, listed_vehicles
from .probes import DEFAULT_READINESS_TIMEOUT_S, require_positive_timeout
from .providers import CHASE_SIM, PROVIDERS


def get_vehicles_status(*, timeout_s: float = DEFAULT_READINESS_TIMEOUT_S) -> dict[str, Any]:
    """Each listed vehicle's state.

    Status looks at the default simulator, each simulator ``simulators
    create`` started, then each vehicle ``vehicles discover`` listed. A
    simulator runs when its server answers; its frontend is connected when a
    browser has the page open. Every vehicle is probed in parallel within the
    one deadline.
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
    return {"schema": "automa_vehicles_status", "vehicles": vehicles}


def format_vehicles_status(status: dict[str, Any]) -> str:
    return "\n\n".join(_vehicle_block(vehicle) for vehicle in status.get("vehicles") or [])


def _vehicle_block(vehicle: dict[str, Any]) -> str:
    heading = f"{PROVIDERS[vehicle['provider']].heading}  {vehicle['state']} at {vehicle['url']}"
    # A PiCar reports only its state; a simulator also reports its frontend.
    if "frontend" not in vehicle:
        return heading + (f": {vehicle['error']}" if vehicle.get("error") else "")
    lines = [heading]
    if vehicle.get("frontend") == "connected" and vehicle.get("game"):
        lines.append(f"  frontend connected, game {vehicle['game']}")
    elif vehicle.get("frontend"):
        lines.append(f"  frontend {vehicle['frontend']}")
    lines += indented(vehicle.get("error"))
    if vehicle.get("pid"):
        lines.append(f"  created by simulators create, pid {vehicle['pid']}")
    return "\n".join(lines)


def indented(text: Any) -> list[str]:
    return [f"  {line}" for line in str(text).splitlines()] if text else []
