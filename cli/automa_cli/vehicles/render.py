"""Text for a vehicles-status payload that the caller already collected."""

from __future__ import annotations

from typing import Any

from ..providers.catalog import PROVIDERS
from ..shared.text import indented


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
