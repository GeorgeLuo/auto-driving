"""``automa hosts status``: each host's state, and its deployment, steps, run and view.

A worker's state and process records say how its run went; the host's
``/host/status`` reports the cycles it is running now. Each step's staged
selection is reported beside the one its host applied and how its runs went,
so either can be missing or invalid without hiding the other.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..shared.command_result import CommandResult
from .bundles import controller_bundle_paths
from ..shared.json_files import read_json
from ..shared.paths import display_path, safe_path_part
from ..shared.processes import pid_alive
from . import runtime_hosts
from .runtime_hosts import RuntimeHostError, running_base_url, update_command
from .host_publications import host_state
from .host_status import host_block, read_hosts
from .step_activations import (
    bundle_activation_problems,
    decision_identity,
    format_activation_problems,
    selection_label,
    staged_selections,
)
from .run_record import timestamp_ms
from .view_discovery import discover_runtime_view
from ..frontend.perception_view import get_perception_view_status
from ..vehicles.probes import DEFAULT_READINESS_TIMEOUT_S


MAX_STATUS_REASON_CHARS = 240



def _host_runtime_status(
    host_name: str,
    base_url: str,
    *,
    timeout_s: float,
    read: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A runtime host's ``/host/status`` in the worker status rows.

    While a monitor runs, its state record holds these rows; otherwise the
    host reports the same cycle counters from its observation status
    provider. ``read`` is the host's ``host_state`` when the caller already
    has it. The local view is the one a terminal ``plugins stream`` hosts.
    """

    endpoint = f"{base_url}/host/status"
    host = read or host_state(base_url, host_name=host_name, timeout_s=max(0.1, timeout_s))
    status = host["reply"] or {}
    error = host["error"] if host["state"] in {"not answering", "error"} else None
    worker_status = "error" if error is not None else host["state"]
    autonomy = status.get("host") if isinstance(status.get("host"), dict) else {}
    components = autonomy.get("components") if isinstance(autonomy.get("components"), dict) else {}
    observation = (
        components.get("observation") if isinstance(components.get("observation"), dict) else {}
    )
    latest = observation.get("latest") if isinstance(observation.get("latest"), dict) else {}
    completed_at_ms = _int_or_none(latest.get("completed_at_ms"))
    view = discover_runtime_view(
        host_name,
        lambda directory: get_perception_view_status(
            directory, timeout_s=min(0.25, max(0.0, timeout_s)),
        ),
        runtime_root=runtime_hosts.RUNTIME_ROOT,
    )
    if not view.get("available"):
        view = {
            **view,
            "reason": f"start `./cli/automa plugins stream --step perception --host-name {host_name}` to host it",
        }
    return {
        "process": {
            "pid": None,
            "running": worker_status == "running",
            "pid_state": f"host {base_url}",
            "status": worker_status,
            "generation_matches": True,
            "reason": error,
            "log_to_disk": False,
            "log_path": None,
            "command": None,
            "process_record": endpoint,
        },
        "state": {
            "status": worker_status,
            "run_id": None,
            "frames_received": observation.get("frames_received", 0),
            "frames_dropped": observation.get("frames_dropped"),
            "processed_count": observation.get("processed_count", 0),
            "num_decisions": (autonomy.get("session") or {}).get("configuration", {}).get(
                "num_decisions"
            ),
            "recording": False,
            "control_source": "runtime_host",
            "action_policy": (autonomy.get("execution") or {}).get("mode"),
            "execution": autonomy.get("execution"),
            "session": autonomy.get("session"),
            "error": error,
            "last_frame": (
                {
                    "frame_id": latest.get("frame_id"),
                    "perception_completed_at_ms": completed_at_ms,
                    "cycle_duration_ms": latest.get("duration_ms"),
                }
                if latest
                else {}
            ),
            "latest_perception_text": endpoint,
            "state_record": endpoint,
            "latest_perception_age_ms": (
                None if completed_at_ms is None else max(0, timestamp_ms() - completed_at_ms)
            ),
        },
        "published_view": view,
        "steps": autonomy.get("steps"),
        "applied_decision": autonomy.get("applied_decision"),
        "host_state": host["state"],
    }



def _decision_summary(identity: Any) -> dict[str, Any]:
    """Whether a decision identity is deployed, and its generation."""

    if not isinstance(identity, dict) or not isinstance(identity.get("steps"), dict):
        return {"deployed": False}
    return {"deployed": True, "generation_id": identity.get("generation_id")}



def get_hosts_status(
    *,
    host_name: str | None = None,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
    long: bool = False,
    json_output: bool = False,
) -> CommandResult:
    """Each host's state and attachment; ``long`` adds its deployment, release, steps, run and view.

    The JSON carries every host's run. With ``host_name``, exits 1 when that host
    is missing, stopped, not answering, or reports an error. Without it, no
    host's state changes the exit.
    """

    reads = read_hosts(host_name=host_name, timeout_s=timeout_s)
    hosts = [host for host, _ in reads]
    failed = {"not found", "not running", "not answering", "error"}
    exit_code = 1 if host_name is not None and any(host["state"] in failed for host in hosts) else 0
    if long or json_output:
        runs = {
            run["host_name"]: run
            for run in collect_automation_status(
                host_name=host_name,
                host_reads={host["host_name"]: read for host, read in reads if read is not None},
            )
        }
        for host in hosts:
            host["run"] = runs.get(host["host_name"])
    if json_output:
        payload = {"schema": "automa_hosts_status", "host_name": host_name, "hosts": hosts}
        return CommandResult(exit_code, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(exit_code, _format_hosts_status(hosts, long=long))



def collect_automation_status(
    *,
    host_name: str | None,
    view_timeout_s: float | None = None,
    host_reads: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    # ``host_reads`` holds each host's ``host_state`` the caller already read.
    if host_name is not None:
        candidate = runtime_hosts.RUNTIME_ROOT / safe_path_part(host_name)
        candidates = [candidate] if (candidate / "host-configuration.json").is_file() else []
    elif runtime_hosts.RUNTIME_ROOT.exists():
        candidates = sorted(path.parent for path in runtime_hosts.RUNTIME_ROOT.glob("*/host-configuration.json"))
    else:
        candidates = []

    # One wall-clock budget for every runtime's view probes and warm-up sleeps.
    # Aggregate status without --name must not restart the full timeout per card.
    if view_timeout_s is None:
        view_budget_s = 1.0
    else:
        view_budget_s = max(0.0, float(view_timeout_s))
    view_deadline = time.monotonic() + view_budget_s

    def _remaining_view_budget() -> float:
        return max(0.0, view_deadline - time.monotonic())

    def _exhausted_view_status() -> dict[str, Any]:
        return {
            "schema": "automa_perception_view",
            "available": False,
            "status": "unavailable",
            "url": None,
            "reason": (
                "command deadline was exhausted before current-generation "
                "view health could be checked"
            ),
        }

    statuses = []
    for host_dir in candidates:
        name = host_dir.name
        try:
            host_url = running_base_url(name)
        except RuntimeHostError:
            # The host's status says why its configuration cannot be read.
            continue
        bundle = controller_bundle_paths(host_dir)
        bundle_root = Path(bundle["root_dir"])
        automation_dir = Path(bundle["runtime_dir"]) / "automation"
        process_path = automation_dir / "process.json"
        state_path = automation_dir / "state.json"
        latest_perception_path = automation_dir / "latest_perception.txt"

        process = read_json(process_path)
        state = read_json(state_path)
        process = process if isinstance(process, dict) else {}
        state = state if isinstance(state, dict) else {}

        pid = process.get("pid") if isinstance(process.get("pid"), int) else state.get("pid")
        alive = pid_alive(pid) if isinstance(pid, int) else False
        state_pid = state.get("pid") if isinstance(state.get("pid"), int) else None
        process_pid = process.get("pid") if isinstance(process.get("pid"), int) else None
        generation_matches = (
            state_pid is None
            or process_pid is None
            or state_pid == process_pid
        )
        run_id = state.get("run_id") if isinstance(state.get("run_id"), str) else None
        remaining_view_budget = _remaining_view_budget()
        if remaining_view_budget <= 0:
            published_view = _exhausted_view_status()
        else:
            first_probe_timeout_s = min(0.25, remaining_view_budget)
            published_view = get_perception_view_status(
                automation_dir,
                timeout_s=first_probe_timeout_s,
                expected_run_id=run_id,
                expected_worker_pid=pid if isinstance(pid, int) else None,
            )
        if not generation_matches:
            worker_status = "stale"
            worker_reason = (
                "process/state worker generation mismatch: "
                f"process pid={process_pid}, state pid={state_pid}"
            )
        else:
            worker_status = _worker_status(
                pid=pid,
                pid_alive=alive,
                run_status=state.get("status"),
            )
            worker_reason = _worker_reason(
                worker_status=worker_status,
                pid=pid,
                state=state,
            )
            # The first capture is published before its perception result.
            # Status waits out that gap, within the command budget. A later
            # capture does not withdraw a completed result whose image remains.
            if (
                worker_status == "running"
                and alive
                and not published_view.get("available")
                and published_view.get("status") in {None, "warming", "unavailable"}
                and _remaining_view_budget() > 0
            ):
                while _remaining_view_budget() > 0:
                    sleep_s = min(0.1, _remaining_view_budget())
                    if sleep_s <= 0:
                        break
                    time.sleep(sleep_s)
                    probe_timeout_s = min(0.25, _remaining_view_budget())
                    if probe_timeout_s <= 0:
                        break
                    published_view = get_perception_view_status(
                        automation_dir,
                        timeout_s=probe_timeout_s,
                        expected_run_id=run_id,
                        expected_worker_pid=pid if isinstance(pid, int) else None,
                    )
                    if published_view.get("available"):
                        break
        activation_problems = bundle_activation_problems(bundle, name)
        last_frame = state.get("last_frame") if isinstance(state.get("last_frame"), dict) else {}
        completed_at_ms = _int_or_none(last_frame.get("perception_completed_at_ms"))
        generated_at_ms = timestamp_ms()

        # The staged steps are what a start deploys; unstaged ones run their
        # built-ins or nothing. A running monitor records the steps and
        # decision its host applied; otherwise the host reports them.
        selections = staged_selections(bundle)
        reports, unreported = (
            (state.get("steps"), "the run has not reported its steps") if alive else (None, "host not running")
        )
        try:
            identity = state.get("decision") if alive else None
            decision = _decision_summary(identity) if identity else _decision_summary(decision_identity(bundle))
        except (OSError, TypeError, ValueError):
            decision = {"deployed": False}
        try:
            release = _release_summary(runtime_hosts.release_manifest(name))
        except RuntimeHostError:
            release = None

        statuses.append(
            {
                "host_name": name,
                "activation_problems": activation_problems,
                "deployed": release is not None and decision["deployed"] and not activation_problems,
                "release": release,
                "bundle_root": display_path(bundle_root),
                "automation_runtime_exists": automation_dir.exists(),
                "automation_dir": display_path(automation_dir),
                "decision": {
                    "activation": display_path(Path(bundle["runtime_dir"])),
                    **decision,
                },
                "process": {
                    "pid": pid,
                    "running": alive,
                    "pid_state": "alive" if alive else ("not_running" if isinstance(pid, int) else "none"),
                    "status": worker_status,
                    "generation_matches": generation_matches,
                    "reason": worker_reason,
                    "log_to_disk": bool(process.get("log_to_disk")),
                    "log_path": process.get("log_path") if isinstance(process.get("log_path"), str) else None,
                    "command": process.get("command") if isinstance(process.get("command"), list) else None,
                    "process_record": display_path(process_path),
                },
                "state": {
                    "status": state.get("status", "none"),
                    "run_id": state.get("run_id"),
                    "pipeline": state.get("pipeline"),
                    "frames_received": state.get("frames_received", 0),
                    "frames_dropped": state.get("frames_dropped"),
                    "processed_count": state.get("processed_count", 0),
                    "num_decisions": state.get("num_decisions"),
                    "recording": state.get("recording"),
                    "pid": state.get("pid"),
                    "control_source": state.get("control_source"),
                    "action_policy": state.get("action_policy"),
                    "execution": state.get("execution"),
                    "arming": state.get("arming"),
                    "session": state.get("session"),
                    "control_application": state.get("control_application"),
                    "passive_capture": state.get("passive_capture")
                    if isinstance(state.get("passive_capture"), dict)
                    else None,
                    "passive_session": state.get("passive_session")
                    if isinstance(state.get("passive_session"), dict)
                    else None,
                    "readiness": state.get("readiness")
                    if isinstance(state.get("readiness"), dict)
                    else None,
                    "error": state.get("error") if isinstance(state.get("error"), str) else None,
                    "error_code": state.get("error_code")
                    if isinstance(state.get("error_code"), str)
                    else None,
                    "error_details": state.get("error_details")
                    if isinstance(state.get("error_details"), dict)
                    else None,
                    "exit_code": _int_or_none(state.get("exit_code")),
                    "stop_reason": state.get("stop_reason")
                    if isinstance(state.get("stop_reason"), str)
                    else None,
                    "updated_at_ms": state.get("updated_at_ms"),
                    "last_capture": state.get("last_capture")
                    if isinstance(state.get("last_capture"), dict)
                    else {},
                    "last_frame": last_frame,
                    "latest_perception_text": display_path(latest_perception_path),
                    "state_record": display_path(state_path),
                    "latest_perception_age_ms": None if completed_at_ms is None else max(0, generated_at_ms - completed_at_ms),
                },
                "published_view": published_view,
            }
        )
        if host_url is not None and not alive:
            host = _host_runtime_status(
                name, host_url, timeout_s=_remaining_view_budget() or 0.5,
                read=(host_reads or {}).get(name),
            )
            applied = host.pop("applied_decision")
            reports, unreported = host.pop("steps"), f"host {host.pop('host_state')}"
            statuses[-1].update(host)
            if applied:
                statuses[-1]["decision"].update(_decision_summary(applied))
        statuses[-1].update(_compared_steps(selections, reports, unreported=unreported))
    return statuses


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

    report = report if isinstance(report, dict) else {}
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

    archive = manifest.get("archive") if isinstance(manifest.get("archive"), dict) else {}
    listed = manifest.get("manifest") if isinstance(manifest.get("manifest"), dict) else {}
    return {
        "tree_sha256": manifest.get("tree_sha256"),
        "created_at_ms": manifest.get("created_at_ms"),
        "archive_sha256": archive.get("sha256"),
        "archive": display_path(Path(archive["path"])) if isinstance(archive.get("path"), str) else None,
        "manifest": display_path(Path(listed["path"])) if isinstance(listed.get("path"), str) else None,
    }



def _format_hosts_status(hosts: list[dict[str, Any]], *, long: bool) -> str:
    if not hosts:
        return "No hosts found"
    return "\n\n".join(
        "\n".join([host_block(host), *(_run_lines(host["run"]) if long else [])]) for host in hosts
    )



def _run_lines(item: dict[str, Any] | None) -> list[str]:
    """A host's deployment, release, steps, worker, run and view, under its host block."""

    if not isinstance(item, dict):
        return []
    decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
    process = item.get("process") if isinstance(item.get("process"), dict) else {}
    state = item.get("state") if isinstance(item.get("state"), dict) else {}
    published_view = item.get("published_view") if isinstance(item.get("published_view"), dict) else {}
    last_frame = state.get("last_frame") if isinstance(state.get("last_frame"), dict) else {}
    lines = [
        f"  deployment: {'deployed' if item.get('deployed') else 'not deployed'}",
        f"  release: {_release_label(item.get('release'), item.get('host_name'))}",
        *_selection_lines(item),
        f"  decision: {_decision_label(decision)}",
        f"  worker: {_worker_label(process, state)}",
        f"  run: {_run_label(state)}",
        f"  latest: {_latest_status_label(state, last_frame)}",
        f"  view: {_published_view_label(published_view)}",
        f"  state: {state.get('state_record', 'unknown')}",
        f"  log: {_status_log_label(process)}",
    ]
    arming = state.get("arming")
    if isinstance(arming, dict) and arming.get("request_id"):
        lines.append(f"  arming: {arming['status']} (request {arming['request_id']})")
        if arming.get("error"):
            lines.append(f"  problem: {arming['error']}")
    problems = item.get("activation_problems")
    if isinstance(problems, list) and problems:
        lines.extend(f"  {line}" for line in format_activation_problems(problems).splitlines())
    reason = process.get("reason")
    if isinstance(reason, str) and reason:
        lines.append(f"  problem: {reason}")
    return lines



def _release_label(release: Any, host_name: Any) -> str:
    if not isinstance(release, dict):
        return f"none (run `{update_command(str(host_name))}` to package one)"
    fields = {"archive": release.get("archive"), "manifest": release.get("manifest")}
    return "  ".join([
        str(release.get("tree_sha256") or "unknown")[:12],
        *(f"{name}={value}" for name, value in fields.items() if value),
    ])



def _selection_lines(item: dict[str, Any]) -> list[str]:
    """Every step's staged selection and its source, over the selection its host applied."""

    selections = item.get("steps")
    if not isinstance(selections, dict):
        return []
    applied = item.get("applied_steps") if isinstance(item.get("applied_steps"), dict) else {}
    lines = [
        "  steps: staged, and applied by the running host"
        if applied.get("available")
        else f"  steps: staged; applied unavailable ({applied.get('reason') or 'unknown'})"
    ]
    for step, selection in selections.items():
        lines.append(f"    {step}: {_staged_label(selection)}")
        if isinstance(selection.get("applied"), dict):
            lines.append(f"      applied: {_applied_label(selection, selection['applied'])}")
    return lines



def _staged_label(selection: dict[str, Any]) -> str:
    source = selection.get("source")
    if source == "invalid":
        return "unreadable  source=invalid"
    if source == "staged":
        source = f"staged ({'saved' if selection.get('saved') else 'default'})"
    return f"{selection_label(selection.get('preset'), selection.get('plugins') or [])}  source={source}"



def _applied_label(selection: dict[str, Any], applied: dict[str, Any]) -> str:
    """What the host applied beside the staged selection; restaged configs wait for a restart."""

    if applied.get("error"):
        return f"unavailable ({_status_reason(applied['error'], fallback='its report failed')})"
    configs, staged = applied.get("plugin_configs"), selection.get("plugin_configs") or {}
    if selection.get("source") == "invalid" or (
        (applied.get("preset"), applied.get("plugins")) != (selection.get("preset"), selection.get("plugins"))
    ):
        label = selection_label(applied.get("preset"), applied.get("plugins") or [])
    elif not isinstance(configs, dict):
        label = "staged plugins, configs not reported"
    elif configs != staged:
        differ = [plugin for plugin in dict.fromkeys([*configs, *staged]) if configs.get(plugin) != staged.get(plugin)]
        label = f"staged plugins, configs differ ({', '.join(differ)})"
    else:
        label = "as staged"
    last_error = applied.get("last_error")
    fields = {
        "runs": applied.get("run_count"),
        "failures": applied.get("failure_count"),
        "last_error": _status_reason(last_error, fallback="") if last_error else None,
    }
    return "  ".join([label, *(f"{name}={value}" for name, value in fields.items() if value is not None)])



def _decision_label(decision: dict[str, Any]) -> str:
    if not decision.get("deployed"):
        return f"not deployed; expected {decision.get('activation', 'unknown')}"
    return str(decision.get("generation_id") or "no generation")



def _worker_label(process: dict[str, Any], state: dict[str, Any]) -> str:
    pid = process.get("pid")
    if pid is None:
        pid = state.get("pid")
    pid_text = str(pid) if isinstance(pid, int) else "none"
    return f"{process.get('status', 'unknown')}  pid={pid_text} ({process.get('pid_state', 'unknown')})"



def _worker_status(*, pid: Any, pid_alive: bool, run_status: Any) -> str:
    if run_status == "error":
        return "error"
    if pid_alive:
        return "starting" if run_status in {"launching", "starting"} else "running"
    if run_status in {"launching", "starting", "running"}:
        return "stale"
    if run_status in {"completed", "stopped"}:
        return str(run_status)
    if isinstance(pid, int):
        return "not_running"
    return "not_started"



def _worker_reason(
    *,
    worker_status: str,
    pid: Any,
    state: dict[str, Any],
) -> str | None:
    if worker_status == "stale":
        pid_text = str(pid) if isinstance(pid, int) else "none"
        return f"recorded worker PID {pid_text} is not running"
    if worker_status == "error":
        return _status_reason(
            state.get("error"),
            fallback="automation worker reported an error",
        )
    return None



def _status_reason(value: Any, *, fallback: str) -> str:
    lines = (
        [line.strip() for line in value.splitlines() if line.strip()]
        if isinstance(value, str)
        else []
    )
    reason = lines[0] if lines else fallback
    if len(reason) <= MAX_STATUS_REASON_CHARS:
        return reason
    return f"{reason[:MAX_STATUS_REASON_CHARS]}..."



def _run_label(state: dict[str, Any]) -> str:
    if not state.get("run_id"):
        return "none"
    num_decisions = state.get("num_decisions")
    fields = {
        "id": state["run_id"],
        "frames_received": state.get("frames_received") or 0,
        "frames_dropped": state.get("frames_dropped"),
        "decisions": f"{state.get('processed_count') or 0}/{'unbounded' if num_decisions is None else num_decisions}",
        "recording": state.get("recording"),
        "control": state.get("control_source"),
        "action": state.get("action_policy"),
    }
    return "  ".join(f"{name}={value}" for name, value in fields.items() if value is not None)



def _latest_status_label(state: dict[str, Any], last_frame: dict[str, Any]) -> str:
    if not last_frame:
        return "none"
    fields = {
        "frame": last_frame.get("frame_id"),
        "signals": last_frame.get("signals"),
        "things": last_frame.get("things"),
        "perception_ms": last_frame.get("perception_duration_ms"),
        "cycle_ms": last_frame.get("cycle_duration_ms"),
        "age_ms": state.get("latest_perception_age_ms"),
    }
    return "  ".join(f"{name}={value}" for name, value in fields.items() if value is not None)



def _published_view_label(published_view: dict[str, Any]) -> str:
    if published_view.get("available") and published_view.get("url"):
        label = str(published_view["url"])
        lag = published_view.get("capture_lag")
        if isinstance(lag, int) and lag > 0:
            lag_ms = published_view.get("capture_lag_ms")
            if isinstance(lag_ms, int):
                return f"{label}  capture_lag={lag} ({lag_ms}ms)"
            return f"{label}  capture_lag={lag}"
        return label
    return f"unavailable ({published_view.get('reason', 'automation view is not running')})"



def _status_log_label(process: dict[str, Any]) -> str:
    if not process.get("log_to_disk"):
        return "disabled"
    return process.get("log_path") or "enabled"



def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
