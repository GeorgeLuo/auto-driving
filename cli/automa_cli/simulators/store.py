"""Records of the simulators ``simulators create`` started.

``vehicles status`` reads these records while the process is still alive.
This module does not start or stop a simulator.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..shared.io.processes import pid_alive
from ..shared.paths import RUNTIME_ROOT


SIMULATORS = "simulators"
SIMULATOR_RECORD = "simulator.json"
SIMULATOR_SCHEMA = "automa_created_simulator"


def simulator_dir(port: int) -> Path:
    return RUNTIME_ROOT / SIMULATORS / str(port)


def created_simulators() -> list[dict[str, Any]]:
    """The record of each simulator ``simulators create`` started that is still running, by port."""

    records = []
    for path in (RUNTIME_ROOT / SIMULATORS).glob(f"*/{SIMULATOR_RECORD}"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and record.get("schema") == SIMULATOR_SCHEMA and pid_alive(record.get("pid")):
            records.append(record)
    return sorted(records, key=lambda record: record["port"])


def write_simulator_record(record: dict[str, Any]) -> None:
    path = simulator_dir(record["port"]) / SIMULATOR_RECORD
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def remove_simulator_record(record: dict[str, Any]) -> None:
    (simulator_dir(record["port"]) / SIMULATOR_RECORD).unlink(missing_ok=True)
