"""Live info, streams, and record formatters for proposal, plan, and action."""

from __future__ import annotations

import json
import time
from typing import Any, Callable, TextIO

from autonomy.decision_cycle.action.hold import HOLD_IDLE_REASON, HoldAction, idle_output
from autonomy.decision_cycle.action.selected import SelectedAction
from autonomy.decision_cycle.activation import DECISION_STEPS
from autonomy.decision_cycle.confidence import confidence_text
from autonomy.decision_cycle.timing import step_timing
from implementations.decision_cycle.action.mode.plugin import LIVE_MODES, ModeAction

from .decision import (
    CommandResult,
    DecisionSurfaceError,
    _error_result,
    _require_valid_activations,
    accept_decision_publication,
    decision_error_payload,
)
from .memory_report import evidence_publisher, evidence_records, plugin_summaries
from .host_publications import fetch_decision_publication
from .runtime_hosts import RuntimeHostError, bundle_paths, runtime_base_url
from .step_activations import decision_identity
from .step_schema import format_staged_step, staged_step_info
from .streaming import _view_line, _ViewFeed

_RETAINED_SHOWN = 12


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------


def get_host_step_info(
    step: str,
    *,
    host_name: str,
    json_output: bool = False,
    include_live: bool = True,
    timeout_s: float = 3.0,
) -> CommandResult:
    """The step's staged plugins and schema, the decision it belongs to, and its live runner."""

    bundle = bundle_paths(host_name)
    staged, error = staged_step_info(bundle, host_name, step)
    if error is not None:
        return CommandResult(2, error)
    try:
        _require_valid_activations(bundle, host_name=host_name, steps=DECISION_STEPS)
    except DecisionSurfaceError as exc:
        return CommandResult(exc.exit_code, exc.message_text)
    identity = decision_identity(bundle)
    plugins = {
        name: list((identity["steps"].get(name) or {}).get("plugins") or [])
        for name in DECISION_STEPS
    }
    payload: dict[str, Any] = {
        "schema": f"host_{step}_info",
        "host_name": host_name,
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

        payload["live"] = probe_live_step(step, host_name=host_name, timeout_s=timeout_s)
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

        lines.extend(["", format_live_step_screen(step, host_name=payload["host_name"], live=live)])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# stream
# ---------------------------------------------------------------------------


def stream_host_step(
    step: str,
    *,
    host_name: str,
    refresh_s: float = 0.5,
    once: bool = False,
    no_clear: bool = False,
    json_output: bool = False,
    output: TextIO | None = None,
    timeout_s: float = 3.0,
) -> CommandResult:
    """Poll the step's record in the latest cycle the host published."""

    timeout_s = max(0.1, float(timeout_s))
    try:
        # Broken staging is the first thing to fix; it names its restage command.
        _require_valid_activations(bundle_paths(host_name), host_name=host_name, steps=DECISION_STEPS)
        base_url = runtime_base_url(host_name)
    except DecisionSurfaceError as exc:
        return _error_result(exc, json_output=json_output)
    except RuntimeHostError as exc:
        return _error_result(DecisionSurfaceError(
            "runtime_unavailable", str(exc), host_name=host_name, details={"reason": "missing"},
        ), json_output=json_output)

    # A running terminal stream hosts the runtime view; its decision page shows
    # the evidence, candidates and selection behind each record.
    view = None if json_output or once else _ViewFeed(host_name, base_url=base_url, timeout_s=timeout_s)

    def accept() -> dict[str, Any]:
        if view is not None:
            view.publish(view.read_observation()[0])
        try:
            publication = fetch_decision_publication(base_url, timeout_s=timeout_s)
        except (ConnectionError, OSError) as exc:
            raise DecisionSurfaceError(
                "runtime_decision_unavailable",
                f"Could not read the decision publication at {base_url}: {exc}",
                host_name=host_name,
                details={"reason": "missing", "transport": str(exc)},
            ) from exc
        normalized = accept_decision_publication(
            publication, host_name=host_name, now_ms=int(time.time() * 1000),
        )
        report = normalized["decision"]
        activation = report["values"]["activation"]
        return {
            "schema": f"host_{step}_stream",
            **{key: report.get(key) for key in (
                "host_name", "run_id", "generation_id", "frame_id", "frame_index", "timestamp_ms",
            )},
            # The host computes the result age on its own clock.
            "freshness": {"age_ms": normalized["result_age_ms"], "max_age_ms": normalized["max_age_ms"]},
            "plugins": list((activation["steps"].get(step) or {}).get("plugins") or []),
            "record": report["cycle"][step],
            # The cycle's timing; a host that does not publish it leaves None.
            "timing": report["values"].get("timing"),
            **({"application": report["application"]} if step == "action" else {}),
            **({key: report["cycle"][key] for key in ("observation", "memory")}
               if step == "proposal" else {}),
        }

    try:
        return _poll(
            host_name=host_name,
            accept=accept,
            render=lambda frame: _format_stream_frame(step, frame),
            footer=(lambda: [
                view.decision_line(), _view_line("perception view", view.url, view.error, "/perception"),
            ]) if view is not None else None,
            refresh_s=refresh_s,
            once=once,
            no_clear=no_clear,
            json_output=json_output,
            output=output,
        )
    finally:
        if view is not None:
            view.stop()


def _poll(
    *,
    host_name: str,
    accept: Callable[[], dict[str, Any]],
    render: Callable[[dict[str, Any]], str],
    footer: Callable[[], list[str]] | None = None,
    refresh_s: float,
    once: bool,
    no_clear: bool,
    json_output: bool,
    output: TextIO | None,
) -> CommandResult:
    """``accept`` returns one stream frame or raises DecisionSurfaceError,
    which the loop prints and keeps polling past; ``--once`` exits with it.
    ``footer`` lines follow each terminal frame, the error ones too."""

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
                    error=exc.error, message=exc.message_text, host_name=host_name, details=exc.details,
                ), sort_keys=True) if json_output else last_error
            if footer is not None and not json_output:
                text = "\n".join([text, "", *footer()])
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
        f"{step.capitalize()} stream: {frame['host_name']} frame={frame['frame_id']} "
        f"run={frame['run_id']} age_ms={freshness['age_ms']} max_age_ms={freshness['max_age_ms']}",
        f"Generation: {frame['generation_id']}",
        f"Plugins: {', '.join(frame['plugins']) or 'none'}",
        *format_step_record(
            step, frame["record"], observation=frame.get("observation"), memory=frame.get("memory"),
        ),
    ]
    if step == "action":
        application = frame["application"]
        lines.append(
            "Host application: "
            f"applied={str(application.get('applied') is True).lower()} "
            f"mode={application.get('mode')} reason={application.get('reason')} "
            f"steering={application.get('steering')} throttle={application.get('throttle')}"
        )
    lines.append(format_step_timing(step, frame["timing"]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# One step record as text; the stream and inspect both print it.
# ---------------------------------------------------------------------------


def format_step_timing(step: str, timing: dict[str, Any] | None) -> str:
    """``step``'s and its plugins' time in one cycle's ``timing``, then the cycle's."""

    entry = step_timing(timing, step)
    if entry is None:
        return "Timing: none"
    plugins = ", ".join(
        f"{plugin.get('plugin_id')} {_ms(plugin.get('duration_ms'))}"
        for plugin in entry.get("plugins") or ()
        if isinstance(plugin, dict)
    )
    return (
        f"Timing: {step} {_ms(entry.get('duration_ms'))}"
        + (f" ({plugins})" if plugins else "")
        + f"; cycle {_ms(timing.get('duration_ms'))}"
    )


def _ms(value: Any) -> str:
    return f"{value:.3f} ms" if isinstance(value, (int, float)) else "-"


def format_step_record(
    step: str, record: dict[str, Any] | None, *,
    observation: dict[str, Any] | None = None, memory: dict[str, Any] | None = None,
) -> list[str]:
    if record is None:
        return [f"{step.capitalize()}: none (an earlier step failed or {step} is disabled)"]
    if step == "proposal":
        return _proposal_lines(record, observation=observation, memory=memory)
    if step == "plan":
        return _plan_lines(record)
    return _action_lines(record)


def _proposal_lines(
    record: dict[str, Any], *, observation: dict[str, Any] | None, memory: dict[str, Any] | None,
) -> list[str]:
    lines = [
        f"Proposal: status={record.get('status')} reason={record.get('reason') or '-'}",
        f"Observation: {observation['observation_id'] if observation is not None else 'none'}",
    ]
    lines.extend(
        f"Memory: plugin={item['plugin_id']} health={item['health']} "
        f"records={item['record_count']} epoch={item['epoch_id']}"
        for item in plugin_summaries(memory)
    )
    lines.append(f"Memory: publisher={evidence_publisher(memory) or 'none'}")
    for item in (evidence_records(memory) or [])[:_RETAINED_SHOWN]:
        origin = item.get("origin") or {}
        lines.append(
            f"Retained: record_id={item.get('record_id')} kind={item.get('kind')} "
            f"confidence={confidence_text(item.get('confidence'))} frame_id={origin.get('frame_id')}"
        )
    lines.extend(_candidate_lines(record.get("candidates"), selected=None))
    return lines


def _plan_lines(record: dict[str, Any]) -> list[str]:
    selected = record.get("selected_proposal_id")
    contributions = record.get("contributions") if isinstance(record.get("contributions"), list) else []
    return [
        f"Plan: status={record.get('status')} selector={record.get('selector_id')} selected={selected}",
        f"Command: {json.dumps(_command(record.get('command')), sort_keys=True)} "
        f"reason={record.get('reason')} confidence={confidence_text(record.get('confidence'))}",
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
        f"confidence={confidence_text(item.get('confidence'))} reason={item.get('reason')} "
        f"command={json.dumps(_command(item.get('command')), sort_keys=True)}"
        for item in candidates
    ]


def _command(command: Any) -> dict[str, Any] | None:
    if not isinstance(command, dict):
        return None
    return {"steering": command.get("steering"), "throttle": command.get("throttle")}
