"""Live streams for proposal, plan, and action."""

from __future__ import annotations

import time
from typing import Any, TextIO

from autonomy.decision_cycle.activation import DECISION_STEPS

from ..shared.command_result import CommandResult
from ..shared.controller_releases import RuntimeHostError, bundle_paths
from .step_formatting import format_step_record, format_step_timing
from ..hosts.decision import (
    DecisionSurfaceError,
    accept_decision_publication,
    decision_error_payload,
    require_valid_activations,
)
from ..hosts.host_publications import fetch_decision_publication
from ..hosts.runtime_hosts import runtime_base_url
from ..hosts.view_feed import ViewFeed, view_page_lines
from .streaming import poll_stream, stream_preflight_outcome, unavailable_probe


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
        require_valid_activations(bundle_paths(host_name), host_name=host_name, steps=DECISION_STEPS)
        base_url = runtime_base_url(host_name)
    except DecisionSurfaceError as exc:
        return CommandResult(*stream_preflight_outcome(
            step=step, live=_error_probe(exc), message=exc.message_text,
            json_output=json_output, stream=output,
        ))
    except RuntimeHostError as exc:
        return CommandResult(*stream_preflight_outcome(
            step=step, live=unavailable_probe(f"host_{step}_stream", host_name, str(exc)),
            message=str(exc), json_output=json_output, stream=output,
        ))

    # A running terminal stream hosts the runtime view; its decision page shows
    # the evidence, candidates and selection behind each record.
    view = None if json_output or once else ViewFeed(host_name, base_url=base_url, timeout_s=timeout_s)

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
        text = _format_stream_frame(step, live) if live["status"] == "live" else live["error"]
        if view is None:
            return text
        return "\n".join([text, "", *view_page_lines(view, ("perception view", "/perception"))])

    try:
        return poll_stream(
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
    """A decision error as a stream probe: ``error`` is its text and ``error_code`` its code."""

    return {
        **decision_error_payload(
            error_code=exc.error_code, error=exc.message_text, exit_code=exc.exit_code,
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
