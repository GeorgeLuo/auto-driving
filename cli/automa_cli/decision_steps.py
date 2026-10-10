"""Proposal, plan, and action: each decision step's info, stream, and inspect.

Like ``perception`` and ``memory``, each decision step has an info command over
its staged contract and live runner, a stream of its record from the latest
cycle the vehicle published, and an offline inspect that replays a recording
or images through the cycle and reports the step for every frame. The three
steps share this module because one cycle publication and one replay hold all
of their records; ``decision`` validates that publication.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, TextIO

from autonomy.decision_cycle.action.hold import HOLD_IDLE_REASON, HoldAction, idle_output
from autonomy.decision_cycle.action.selected import SelectedAction
from autonomy.decision_cycle.activation import DECISION_STEPS
from implementations.decision_cycle.action.mode.plugin import LIVE_MODES, ModeAction
from implementations.decision_cycle.catalog import packaged_activation

from .decision import (
    CommandResult,
    DecisionSurfaceError,
    _error_result,
    _require_valid_activations,
    accept_decision_publication,
    decision_error_payload,
)
from .paths import ROOT, display_path
from .host_publications import fetch_decision_publication
from .runtime_hosts import RuntimeHostError, bundle_paths, runtime_base_url
from .step_activations import decision_identity
from .step_replay import StepReplay, inspect_run_id, read_replay_source, record_inspect_run
from .step_schema import format_staged_step, staged_step_info
from .workbench_source import WORKBENCH_DEFAULT_MAX_FRAMES, SourceValidationError

# Each decision step replays every step before it, from perception on.
_REPLAY_STEPS = ("perception", "observation", "memory", *DECISION_STEPS)
_RETAINED_SHOWN = 12


def inspect_root(step: str) -> Path:
    return Path(os.environ.get(
        f"AUTOMA_{step.upper()}_INSPECT_ROOT", ROOT / "runtime" / f"{step}-inspections",
    ))


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------


def get_vehicle_step_info(
    step: str,
    *,
    vehicle_id: str,
    json_output: bool = False,
    include_live: bool = True,
    timeout_s: float = 3.0,
) -> CommandResult:
    """The step's staged plugins and schema, the decision it belongs to, and its live runner."""

    bundle = bundle_paths(vehicle_id)
    staged, error = staged_step_info(bundle, vehicle_id, step)
    if error is not None:
        return CommandResult(2, error)
    try:
        _require_valid_activations(bundle, vehicle_id=vehicle_id, steps=DECISION_STEPS)
    except DecisionSurfaceError as exc:
        return CommandResult(exc.exit_code, exc.message_text)
    identity = decision_identity(bundle)
    plugins = {
        name: list((identity["steps"].get(name) or {}).get("plugins") or [])
        for name in DECISION_STEPS
    }
    payload: dict[str, Any] = {
        "schema": f"vehicle_{step}_info_v1",
        "vehicle_id": vehicle_id,
        **staged,
        "decision": {
            "generation_id": identity["generation_id"],
            "plugins": plugins,
            "authority": _action_authority_description(
                plugins["action"][0] if plugins["action"] else None
            ),
        },
        "live": None,
    }
    if include_live:
        from .streaming import probe_live_step

        payload["live"] = probe_live_step(step, vehicle_id=vehicle_id, timeout_s=timeout_s)
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(0, _format_step_info(step, payload))


def _action_authority_description(plugin_id: str | None) -> dict[str, Any]:
    """How the staged action plugin authorizes control, for operator-facing summaries."""

    if plugin_id == HoldAction.plugin_id:
        return {
            "gate_id": HoldAction.plugin_id,
            "proposed_applied": False,
            "authorized_idle_reason": HOLD_IDLE_REASON,
        }
    if plugin_id == SelectedAction.plugin_id:
        return {
            "gate_id": SelectedAction.plugin_id,
            "proposed_applied": "when a plan candidate is selected",
            "authorized_idle_reason": None,
        }
    if plugin_id == ModeAction.plugin_id:
        return {
            "gate_id": ModeAction.plugin_id,
            "proposed_applied": f"in {'/'.join(sorted(LIVE_MODES))} drive modes",
            "authorized_idle_reason": None,
        }
    return {"gate_id": plugin_id, "proposed_applied": None, "authorized_idle_reason": None}


def _format_step_info(step: str, payload: dict[str, Any]) -> str:
    decision = payload["decision"]
    authority = decision["authority"]
    lines = [
        *format_staged_step(step, payload),
        "",
        f"Decision: generation={decision['generation_id']}",
        *(
            f"- {name}: {', '.join(plugins) or 'none'}"
            for name, plugins in decision["plugins"].items()
        ),
        (
            f"Authority: gate={authority.get('gate_id')} "
            f"proposed_applied={authority.get('proposed_applied')} "
            f"idle_reason={authority.get('authorized_idle_reason')}"
        ),
    ]
    live = payload.get("live")
    if isinstance(live, dict):
        from .streaming import format_live_step_screen

        lines.extend(["", format_live_step_screen(step, vehicle_id=payload["vehicle_id"], live=live)])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# stream
# ---------------------------------------------------------------------------


def stream_vehicle_step(
    step: str,
    *,
    vehicle_id: str,
    refresh_s: float = 0.5,
    once: bool = False,
    no_clear: bool = False,
    json_output: bool = False,
    output: TextIO | None = None,
    timeout_s: float = 3.0,
) -> CommandResult:
    """Poll the step's record in the latest cycle the vehicle's runtime host published."""

    timeout_s = max(0.1, float(timeout_s))
    try:
        # Broken staging is the first thing to fix; it names its restage command.
        _require_valid_activations(bundle_paths(vehicle_id), vehicle_id=vehicle_id, steps=DECISION_STEPS)
        base_url = runtime_base_url(vehicle_id)
    except DecisionSurfaceError as exc:
        return _error_result(exc, json_output=json_output)
    except RuntimeHostError as exc:
        return _error_result(DecisionSurfaceError(
            "runtime_unavailable", str(exc), vehicle_id=vehicle_id, details={"reason": "missing"},
        ), json_output=json_output)

    def accept() -> dict[str, Any]:
        try:
            publication = fetch_decision_publication(base_url, timeout_s=timeout_s)
        except (ConnectionError, OSError) as exc:
            raise DecisionSurfaceError(
                "runtime_decision_unavailable",
                f"Could not read the decision publication at {base_url}: {exc}",
                vehicle_id=vehicle_id,
                details={"reason": "missing", "transport": str(exc)},
            ) from exc
        normalized = accept_decision_publication(
            publication, vehicle_id=vehicle_id, now_ms=int(time.time() * 1000),
        )
        report = normalized["decision"]
        activation = report["values"]["activation"]
        return {
            "schema": f"vehicle_{step}_stream_v1",
            **{key: report.get(key) for key in (
                "vehicle_id", "run_id", "generation_id", "frame_id", "frame_index", "timestamp_ms",
            )},
            # The host computes the result age on its own clock.
            "freshness": {"age_ms": normalized["result_age_ms"], "max_age_ms": normalized["max_age_ms"]},
            "plugins": list((activation["steps"].get(step) or {}).get("plugins") or []),
            "record": report["cycle"][step],
            **({"application": report["application"]} if step == "action" else {}),
        }

    return _poll(
        vehicle_id=vehicle_id,
        accept=accept,
        render=lambda frame: _format_stream_frame(step, frame),
        refresh_s=refresh_s,
        once=once,
        no_clear=no_clear,
        json_output=json_output,
        output=output,
    )


def _poll(
    *,
    vehicle_id: str,
    accept: Callable[[], dict[str, Any]],
    render: Callable[[dict[str, Any]], str],
    refresh_s: float,
    once: bool,
    no_clear: bool,
    json_output: bool,
    output: TextIO | None,
) -> CommandResult:
    """``accept`` returns one stream frame or raises DecisionSurfaceError,
    which the loop prints and keeps polling past; ``--once`` exits with it."""

    if once:
        try:
            accepted = accept()
        except DecisionSurfaceError as exc:
            return _error_result(exc, json_output=json_output)
        if json_output:
            return CommandResult(0, json.dumps(accepted, indent=2, sort_keys=True))
        return CommandResult(0, render(accepted))

    last_error: str | None = None
    try:
        while True:
            try:
                accepted = accept()
                last_error = None
                text = json.dumps(accepted, sort_keys=True) if json_output else render(accepted)
            except DecisionSurfaceError as exc:
                last_error = exc.message_text
                text = json.dumps(decision_error_payload(
                    error=exc.error, message=exc.message_text, vehicle_id=vehicle_id, details=exc.details,
                ), sort_keys=True) if json_output else last_error
            if output is not None:
                if not no_clear and not json_output:
                    output.write("\033[2J\033[H")
                output.write(text + "\n")
                output.flush()
            time.sleep(max(0.05, float(refresh_s)))
    except KeyboardInterrupt:
        return CommandResult(130, last_error or "")


def _format_stream_frame(step: str, frame: dict[str, Any]) -> str:
    freshness = frame["freshness"]
    lines = [
        f"{step.capitalize()} stream: {frame['vehicle_id']} frame={frame['frame_id']} "
        f"run={frame['run_id']} age_ms={freshness['age_ms']} max_age_ms={freshness['max_age_ms']}",
        f"Generation: {frame['generation_id']}",
        f"Plugins: {', '.join(frame['plugins']) or 'none'}",
        *format_step_record(step, frame["record"]),
    ]
    if step == "action":
        application = frame["application"]
        lines.append(
            "Host application: "
            f"applied={str(application.get('applied') is True).lower()} "
            f"mode={application.get('mode')} reason={application.get('reason')} "
            f"steering={application.get('steering')} throttle={application.get('throttle')}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# One step record as text; the stream and inspect both print it.
# ---------------------------------------------------------------------------


def format_step_record(step: str, record: dict[str, Any] | None) -> list[str]:
    if record is None:
        return [f"{step.capitalize()}: none (an earlier step failed or {step} is disabled)"]
    if step == "proposal":
        return _proposal_lines(record)
    if step == "plan":
        return _plan_lines(record)
    return _action_lines(record)


def _proposal_lines(record: dict[str, Any]) -> list[str]:
    source = record.get("source") if isinstance(record.get("source"), dict) else None
    observation = _observation_summary(source)
    memory = _memory_summary(source)
    lines = [
        f"Proposal: status={record.get('status')} reason={record.get('reason') or '-'}",
        f"Observation: status={observation['status']} frame_id={observation['frame_id']} "
        f"reason={observation['reason'] or '-'}",
        f"Memory: status={memory['status']} health={memory['health']} records={memory['record_count']}",
    ]
    lines.extend(
        f"Retained: record_id={item['record_id']} kind={item['kind']} "
        f"confidence={item['confidence']} frame_id={item['frame_id']}"
        for item in memory["records"]
    )
    lines.extend(_candidate_lines(record.get("candidates"), selected=None))
    return lines


def _plan_lines(record: dict[str, Any]) -> list[str]:
    selected = record.get("selected_proposal_id")
    contributions = record.get("contributions") if isinstance(record.get("contributions"), list) else []
    return [
        f"Plan: status={record.get('status')} selector={record.get('selector_id')} selected={selected}",
        *_candidate_lines(record.get("candidates"), selected=selected),
        "Contributions: " + (", ".join(
            f"{item.get('plugin_id')}(weight={item.get('weight')}, role={item.get('role')})"
            for item in contributions if isinstance(item, dict)
        ) or "none"),
    ]


def _action_lines(record: dict[str, Any]) -> list[str]:
    authority = record.get("authority") if isinstance(record.get("authority"), dict) else {}
    return [
        f"Action: status={record.get('status')} reason={record.get('reason') or '-'} "
        f"gate={authority.get('gate_id') or HoldAction.plugin_id}",
        f"Proposed: {json.dumps(authority.get('proposed'), sort_keys=True)}",
        f"Authorized: {json.dumps(authority.get('authorized_output') or idle_output(), sort_keys=True)}",
        # The action step passed the proposal through; the host decides whether it reaches the vehicle.
        f"Authorized as proposed: {str(authority.get('proposed_applied') is True).lower()}",
    ]


def _candidate_lines(candidates: Any, *, selected: str | None) -> list[str]:
    candidates = [item for item in (candidates if isinstance(candidates, list) else []) if isinstance(item, dict)]
    if not candidates:
        return ["Candidates: none"]
    return [
        ("* " if selected is not None and item.get("proposal_id") == selected else "- ")
        + f"plugin={item.get('plugin_id')} proposal={item.get('proposal_id')} "
        f"lifecycle={item.get('lifecycle')} freshness={item.get('freshness')} "
        f"confidence={item.get('confidence')} reason={item.get('reason')} "
        f"command={json.dumps(_command(item.get('command')), sort_keys=True)}"
        for item in candidates
    ]


def _command(command: Any) -> dict[str, Any] | None:
    if not isinstance(command, dict):
        return None
    return {"steering": command.get("steering"), "throttle": command.get("throttle")}


def _observation_summary(source: dict[str, Any] | None) -> dict[str, Any]:
    observation = source.get("observation") if source is not None else None
    if not isinstance(observation, dict):
        return {"status": "absent", "frame_id": None, "reason": "no_observation"}
    status = observation.get("status")
    if status == "ready":
        value = observation.get("value") if isinstance(observation.get("value"), dict) else {}
        return {"status": "ready", "frame_id": value.get("observation_id") or source.get("frame_id"), "reason": ""}
    return {"status": str(status or "absent"), "frame_id": None, "reason": str(observation.get("reason") or status)}


def _memory_summary(source: dict[str, Any] | None) -> dict[str, Any]:
    evidence = source.get("evidence") if source is not None else None
    value = evidence.get("value") if isinstance(evidence, dict) else None
    if not isinstance(evidence, dict) or evidence.get("status") != "ready" or not isinstance(value, list):
        status = evidence.get("status") if isinstance(evidence, dict) else None
        return {"status": str(status or "absent"), "health": None, "record_count": None, "records": []}
    records = []
    for item in value[:_RETAINED_SHOWN]:
        if isinstance(item, dict):
            origin = item.get("origin") if isinstance(item.get("origin"), dict) else {}
            records.append({
                "record_id": item.get("record_id"),
                "kind": item.get("kind"),
                "confidence": item.get("confidence"),
                "frame_id": origin.get("frame_id"),
            })
    return {
        "status": "ready",
        "health": "healthy" if value else "empty",
        "record_count": len(value),
        "records": records,
    }


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


def inspect_decision_step(
    step: str,
    *,
    source: str | Path,
    plugins: list[str] | None = None,
    frame: int | None = None,
    record: bool = False,
    json_output: bool = False,
    max_frames: int = WORKBENCH_DEFAULT_MAX_FRAMES,
) -> CommandResult:
    """Replay a recording or images through the cycle and report ``step`` per frame.

    Every step runs the selection the source recorded, restaged per frame, or
    its default; ``plugins`` replaces the inspected step's. ``frame`` reports
    only that 0-based frame, after replaying the frames before it.
    """

    try:
        image_source, manifest = read_replay_source(source, max_frames=max_frames)
    except SourceValidationError as exc:
        return CommandResult(2, f"Could not read {step} inspect source: {exc}")
    count = len(image_source.frames)
    if frame is not None and not 0 <= frame < count:
        return CommandResult(2, f"--frame {frame} is outside the source's {count} frames (0-{count - 1}).")
    try:
        overrides = {step: packaged_activation(step, plugins)} if plugins else None
        replay = StepReplay(manifest, steps=_REPLAY_STEPS, overrides=overrides)
    except Exception as exc:  # Plugin construction is a CLI preflight boundary.
        return CommandResult(2, f"Could not load plugins for {step} inspect: {type(exc).__name__}: {exc}")

    frames: list[dict[str, Any]] = []
    for replay_frame in image_source.frames[: count if frame is None else frame + 1]:
        try:
            outcome = replay.run(replay_frame)
        except Exception as exc:  # Plugins are third-party code; name the frame that broke.
            return CommandResult(
                2, f"{step.capitalize()} inspect failed at {replay_frame.frame_id}: {type(exc).__name__}: {exc}"
            )
        if frame is not None and replay_frame.position != frame:
            continue
        step_record = getattr(outcome.result, step)
        frames.append({
            "position": replay_frame.position,
            "frame_id": replay_frame.frame_id,
            "frame_index": replay_frame.frame_index,
            "timestamp_ms": replay_frame.timestamp_ms,
            "image_path": str(replay_frame.image_path) if replay_frame.image_path is not None else None,
            "absence_reason": replay_frame.absence_reason,
            "record": step_record.to_dict() if step_record is not None else None,
            "plugin_report": outcome.plugin_reports.get(step),
            "steps": replay.step_payloads(),
        })

    run_id = inspect_run_id(image_source.source_id)
    report = {
        "schema": f"{step}_inspect_v1",
        "step": step,
        "run_id": run_id,
        "source_id": image_source.source_id,
        "source": {
            "path": str(image_source.source_path),
            "source_id": image_source.source_id,
            "frame_count": count,
        },
        "selections": replay.selection_records(),
        "frames": frames,
        "run_dir": None,
    }
    if record:
        run_dir = inspect_root(step) / run_id
        try:
            record_inspect_run(run_dir, report, image_source)
        except OSError as exc:
            return CommandResult(2, f"Could not record {step} inspect run {run_dir}: {exc}")
    if json_output:
        return CommandResult(0, json.dumps(report, indent=2, sort_keys=True))
    return CommandResult(0, _format_inspect_report(report))


def _format_inspect_report(report: dict[str, Any]) -> str:
    step = report["step"]
    source = report["source"]
    lines = [
        f"{step.capitalize()} inspect",
        "-" * len(f"{step} inspect"),
        f"Source: {source['path']} ({source['frame_count']} frames)",
        "Steps: " + ", ".join(
            f"{name}={','.join(selection['plugins']) or '-'}" if selection else f"{name}=off"
            for name, selection in report["selections"].items()
        ),
    ]
    for item in report["frames"]:
        lines.extend(["", f"Frame {item['position']}: {item['frame_id']}"])
        if item["absence_reason"]:
            lines.append(f"Absent: {item['absence_reason']}")
        lines.extend(format_step_record(step, item["record"]))
    if report["run_dir"]:
        lines.extend(["", f"Recorded: {display_path(Path(report['run_dir']))}"])
    return "\n".join(lines)
