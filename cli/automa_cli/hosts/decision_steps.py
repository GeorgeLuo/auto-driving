"""Live info and streams for proposal, plan, and action."""

from __future__ import annotations

import json
import time
from typing import Any, TextIO

from autonomy.decision_cycle.action.hold import HOLD_IDLE_REASON, HoldAction
from autonomy.decision_cycle.action.selected import SelectedAction
from autonomy.decision_cycle.activation import DECISION_STEPS
from implementations.decision_cycle.action.mode.plugin import LIVE_MODES, ModeAction

from ..shared.command_result import CommandResult
from .decision import (
    DecisionSurfaceError,
    _require_valid_activations,
    accept_decision_publication,
    decision_error_payload,
)
from .host_publications import fetch_decision_publication
from .runtime_hosts import RuntimeHostError, bundle_paths, runtime_base_url
from .step_activations import decision_identity
from .step_formatting import format_step_record, format_step_timing
from .step_schema import format_staged_step, staged_step_info
from .streaming import _poll_stream, _unavailable_probe, _view_lines, _ViewFeed, stream_preflight_outcome


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
    """Poll the step's record in the latest cycle the host published.

    Each probe is the ``host_<step>_stream`` frame with ``status: live``, or
    the decision error that kept it from one with ``status: error``.
    """

    timeout_s = max(0.1, float(timeout_s))
    try:
        # Broken staging is the first thing to fix; it names the command that replaces it.
        _require_valid_activations(bundle_paths(host_name), host_name=host_name, steps=DECISION_STEPS)
        base_url = runtime_base_url(host_name)
    except DecisionSurfaceError as exc:
        return CommandResult(*stream_preflight_outcome(
            step=step, live=_error_probe(exc), message=exc.message_text,
            json_output=json_output, stream=output,
        ))
    except RuntimeHostError as exc:
        return CommandResult(*stream_preflight_outcome(
            step=step, live=_unavailable_probe(f"host_{step}_stream", host_name, str(exc)),
            message=str(exc), json_output=json_output, stream=output,
        ))

    # A running terminal stream hosts the runtime view; its decision page shows
    # the evidence, candidates and selection behind each record.
    view = None if json_output or once else _ViewFeed(host_name, base_url=base_url, timeout_s=timeout_s)

    def read_frame() -> dict[str, Any]:
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

    def probe() -> tuple[dict[str, Any], None]:
        if view is not None:
            view.publish(view.read_observation()[0])
        try:
            return {**read_frame(), "status": "live"}, None
        except DecisionSurfaceError as exc:
            return _error_probe(exc), None

    def render(live: dict[str, Any], _source: None) -> str:
        text = _format_stream_frame(step, live) if live["status"] == "live" else live["message"]
        if view is None:
            return text
        return "\n".join([text, "", *_view_lines(view, ("perception view", "/perception"))])

    try:
        return _poll_stream(
            step=step,
            probe=probe,
            render=render,
            refresh_s=refresh_s,
            once=once,
            no_clear=no_clear,
            json_output=json_output,
            stream=output,
        )
    finally:
        if view is not None:
            view.stop()


def _error_probe(exc: DecisionSurfaceError) -> dict[str, Any]:
    """A decision error as a stream probe; ``error`` keeps its code."""

    return {
        **decision_error_payload(
            error=exc.error, message=exc.message_text, exit_code=exc.exit_code,
            host_name=exc.host_name, details=exc.details,
        ),
        "status": "error",
    }


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
