"""Collect each host's state, and its deployment, steps, run and view.

A worker's state and process records say how its run went; the host's
``/host/status`` reports the cycles it is running now. Each step's staged
selection is collected beside the one its host applied and how its runs went,
so either can be missing or invalid without hiding the other. ``render``
turns that data into text or JSON.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ...frontend.perception_view import get_perception_view_status
from ...providers.models import DEFAULT_READINESS_TIMEOUT_S
from ...releases.errors import ReleaseError
from ...releases.paths import controller_bundle_paths
from ...releases.store import release_manifest
from ...shared.io.json_files import read_json
from ...shared.io.processes import pid_alive
from ...shared.paths import display_path
from ...shared.timestamps import timestamp_ms
from .. import staging
from ..host_formatting import perception_stream_command
from ..host_status import read_hosts
from ..publications.host_publications import observation_component
from ..step_activations import (
    bundle_activation_problems,
    decision_identity,
    staged_selections,
)
from ..views.view_discovery import runtime_view_dir
from .models import status_reason

# The run state fields status reports as the worker recorded them.
_STATE_FIELDS = (
    "run_id", "pipeline", "frames_dropped", "num_decisions", "recording", "pid", "control_source",
    "action_policy", "execution", "arming", "session", "control_application", "passive_capture",
    "passive_session", "readiness", "error", "error_code", "error_details", "exit_code", "stop_reason",
    "updated_at_ms",
)


def hosts_status(
    *,
    host_name: str | None = None,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
    include_runs: bool = False,
) -> tuple[int, dict[str, Any]]:
    """The exit and the ``automa_hosts_status`` payload: each host's state and attachment.

    ``include_runs`` adds its deployment, release, steps, run and view. JSON
    status includes that data, and plain text includes it with ``--long``.
    Each host's view is checked once, side by side with the others'. With
    ``host_name``, the exit is 1 when that host is missing, stopped, not
    answering, or reports an error. Without it, no host's state changes the
    exit.
    """

    reads = read_hosts(host_name=host_name, timeout_s=timeout_s)
    hosts = [host for host, _ in reads]
    failed = {"not found", "not running", "not answering", "error"}
    exit_code = 1 if host_name is not None and any(host["state"] in failed for host in hosts) else 0
    if include_runs:
        with ThreadPoolExecutor() as pool:
            runs = list(pool.map(_run_status, hosts, [read for _, read in reads]))
        for host, run in zip(hosts, runs):
            host["run"] = run
    return exit_code, {"schema": "automa_hosts_status", "host_name": host_name, "hosts": hosts}


def _run_status(host: dict[str, Any], read: dict[str, Any] | None) -> dict[str, Any] | None:
    """A host's deployment and steps, and its run as its monitor or, without one, its host reports it.

    ``read`` is the host's ``host_state`` that ``read_hosts`` collected. The
    staged steps are what a start deploys; unstaged ones run their built-ins
    or nothing. ``decision`` names the generation the run applies, else the
    staged one.
    """

    if host["provider"] is None:
        return None  # Missing or unreadable host configuration.
    name = host["host_name"]
    bundle = controller_bundle_paths(staging.RUNTIME_ROOT / name)
    automation_dir = Path(bundle["runtime_dir"]) / "automation"
    process = _dict(read_json(automation_dir / "process.json"))
    state = _dict(read_json(automation_dir / "state.json"))
    process_pid, state_pid = _int_or_none(process.get("pid")), _int_or_none(state.get("pid"))
    pid = process_pid if process_pid is not None else state_pid
    alive = pid is not None and pid_alive(pid)
    selections = staged_selections(bundle)
    if read is not None and not alive:
        run, applied = _host_run(name, host["url"], read, selections)
    else:
        run, applied = _worker_run(automation_dir, process, state, selections, pid=pid, state_pid=state_pid,
                                   alive=alive)
    try:
        decision = applied or decision_identity(bundle)
    except (OSError, TypeError, ValueError):
        decision = None  # Its activation problem says why.
    try:
        release = _release_summary(release_manifest(name))
    except ReleaseError:
        release = None
    problems = bundle_activation_problems(bundle, name)
    return {
        "host_name": name,
        "activation_problems": problems,
        "deployed": release is not None and not problems,
        "release": release,
        "bundle_root": display_path(Path(bundle["root_dir"])),
        "automation_runtime_exists": automation_dir.exists(),
        "automation_dir": display_path(automation_dir),
        "decision": {
            "activation": display_path(Path(bundle["runtime_dir"])),
            "generation_id": decision.get("generation_id") if isinstance(decision, dict) else None,
        },
        **run,
    }


def _worker_run(
    automation_dir: Path,
    process: dict[str, Any],
    state: dict[str, Any],
    selections: dict[str, dict[str, Any]],
    *,
    pid: int | None,
    state_pid: int | None,
    alive: bool,
) -> tuple[dict[str, Any], Any]:
    """A run as its monitor's records report it, and the decision it applies.

    ``pid`` is the process record's, else the state's. The view is the one
    the monitor serves; ``hosts start`` waits for it, so only a run still
    starting reports it warming.
    """

    status, reason = _worker_state(state, pid=pid, state_pid=state_pid, alive=alive)
    run_id = state.get("run_id") if isinstance(state.get("run_id"), str) else None
    last_frame = _dict(state.get("last_frame"))
    run = {
        "process": {
            "pid": pid,
            "running": alive,
            "pid_state": "alive" if alive else ("not_running" if pid is not None else "none"),
            "status": status,
            "generation_matches": state_pid in (None, pid),
            "reason": reason,
            "log_to_disk": bool(process.get("log_to_disk")),
            "log_path": process.get("log_path"),
            "command": process.get("command"),
            "process_record": display_path(automation_dir / "process.json"),
        },
        "state": {
            **{field: state.get(field) for field in _STATE_FIELDS},
            "status": state.get("status", "none"),
            "frames_received": state.get("frames_received", 0),
            "processed_count": state.get("processed_count", 0),
            "last_capture": _dict(state.get("last_capture")),
            "last_frame": last_frame,
            "latest_perception_text": display_path(automation_dir / "latest_perception.txt"),
            "state_record": display_path(automation_dir / "state.json"),
            "latest_perception_age_ms": _age_ms(_int_or_none(last_frame.get("perception_completed_at_ms"))),
        },
        "published_view": get_perception_view_status(
            automation_dir, expected_run_id=run_id, expected_worker_pid=pid,
        ),
        **_compared_steps(
            selections, state.get("steps") if alive else None,
            unreported="the run has not reported its steps" if alive else "host not running",
        ),
    }
    return run, state.get("decision") if alive else None


def _host_run(
    host_name: str, base_url: str, read: dict[str, Any], selections: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], Any]:
    """A run as its host's ``/host/status`` reports it, and the decision it applied.

    Without a monitor, its host reports the same cycle counters from its
    observation status provider. No run view is served then, so the view is
    the one a terminal ``plugins stream`` hosts.
    """

    endpoint = f"{base_url}/host/status"
    autonomy = _dict((read["reply"] or {}).get("host"))
    observation = observation_component(autonomy)
    latest = _dict(observation.get("latest"))
    error = read["error"] if read["state"] in {"not answering", "error"} else None
    status = "error" if error is not None else read["state"]
    view = get_perception_view_status(runtime_view_dir(host_name, runtime_root=staging.RUNTIME_ROOT))
    if view.get("url") is None:  # No view is recorded here.
        view["reason"] = f"start `{perception_stream_command(host_name)}` to host it"
    run = {
        "process": {
            "pid": None,
            "running": status == "running",
            "pid_state": f"host {base_url}",
            "status": status,
            "generation_matches": True,
            "reason": error,
            "log_to_disk": False,
            "log_path": None,
            "command": None,
            "process_record": endpoint,
        },
        "state": {
            "status": status,
            "run_id": None,
            "frames_received": observation.get("frames_received", 0),
            "frames_dropped": observation.get("frames_dropped"),
            "processed_count": observation.get("processed_count", 0),
            "num_decisions": _dict(_dict(autonomy.get("session")).get("configuration")).get("num_decisions"),
            "recording": False,
            "control_source": "runtime_host",
            "action_policy": _dict(autonomy.get("execution")).get("mode"),
            "execution": autonomy.get("execution"),
            "session": autonomy.get("session"),
            "error": error,
            "last_frame": {
                "frame_id": latest.get("frame_id"),
                "perception_completed_at_ms": read["last_frame_at_ms"],
                "cycle_duration_ms": latest.get("duration_ms"),
            } if latest else {},
            "latest_perception_text": endpoint,
            "state_record": endpoint,
            "latest_perception_age_ms": _age_ms(read["last_frame_at_ms"]),
        },
        "published_view": view,
        **_compared_steps(selections, autonomy.get("steps"), unreported=f"host {read['state']}"),
    }
    return run, autonomy.get("applied_decision")


def _compared_steps(
    selections: dict[str, dict[str, Any]], reports: Any, *, unreported: str,
) -> dict[str, Any]:
    """Each staged selection with the one its host applied, and whether the host reported them.

    Without the host's step reports every step's ``applied`` is ``None`` and
    ``applied_steps`` says why; an invalid staged step keeps its host's report.
    """

    available = isinstance(reports, dict) and any(step in reports for step in selections)
    return {
        "steps": {
            step: {**selection, "applied": _applied_step(reports.get(step)) if available else None}
            for step, selection in selections.items()
        },
        "applied_steps": {"available": available, "reason": None if available else unreported},
    }


def _applied_step(report: Any) -> dict[str, Any]:
    """A step as its host reports running it; a step it runs no runner for has no plugins.

    ``plugin_configs`` is ``None`` when the host does not report them. ``error``
    is set when the host could not read the step's report.
    """

    report = _dict(report)
    return {
        "preset": report.get("preset"),
        "plugins": list(report.get("plugin_ids") or []),
        "plugin_configs": report.get("plugin_configs") if isinstance(report.get("plugin_configs"), dict) else None,
        "run_count": _int_or_none(report.get("run_count")),
        "failure_count": _int_or_none(report.get("failure_count")),
        "last_error": report.get("last_error") if isinstance(report.get("last_error"), str) else None,
        "error": report.get("error") if report.get("status") == "error" else None,
    }


def _release_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    """The packaged release a start deploys: its source tree, archive and manifest."""

    archive = _dict(manifest.get("archive"))
    listed = _dict(manifest.get("manifest"))
    return {
        "tree_sha256": manifest.get("tree_sha256"),
        "created_at_ms": manifest.get("created_at_ms"),
        "archive_sha256": archive.get("sha256"),
        "archive": display_path(Path(archive["path"])) if isinstance(archive.get("path"), str) else None,
        "manifest": display_path(Path(listed["path"])) if isinstance(listed.get("path"), str) else None,
    }


def _worker_state(
    state: dict[str, Any], *, pid: int | None, state_pid: int | None, alive: bool,
) -> tuple[str, str | None]:
    """The worker's status from its records, and why when it is stale or failed."""

    if state_pid not in (None, pid):
        return "stale", f"process/state worker generation mismatch: process pid={pid}, state pid={state_pid}"
    run_status = state.get("status")
    if run_status == "error":
        return "error", status_reason(state.get("error"), fallback="automation worker reported an error")
    if alive:
        return ("starting" if run_status == "starting" else "running"), None
    if run_status in {"starting", "running"}:
        return "stale", f"recorded worker PID {pid if pid is not None else 'none'} is not running"
    if run_status in {"completed", "stopped"}:
        return run_status, None
    return ("not_running" if pid is not None else "not_started"), None


def _age_ms(completed_at_ms: int | None) -> int | None:
    return None if completed_at_ms is None else max(0, timestamp_ms() - completed_at_ms)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
