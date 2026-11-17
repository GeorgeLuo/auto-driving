"""Format host status and the live run transcript from supplied data.

Nothing here fetches a host, starts a viewer, or reads a run record.
The worker writes the lines shown while a run is in progress and receives
the closing result for the caller to print.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from ...shared.paths import display_path
from ...shared.text import automa_command
from ..host_formatting import host_block, perception_stream_command, viewer_command
from ..step_formatting import format_activation_problems, selection_label, step_lines
from .models import RestartResult, RunState, StartResult, StopResult, WorkerProcess, status_reason

from .models import AppliedStep, ComparedStep, HostRun, RunDecision, RunProcess, RunRelease, RunStatusState

# A run's host owns its wheel commands. The label is the words status and the
# live transcript print; the stored value stays ``runtime_host``.
_CONTROL_SOURCE_LABELS = {"runtime_host": "runtime host"}


def render_hosts_status(status: dict[str, object], *, json_output: bool, long: bool) -> str:
    """Text for a terminal, or ``automa_hosts_status`` JSON."""

    if json_output:
        return json.dumps(status, indent=2, sort_keys=True)
    return _format_hosts_status(status["hosts"], long=long)


def _format_hosts_status(hosts: list[dict[str, object]], *, long: bool) -> str:
    if not hosts:
        return "No hosts found"
    return "\n\n".join(
        "\n".join([host_block(host), *(_run_lines(host["run"]) if long else [])])
        for host in hosts
    )


def _run_lines(item: HostRun | None) -> list[str]:
    """A host's deployment, release, steps, worker, run and view, under its host block."""

    if item is None:
        return []
    decision, process, state = item["decision"], item["process"], item["state"]
    published_view, last_frame = item["published_view"], state["last_frame"]
    lines = [
        f"  deployment: {'deployed' if item['deployed'] else 'not deployed'}",
        f"  release: {_release_label(item['release'], item['host_name'])}",
        *_selection_lines(item),
        f"  decision: {_decision_label(decision)}",
        f"  worker: {_worker_label(process)}",
        f"  run: {_run_label(state)}",
        f"  latest: {_latest_status_label(state, last_frame)}",
        f"  view: {_published_view_label(published_view)}",
        f"  state: {state['state_record']}",
        f"  log: {_status_log_label(process)}",
    ]
    arming = state.get("arming")
    if arming is not None and arming.get("request_id"):
        request = f"request {arming['request_id']}"
        generation = arming.get("generation_id")
        if isinstance(generation, str) and generation:
            request = f"{request}, generation {generation}"
        lines.append(f"  arming: {arming['status']} ({request})")
        if arming.get("error"):
            lines.append(f"  problem: {arming['error']}")
    problems = item["activation_problems"]
    if problems:
        lines.extend(
            f"  {line}" for line in format_activation_problems(problems).splitlines()
        )
    reason = process["reason"]
    if reason:
        lines.append(f"  problem: {reason}")
    return lines


def _release_label(release: RunRelease | None, host_name: str) -> str:
    if release is None:
        return f"none (run `{automa_command('hosts', 'update', '--name', str(host_name))}` to package one)"
    fields = {"archive": release.get("archive"), "manifest": release.get("manifest")}
    return "  ".join([str(release.get("tree_sha256") or "unknown")[:12],
                      *(f"{name}={value}" for name, value in fields.items() if value)])


def _selection_lines(item: HostRun) -> list[str]:
    """Every step's staged selection and its source, over the selection its host applied."""

    selections, applied = item["steps"], item["applied_steps"]
    lines = [
        "  steps: staged, and applied by the running host"
        if applied.get("available")
        else f"  steps: staged; applied unavailable ({applied.get('reason') or 'unknown'})"
    ]
    for step, selection in selections.items():
        lines.append(f"    {step}: {_staged_label(selection)}")
        if selection["applied"] is not None:
            lines.append(
                f"      applied: {_applied_label(selection, selection['applied'])}"
            )
    return lines


def _staged_label(selection: ComparedStep) -> str:
    source = selection.get("source")
    if source == "invalid":
        return "unreadable  source=invalid"
    if source == "staged":
        source = f"staged ({'saved' if selection.get('saved') else 'default'})"
    return f"{selection_label(selection.get('preset'), selection.get('plugins') or [])}  source={source}"


def _applied_label(selection: ComparedStep, applied: AppliedStep) -> str:
    """What the host applied beside the staged selection; restaged configs wait for a restart."""

    if applied.get("error"):
        return f"unavailable ({status_reason(applied['error'], fallback='its report failed')})"
    configs, staged = (
        applied.get("plugin_configs"),
        selection.get("plugin_configs") or {},
    )
    if selection.get("source") == "invalid" or (
        (applied.get("preset"), applied.get("plugins"))
        != (selection.get("preset"), selection.get("plugins"))
    ):
        label = selection_label(applied.get("preset"), applied.get("plugins") or [])
    elif configs is None:
        label = "staged plugins, configs not reported"
    elif configs != staged:
        differ = [
            plugin
            for plugin in dict.fromkeys([*configs, *staged])
            if configs.get(plugin) != staged.get(plugin)
        ]
        label = f"staged plugins, configs differ ({', '.join(differ)})"
    else:
        label = "as staged"
    last_error = applied.get("last_error")
    fields = {
        "runs": applied.get("run_count"),
        "failures": applied.get("failure_count"),
        "last_error": status_reason(last_error, fallback="") if last_error else None,
    }
    return "  ".join([label, *(f"{name}={value}" for name, value in fields.items() if value is not None)])


def _decision_label(decision: RunDecision) -> str:
    return str(decision.get("generation_id") or f"not deployed; expected {decision.get('activation', 'unknown')}")


def _worker_label(process: RunProcess) -> str:
    pid = process.get("pid")
    pid_text = str(pid) if pid is not None else "none"
    return f"{process.get('status', 'unknown')}  pid={pid_text} ({process.get('pid_state', 'unknown')})"


def _run_label(state: RunStatusState) -> str:
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
    return "  ".join(
        f"{name}={value}" for name, value in fields.items() if value is not None
    )


def _latest_status_label(state: RunStatusState, last_frame: dict[str, object]) -> str:
    if not last_frame:
        return "none"
    fields = {
        "frame": last_frame.get("frame_id"),
        "cycle_ms": last_frame.get("cycle_duration_ms"),
        "age_ms": state.get("latest_perception_age_ms"),
    }
    return "  ".join(
        f"{name}={value}" for name, value in fields.items() if value is not None
    )


def _published_view_label(published_view: Mapping[str, object]) -> str:
    if published_view.get("available") and published_view.get("url"):
        return str(published_view["url"])
    return f"unavailable ({published_view.get('reason', 'automation view is not running')})"


def _status_log_label(process: RunProcess) -> str:
    if not process.get("log_to_disk"):
        return "disabled"
    return process.get("log_path") or "enabled"


def startup_lines(state: RunState) -> list[str]:
    selections = state.selections
    source = state.control_source
    return [
        f"Run started on host {state.host_name}.",
        *step_lines(
            {step: selection["plugins"] for step, selection in selections.items()},
            {step: selection["preset"] for step, selection in selections.items() if selection["preset"]},
        ),
        f"Recording: {state.run_dir}" if state.run_dir is not None else "Recording: off",
        f"Control source: {_CONTROL_SOURCE_LABELS.get(source, source)}",
        f"Action policy: {state.action_policy}",
        f"Decision generation: {state.decision['generation_id']}",
        f"View: {viewer_command(state.host_name)}",
        "Decisions: unbounded" if state.num_decisions is None else f"Decisions: {state.num_decisions}",
    ]


def render_run_result(state: RunState, *, state_path: str) -> str:
    """The closing lines for a finished run, with the caller's display path."""

    if state.status == "error":
        return (f"The run on host {state.host_name} failed.\n"
                f"Reason: {state.error}\nState: {state_path}")
    if state.status == "completed":
        dropped = "unavailable" if state.frames_dropped is None else state.frames_dropped
        recorded = f"Decisions recorded: {state.recorded_count}\n" if state.recording else ""
        return (f"Run completed on host {state.host_name}.\n"
                f"Frames received: {state.frames_received}\n"
                f"Frames dropped before decision: {dropped}\n"
                f"Decisions completed: {state.processed_count}\n"
                f"Control source: {state.control_source}\nAction policy: {state.action_policy}\n"
                f"Recording: {'on' if state.recording else 'off'}\n{recorded}State: {state_path}")
    return f"Run stopped on host {state.host_name}.\nState: {state_path}"


def _log_status_line(process: WorkerProcess | None, log_path: Path) -> str:
    record = process or WorkerProcess()
    configured_path = record.log_path
    log_to_disk = record.log_to_disk or configured_path is not None
    if not log_to_disk:
        return "Log: disabled; pass --log to persist worker output"
    if configured_path:
        return f"Log: {configured_path}"
    return f"Log: {display_path(log_path)}"


def render_start_result(result: StartResult) -> str:
    if result.problems:
        return format_activation_problems(result.problems)
    if result.kind == "error":
        return result.error
    host, pid = result.host_name, result.pid
    messages = {"starting": f"The run on host {host} is still starting.",
                "stopped": f"The run on host {host} was stopped before it was ready."}
    if result.kind in messages:
        return f"{messages[result.kind]}\nPID: {pid}\nState: {display_path(result.state_path)}"
    if result.kind == "session_not_running":
        return (f"Host {host} has a live worker, but {result.error}.\nPID: {pid}\nState: {display_path(result.state_path)}\n"
                f"Recovery: {automa_command('hosts', 'stop', '--name', host)}")
    if result.kind == "records_disagree":
        return (f"The run records for host {host} disagree.\n"
                f"Process PID: {pid}\nState PID: {result.state_pid}\n"
                "Reason: worker generation is stale; no duplicate worker was started.")
    if result.kind == "authority_mismatch":
        expected, actual = result.expected_authority, result.actual_authority
        return (f"Host {host} already has a run, but its authority does not match this command.\n"
                f"PID: {pid}\nExpected authority: action={expected['action_policy']}, "
                f"control={expected['control_application']}\n"
                f"Current authority: action={actual['action_policy'] or 'unknown'}, "
                f"control={actual['control_application'] or 'unknown'}\n"
                f"Recovery: {automa_command('hosts', 'stop', '--name', host)}")
    if result.kind == "existing":
        return (f"Host {host} already has a run.\nPID: {pid}\nView: {viewer_command(host)}\n"
                f"State: {display_path(result.state_path)}\n{_log_status_line(result.process_record, result.log_path)}\n"
                f"Stream: {perception_stream_command(host)}")
    if result.kind == "launch_failed":
        return f"Could not start a run on host {host}: {result.error}"
    if result.kind == "started":
        return _render_started_run(result)
    raise ValueError(f"Unknown start outcome: {result.kind}")


def _render_started_run(result: StartResult) -> str:
    host, pid, startup = result.host_name, result.pid, result.startup
    if startup["status"] == "ready":
        message = (f"Run started on host {host}.\nPID: {pid}\nSession: running\n"
                   f"Decisions: {startup.get('processed_count', 0)}\nView: {viewer_command(host)}\n"
                   f"Stop: {automa_command('hosts', 'stop', '--name', host)}")
    elif startup["status"] == "completed":
        message = f"The run on host {host} completed during startup verification.\nPID: {pid}"
    else:
        message = (f"The run on host {host} did not become ready.\nPID: {pid}\n"
                   f"Reason: {startup.get('reason', 'unknown startup failure')}")
    return (f"{message}\nState: {display_path(result.state_path)}\n"
            f"{_log_status_line(result.process_record, result.log_path)}\nStream: {perception_stream_command(host)}\n"
            f"Status: {automa_command('hosts', 'status', '--name', host, '--long')}")


def render_stop_result(result: StopResult) -> str:
    host, pid, error = result.host_name, result.pid, result.error
    if result.kind == "error":
        return error
    if result.kind == "remote_failed":
        return f"Could not stop {host}: {error}"
    if result.kind == "stale_pid":
        return (f"The recorded run PID for host {host} is alive but is not this host's run.\nPID: {pid}\n"
                "The PID record was marked stale; no process was terminated.\n"
                f"Process: {result.command or 'unknown'}")
    if result.kind == "release_failed":
        return (f"The Chase host for {host} did not answer and was killed; stopping the car failed: {error}\n"
                "Stop the chaser in the simulator before the next run.")
    messages = {
        "no_pid": f"No run PID is recorded for host {host}.",
        "already_dead": f"Host {host} has no run.\nRecorded PID: {pid}",
        "stopped": f"Stopped the run on host {host}.\nPID: {pid}",
        "forced": f"Force-stopped the run on host {host}.\nPID: {pid}",
        "timeout": f"The run on host {host} did not stop.\nPID: {pid}",
    }
    if result.kind in messages:
        return f"{messages[result.kind]}\nState: {display_path(result.state_path)}"
    raise ValueError(f"Unknown stop outcome: {result.kind}")


def render_lifecycle_result(result: StartResult | StopResult | RestartResult) -> str:
    if isinstance(result, StartResult):
        return render_start_result(result)
    if isinstance(result, StopResult):
        return render_stop_result(result)
    return _render_restart_result(result)


def _render_restart_result(result: RestartResult) -> str:
    if result.kind == "error":
        return format_activation_problems(result.problems) if result.problems else result.error
    if result.kind == "restart_failed":
        return f"Could not restart {result.host_name}: {result.error}"
    if result.kind in {"stop_failed", "start_failed", "started"}:
        return "\n\n".join(
            text
            for child in (result.stop, result.start)
            if child is not None and (text := render_lifecycle_result(child))
        )
    raise ValueError(f"Unknown restart outcome: {result.kind}")
