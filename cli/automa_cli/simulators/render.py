"""Format supplied simulator results without performing operations."""
from __future__ import annotations

from typing import Any

from ..providers.metadata import CHASE_SIM, PROVIDERS
from ..providers.urls import chase_operator_url
from ..shared.text import indented
from .models import SimulatorResult


def simulator_lines(vehicle: dict[str, Any]) -> list[str]:
    """The heading, frontend, error, and pid lines a simulator block prints.

    ``vehicles status`` uses these for a simulator. Lines that only one command
    knows stay with that command.
    """

    heading = f"{PROVIDERS[vehicle['provider']].heading}  {vehicle['state']} at {vehicle['url']}"
    lines = [heading]
    if vehicle.get("frontend") == "connected" and vehicle.get("game"):
        lines.append(f"  frontend connected, game {vehicle['game']}")
    elif vehicle.get("frontend"):
        lines.append(f"  frontend {vehicle['frontend']}")
    lines += indented(vehicle.get("error"))
    if vehicle.get("pid"):
        lines.append(f"  created by simulators create, pid {vehicle['pid']}")
    return lines


def render_simulator_result(result: SimulatorResult) -> str:
    if result.kind == "error":
        return result.error or ""
    if result.kind == "create_error":
        return f"Cannot create a simulator: {result.error}"
    if result.kind == "created":
        return f"Created {CHASE_SIM.heading_at(result.url)}"
    if result.kind == "deleted":
        return f"Deleted {CHASE_SIM.heading_at(result.url)}" + (", killed" if result.killed else "")
    if result.kind == "missing":
        names = ", ".join(chase_operator_url(url) for url in result.urls)
        return f"No created simulator at {result.url}. " + (f"Created: {names}." if names else "None created.")
    if result.kind == "blocked":
        hosts = result.hosts
        subject = f"Host {hosts[0]} uses" if len(hosts) == 1 else f"Hosts {', '.join(hosts)} use"
        return f"{subject} the simulator at {chase_operator_url(result.url)}; delete {'it' if len(hosts) == 1 else 'them'} first."
    raise ValueError(f"Unknown simulator outcome: {result.kind}")
