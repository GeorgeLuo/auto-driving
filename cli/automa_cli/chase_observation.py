"""What a Chase automation worker publishes, read from its runtime directory.

``vehicles automation run`` writes ``state.json`` and
``latest_perception.json`` under :func:`chase_automation_dir`. They are the
worker's counterparts of the PiCar's ``/autonomy/status`` and observation
publication, which ``picar_observation`` reads over HTTP. A worker is live
only while its process runs and its state is fresh.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from .bundles import controller_bundle_paths
from .paths import ROOT, safe_path_part


RUNTIME_ROOT = Path(os.environ.get("AUTOMA_RUNTIME_ROOT", ROOT / "runtime" / "vehicles"))

# A step stream treats a Chase worker whose state is older than this as stale.
CHASE_WORKER_PROBE_MAX_AGE_MS = int(
    os.environ.get("AUTOMA_CHASE_WORKER_PROBE_MAX_AGE_MS", "30000")
)
# Allow tiny forward clock skew before treating updated_at_ms as invalid.
CHASE_WORKER_PROBE_CLOCK_SKEW_MS = int(
    os.environ.get("AUTOMA_CHASE_WORKER_PROBE_CLOCK_SKEW_MS", "2000")
)


def chase_automation_dir(vehicle_id: str) -> Path:
    """The worker's runtime directory: its state, records and view."""

    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))
    return Path(bundle["runtime_dir"]) / "automation"


class ChaseStateError(Exception):
    """The worker's state could not be read; ``status`` is the probe status."""

    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status


def read_chase_state(vehicle_id: str) -> dict[str, Any]:
    """The worker's ``state.json``, as ``fetch_autonomy_status`` reads a PiCar's.

    Raises ChaseStateError when the worker has written none or it is unreadable.
    """

    state_path = chase_automation_dir(vehicle_id) / "state.json"
    if not state_path.exists():
        raise ChaseStateError(
            "unavailable",
            f"No automation runtime state for {vehicle_id!r}. "
            f"Run: ./cli/automa vehicles automation run --id {vehicle_id}",
        )
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ChaseStateError("error", f"Could not read automation state: {exc}") from exc
    if not isinstance(state, dict):
        raise ChaseStateError("error", "Automation state is not a JSON object.")
    return state


def read_chase_record(vehicle_id: str) -> dict[str, Any] | None:
    """The worker's latest cycle record (``latest_perception.json``), as
    ``fetch_observation_publication`` reads a PiCar's; None when there is none."""

    try:
        value = json.loads(
            (chase_automation_dir(vehicle_id) / "latest_perception.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _pid_matches_automation(pid: int, vehicle_id: str) -> bool:
    """Return whether *pid* looks like this vehicle's automation worker.

    When the process command cannot be read, returns True (stop path stays
    permissive). Callers that must fail closed should read the command first
    and use :func:`_automation_command_matches_vehicle` directly.
    """

    command = _process_command(pid)
    if command is None:
        return True
    return _automation_command_matches_vehicle(command, vehicle_id)


def _automation_command_matches_vehicle(command: str, vehicle_id: str) -> bool:
    """Pure check: command is an automation run for exactly *vehicle_id*.

    Requires the contiguous launcher subcommand ``vehicles automation run`` and
    an exact ``--id <vehicle_id>`` argument pair (token equality, not substring).
    """

    if not vehicle_id or not str(vehicle_id).strip():
        return False
    vehicle_key = str(vehicle_id).strip()
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens:
        return False
    # Contiguous launcher subcommand as emitted by start_automation.
    matched_run = False
    for index in range(len(tokens) - 2):
        if tokens[index : index + 3] == ["vehicles", "automation", "run"]:
            matched_run = True
            break
    if not matched_run:
        return False
    for index, token in enumerate(tokens):
        if token == "--id":
            if index + 1 < len(tokens) and tokens[index + 1] == vehicle_key:
                return True
        elif token.startswith("--id="):
            if token[len("--id=") :] == vehicle_key:
                return True
    return False


def _process_command(pid: int) -> str | None:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    command = result.stdout.strip()
    return command or None


def assess_chase_worker_liveness(
    *,
    state: dict[str, Any],
    probed_at_ms: int,
    step: str,
    max_age_ms: int = CHASE_WORKER_PROBE_MAX_AGE_MS,
    vehicle_id: str | None = None,
    clock_skew_ms: int = CHASE_WORKER_PROBE_CLOCK_SKEW_MS,
) -> dict[str, Any]:
    """Whether the Chase automation worker is live enough to report ``step``.

    Requires a running automation process and a fresh state publication.
    Stopped or stale workers must not be reported as live merely because a
    previous state.json still holds the step's last output. A live PID must
    also match the automation run identity for this vehicle; unavailable process
    identity fails closed (unlike stop-path permissive matching).

    ``updated_at_ms`` is the automation-wide state heartbeat refreshed by the
    capture loop. It proves worker publication freshness, not that ``step``
    itself just completed an update.
    """

    run_status = str(state.get("status") or "")
    pid = state.get("pid")
    if not isinstance(pid, int):
        pid = None
    pid_alive = _pid_alive(pid) if isinstance(pid, int) else False
    updated_at_ms = state.get("updated_at_ms")
    try:
        updated_at = int(updated_at_ms) if updated_at_ms is not None else None
    except (TypeError, ValueError):
        updated_at = None
    age_ms = (probed_at_ms - updated_at) if updated_at is not None else None

    if run_status not in {"running", "starting"}:
        return {
            "live": False,
            "status": "stopped",
            "error": (
                f"Automation worker is not running (status={run_status or 'unknown'}). "
                f"Start observe-only automation before probing live {step}."
            ),
            "pid": pid,
            "updated_at_ms": updated_at,
            "age_ms": age_ms,
        }
    if not pid_alive:
        return {
            "live": False,
            "status": "stale",
            "error": (
                f"Automation worker PID {pid} is not running "
                f"(state status={run_status}). Restart automation before probing live {step}."
            ),
            "pid": pid,
            "updated_at_ms": updated_at,
            "age_ms": age_ms,
        }
    # A live step requires verified automation identity — fail closed if unknown.
    if vehicle_id is None or not str(vehicle_id).strip():
        return {
            "live": False,
            "status": "stale",
            "error": (
                "vehicle_id is required to verify the automation worker identity "
                f"before trusting live {step}."
            ),
            "pid": pid,
            "updated_at_ms": updated_at,
            "age_ms": age_ms,
        }
    vehicle_key = str(vehicle_id).strip()
    if isinstance(pid, int):
        command = _process_command(pid)
        if command is None:
            return {
                "live": False,
                "status": "stale",
                "error": (
                    f"Could not read process command for PID {pid}; "
                    "cannot verify automation worker identity. "
                    f"Treating live {step} as unavailable."
                ),
                "pid": pid,
                "updated_at_ms": updated_at,
                "age_ms": age_ms,
            }
        if not _automation_command_matches_vehicle(command, vehicle_key):
            return {
                "live": False,
                "status": "stale",
                "error": (
                    f"PID {pid} is alive but is not the automation worker for "
                    f"{vehicle_key!r} (possible PID reuse). Restart automation."
                ),
                "pid": pid,
                "updated_at_ms": updated_at,
                "age_ms": age_ms,
            }
    if updated_at is None:
        return {
            "live": False,
            "status": "stale",
            "error": (
                "Automation state is missing updated_at_ms; cannot confirm a live publication."
            ),
            "pid": pid,
            "updated_at_ms": None,
            "age_ms": None,
        }
    # Future timestamps (beyond small clock skew) are not trustworthy freshness.
    if age_ms is not None and age_ms < -int(clock_skew_ms):
        return {
            "live": False,
            "status": "stale",
            "error": (
                f"Automation state updated_at_ms is in the future "
                f"(age_ms={age_ms}, skew_tolerance_ms={int(clock_skew_ms)}). "
                "Rejecting as invalid publication time."
            ),
            "pid": pid,
            "updated_at_ms": updated_at,
            "age_ms": age_ms,
        }
    if age_ms is not None and age_ms > int(max_age_ms):
        return {
            "live": False,
            "status": "stale",
            "error": (
                f"Automation state is stale (age_ms={age_ms}, max_age_ms={int(max_age_ms)}). "
                f"Worker may be hung; restart automation for a fresh {step} publication."
            ),
            "pid": pid,
            "updated_at_ms": updated_at,
            "age_ms": age_ms,
        }
    return {
        "live": True,
        "status": "live",
        "error": None,
        "pid": pid,
        "updated_at_ms": updated_at,
        "age_ms": max(0, age_ms) if age_ms is not None else None,
    }
