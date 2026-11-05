"""Text for a vehicles-status payload that the caller already collected."""

from __future__ import annotations

from typing import Any

from ..providers.metadata import PROVIDERS
from ..providers.urls import chase_operator_url
from ..simulators.render import simulator_lines
from .models import VehicleResult


def format_vehicles_status(status: dict[str, Any]) -> str:
    return "\n\n".join(_vehicle_block(vehicle) for vehicle in status.get("vehicles") or [])


def _vehicle_block(vehicle: dict[str, Any]) -> str:
    # A PiCar reports only its state; a simulator also reports its frontend.
    if "frontend" not in vehicle:
        heading = f"{PROVIDERS[vehicle['provider']].heading}  {vehicle['state']} at {vehicle['url']}"
        return heading + (f": {vehicle['error']}" if vehicle.get("error") else "")
    return "\n".join(simulator_lines(vehicle))


def render_vehicle_result(result: VehicleResult) -> str:
    if result.kind == "error":
        return result.error or ""
    if result.kind == "default":
        return f"{chase_operator_url(result.url)} is the default simulator and is always listed."
    if result.kind == "missing":
        names = ", ".join(PROVIDERS[v["provider"]].heading_at(v["url"]) for v in result.vehicles)
        return f"No discovered vehicle at {result.url}. " + (f"Discovered: {names}." if names else "None discovered.")
    if result.kind == "removed":
        return "\n".join(f"Removed {PROVIDERS[v['provider']].heading_at(v['url'])}" for v in result.vehicles)
    vehicle = PROVIDERS[result.provider]
    heading = vehicle.heading_at(result.url)
    if result.kind == "listed":
        return f"Already listed: {heading}"
    if result.kind == "unready":
        return f"No {vehicle.machine} can take a host at {result.url}: {result.error}"
    if result.kind == "discovered":
        return f"Discovered {heading}" + ("" if result.ready else f", which cannot take a host now: {result.error}")
    raise ValueError(f"Unknown vehicle outcome: {result.kind}")
