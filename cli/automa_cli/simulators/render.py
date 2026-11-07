"""Format supplied simulator results without performing operations."""
from __future__ import annotations

import json
from typing import Any

from ..providers.metadata import CHASE_SIM
from ..providers.urls import chase_operator_url
from ..shared.text import format_log_tail, indented
from .models import SimulatorResult


def simulator_lines(vehicle: dict[str, Any]) -> list[str]:
    """The heading, frontend, error, and pid lines a simulator block prints.

    ``vehicles status`` uses these for a simulator. Lines that only one command
    knows stay with that command.
    """

    heading = f"{CHASE_SIM.heading}  {vehicle['state']} at {vehicle['url']}"
    lines = [heading]
    if vehicle.get("frontend") == "connected" and vehicle.get("game"):
        lines.append(f"  frontend connected, game {vehicle['game']}")
    elif vehicle.get("frontend"):
        lines.append(f"  frontend {vehicle['frontend']}")
    lines += indented(vehicle.get("error"))
    if vehicle.get("pid"):
        lines.append(f"  created by simulators create, pid {vehicle['pid']}")
    return lines


def host_lines(hosts: list[str]) -> list[str]:
    """The line naming the hosts created at a vehicle, if any. ``vehicles status`` uses it too."""

    if not hosts:
        return []
    return [f"  used by host {hosts[0]}" if len(hosts) == 1 else f"  used by hosts {', '.join(hosts)}"]


def render_simulators_status(status: dict[str, Any], *, json_output: bool, long: bool) -> str:
    """Text for a terminal, or ``automa_simulators_status`` JSON."""

    if json_output:
        return json.dumps(status, indent=2, sort_keys=True)
    simulators = status["simulators"]
    if not simulators:
        return "None created."
    return "\n\n".join(_status_block(simulator, long=long) for simulator in simulators)


def _status_block(simulator: dict[str, Any], *, long: bool) -> str:
    lines = [*simulator_lines(simulator), *host_lines(simulator["hosts"])]
    if long and simulator["state"] != "not found":
        lines.append(f"  Metrics UI checkout {simulator['metrics_ui_dir']}, config {simulator['config']}")
        lines.append(f"  log {simulator['log']}")
    if simulator["state"] == "exited":
        lines += indented(format_log_tail("Simulator", simulator["log"], simulator["log_tail"]))
    return "\n".join(lines)


def render_simulator_result(result: SimulatorResult) -> str:
    if result.kind == "error":
        return result.error or ""
    if result.kind == "create_error":
        return f"Cannot create a simulator: {result.error}"
    if result.kind == "created":
        return f"Created {CHASE_SIM.heading_at(result.url)}"
    if result.kind == "deleted":
        suffix = {"exited": ", which had exited", "killed": ", killed"}.get(result.ended, "")
        return f"Deleted {CHASE_SIM.heading_at(result.url)}{suffix}"
    if result.kind == "missing":
        names = ", ".join(result.urls)
        return f"No created simulator at {result.url}. " + (f"Created: {names}." if names else "None created.")
    if result.kind == "blocked":
        hosts = result.hosts
        subject = f"Host {hosts[0]} uses" if len(hosts) == 1 else f"Hosts {', '.join(hosts)} use"
        return f"{subject} the simulator at {chase_operator_url(result.url)}; delete {'it' if len(hosts) == 1 else 'them'} first."
    raise ValueError(f"Unknown simulator outcome: {result.kind}")
