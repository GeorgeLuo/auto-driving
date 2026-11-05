"""Records that ``vehicles discover`` and ``remove`` keep.

Simulator records live with ``simulators``. This module reads and writes the
discovered-vehicle file and the marks for simulators ``vehicles remove``
stopped listing. It does not probe a vehicle.
"""

from __future__ import annotations

import json
from typing import Any

from ..shared.io.json_files import write_json_atomically
from ..shared.paths import RUNTIME_ROOT


DISCOVERED = "discovered.json"
DISCOVERED_SCHEMA = "automa_discovered_vehicles"
# A simulator simulators create started, which vehicles remove stopped listing.
UNTRACKED = "untracked.json"
UNTRACKED_SCHEMA = "automa_untracked_simulators"


def discovered_vehicles() -> list[dict[str, str]]:
    """The ``{provider, url}`` of each vehicle ``vehicles discover`` listed, oldest first.

    Like the other records, an unreadable list or entry is skipped rather than failing the command.
    """

    try:
        document = json.loads((RUNTIME_ROOT / DISCOVERED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    vehicles = document.get("vehicles") if isinstance(document, dict) else None
    return [
        vehicle for vehicle in (vehicles if isinstance(vehicles, list) else [])
        if isinstance(vehicle, dict) and isinstance(vehicle.get("provider"), str) and isinstance(vehicle.get("url"), str)
    ]


def write_discovered_vehicles(vehicles: list[dict[str, str]]) -> None:
    write_json_atomically(RUNTIME_ROOT / DISCOVERED, {"schema": DISCOVERED_SCHEMA, "vehicles": vehicles})


def untracked_simulators() -> list[dict[str, Any]]:
    """Simulators ``vehicles remove`` stopped listing, oldest first.

    A mark is that simulator's ``url``, ``pid`` and, when its record has one,
    ``started_at_ms``. An unreadable list or entry is skipped rather than
    failing the command. A mark for a record that is gone does not match the
    next simulator on that port.
    """

    try:
        document = json.loads((RUNTIME_ROOT / UNTRACKED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    simulators = document.get("simulators") if isinstance(document, dict) else None
    marks = []
    for simulator in simulators if isinstance(simulators, list) else []:
        mark = _untracked_mark(simulator)
        if mark is not None:
            marks.append(mark)
    return marks


def write_untracked_simulators(simulators: list[dict[str, Any]]) -> None:
    write_json_atomically(RUNTIME_ROOT / UNTRACKED, {"schema": UNTRACKED_SCHEMA, "simulators": simulators})


def _untracked_mark(simulator: object) -> dict[str, Any] | None:
    if not isinstance(simulator, dict):
        return None
    url, pid = simulator.get("url"), simulator.get("pid")
    if not isinstance(url, str) or type(pid) is not int:
        return None
    mark: dict[str, Any] = {"url": url, "pid": pid}
    if "started_at_ms" not in simulator:
        return mark
    started = simulator.get("started_at_ms")
    if type(started) is not int:
        return None
    mark["started_at_ms"] = started
    return mark
