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
    """The ``{provider, url}`` of each vehicle ``vehicles discover`` listed, oldest first."""

    path = RUNTIME_ROOT / DISCOVERED
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["vehicles"]


def write_discovered_vehicles(vehicles: list[dict[str, str]]) -> None:
    write_json_atomically(RUNTIME_ROOT / DISCOVERED, {"schema": DISCOVERED_SCHEMA, "vehicles": vehicles})
