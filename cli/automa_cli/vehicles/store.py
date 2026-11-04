"""Discovered-vehicle records that ``vehicles discover`` and ``remove`` keep.

Simulator records live with ``simulators``. This module reads and writes the
discovered-vehicle file and does not probe a vehicle.
"""

from __future__ import annotations

import json

from ..shared.io.json_files import write_json_atomically
from ..shared.paths import RUNTIME_ROOT


DISCOVERED = "discovered.json"
DISCOVERED_SCHEMA = "automa_discovered_vehicles"


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
