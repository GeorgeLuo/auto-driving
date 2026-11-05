"""Records of the simulators ``simulators create`` started.

``simulator_records`` reads every record, which ``simulators status`` and
``simulators delete`` use. ``created_simulators`` keeps the ones whose
process is still alive, which ``vehicles`` uses. ``vehicles remove`` sets a
record's ``listed`` to false to stop listing it, and ``vehicles discover``
sets it back; a record without it is listed. This module does not start or
stop a simulator.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..shared.io.processes import pid_alive
from ..shared.paths import RUNTIME_ROOT, SIMULATORS_DIR


SIMULATOR_RECORD = "simulator.json"
SIMULATOR_SCHEMA = "automa_created_simulator"


def simulator_dir(port: int) -> Path:
    return RUNTIME_ROOT / SIMULATORS_DIR / str(port)


def simulator_records() -> list[dict[str, Any]]:
    """Every record ``simulators create`` wrote, including one whose process has exited, by port."""

    records = []
    for path in (RUNTIME_ROOT / SIMULATORS_DIR).glob(f"*/{SIMULATOR_RECORD}"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and record.get("schema") == SIMULATOR_SCHEMA:
            records.append(record)
    return sorted(records, key=lambda record: record["port"])


def created_simulators() -> list[dict[str, Any]]:
    """Those records whose process is still alive, by port."""

    return [record for record in simulator_records() if pid_alive(record.get("pid"))]


def write_simulator_record(record: dict[str, Any]) -> None:
    path = simulator_dir(record["port"]) / SIMULATOR_RECORD
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def remove_simulator_record(record: dict[str, Any]) -> None:
    (simulator_dir(record["port"]) / SIMULATOR_RECORD).unlink(missing_ok=True)
