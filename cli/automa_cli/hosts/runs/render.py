"""Format host status and the live run transcript from supplied data.

Nothing here fetches a host, starts a viewer, or reads a run record.
``RunOutput`` writes the lines shown while a run is in progress and returns
the closing result for the caller to print.
"""

from __future__ import annotations

import json
from typing import Any, TextIO

from ..host_status import host_block
from ..step_activations import format_activation_problems, selection_label, step_lines
from .models import status_reason


# A run's host owns its wheel commands. The label is the words status and the
# live transcript print; the stored value stays ``runtime_host``.
_CONTROL_SOURCE_LABELS = {"runtime_host": "runtime host"}


def render_hosts_status(
    hosts: list[dict[str, Any]],
    *,
    host_name: str | None,
    json_output: bool,
    long: bool,
) -> str:
    """Text for a terminal, or ``automa_hosts_status`` JSON."""

    if json_output:
        payload = {"schema": "automa_hosts_status", "host_name": host_name, "hosts": hosts}
        return json.dumps(payload, indent=2, sort_keys=True)
    return _format_hosts_status(hosts, long=long)


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
        # The same command ``releases.store.update_command`` prints. Rendering
        # stays here so this module does not import the release store.
        return f"none (run `./cli/automa hosts update --name {host_name}` to package one)"
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
        return f"unavailable ({status_reason(applied['error'], fallback='its report failed')})"
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
        "last_error": status_reason(last_error, fallback="") if last_error else None,
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


def startup_lines(state: dict[str, Any]) -> list[str]:
    selections = state["selections"]
    view = state["published_view"]
    source = state["control_source"]
    return [
        f"Run started on host {state['host_name']}.",
        *step_lines(
            {step: selection["plugins"] for step, selection in selections.items()},
            {step: selection["preset"] for step, selection in selections.items() if selection["preset"]},
        ),
        (
            f"Recording: {state['run_dir']}"
            if state["run_dir"] is not None
            else "Recording: off; latest frame and perception are overwritten each iteration"
        ),
        f"Control source: {_CONTROL_SOURCE_LABELS.get(source, source)}",
        f"Action policy: {state['action_policy']}",
        f"Decision generation: {state['decision']['generation_id']}",
        (
            f"Runtime view: {view.get('url')}"
            if view.get("available")
            else f"Runtime view: unavailable ({view.get('reason', 'startup failed')})"
        ),
        "Decisions: unbounded" if state["num_decisions"] is None else f"Decisions: {state['num_decisions']}",
    ]


def reports_frame(count: int, *, verbose: bool) -> bool:
    return verbose or count == 1 or count % 10 == 0


def frame_line(frame_id: str, *, signals: int, things: int, action: Any) -> str:
    return f"{frame_id}: signals={signals} things={things} action={action}"


def run_result(state: dict[str, Any], *, state_path: str) -> tuple[int, str]:
    """The exit code and closing lines for a finished run.

    ``state_path`` is the path text the caller already prepared for display.
    """

    host_name = state["host_name"]
    status = state["status"]
    if status == "error":
        return 2, "\n".join(
            [
                f"The run on host {host_name} failed.",
                f"Reason: {state['error']}",
                f"State: {state_path}",
            ]
        )
    if status == "completed":
        dropped = state.get("frames_dropped")
        dropped = "unavailable" if dropped is None else dropped
        return 0, "\n".join(
            [
                f"Run completed on host {host_name}.",
                f"Frames received: {state.get('frames_received', 0)}",
                f"Frames dropped before decision: {dropped}",
                f"Decisions completed: {state['processed_count']}",
                f"Control source: {state['control_source']}",
                f"Action policy: {state['action_policy']}",
                f"Recording: {'on' if state['recording'] else 'off'}",
                *([f"Decisions recorded: {state['recorded_count']}"] if state["recording"] else []),
                f"State: {state_path}",
                f"Latest perception: {state['latest']['perception_text']}",
            ]
        )
    code = 130 if state.get("stop_reason") == "keyboard_interrupt" else 0
    return code, "\n".join(
        [
            f"Run stopped on host {host_name}.",
            f"State: {state_path}",
        ]
    )


class RunOutput:
    """The transcript a run monitor updates. Live lines go to ``output``; the closing result does not."""

    def __init__(self, output: TextIO | None = None) -> None:
        self._output = output

    def startup(self, state: dict[str, Any]) -> None:
        self._write(startup_lines(state))

    def frame(
        self, frame_id: str, *, signals: int, things: int, action: Any, count: int, verbose: bool,
    ) -> None:
        if reports_frame(count, verbose=verbose):
            self._write([frame_line(frame_id, signals=signals, things=things, action=action)])

    def result(self, state: dict[str, Any], *, state_path: str) -> tuple[int, str]:
        return run_result(state, state_path=state_path)

    def _write(self, lines: list[str]) -> None:
        if self._output is None:
            return
        for line in lines:
            print(line, file=self._output, flush=True)
