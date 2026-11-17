"""Live proposal, plan, and action records from the host's decision publication."""

from __future__ import annotations

from copy import deepcopy
from typing import Callable

from autonomy.decision_cycle.activation import DECISION_STEPS
from autonomy.runtime.frame_loop import FramePublication

from ...hosts.publications.decision import (
    DecisionSurfaceError,
    accept_decision_publication,
    decision_error_payload,
    require_valid_activations,
)
from ...hosts.publications.frame_publication import step_unavailable
from ...hosts.publications.host_publications import fetch_frame_publication
from ...hosts.discovery import runtime_base_url
from ...hosts.errors import RuntimeHostError
from ...releases.paths import bundle_paths
from .models import StreamEvent, StreamResult
from .polling import poll_events, preflight_failure, unavailable_probe
from .session import StreamView, with_view


def stream_decision(
    step: str,
    *,
    host_name: str,
    refresh_s: float,
    once: bool,
    timeout_s: float,
    serve_view: bool,
    emit: Callable[[StreamEvent], None],
) -> StreamResult:
    """Poll one decision step in the latest cycle the host published.

    Each probe is the ``host_<step>_stream`` frame with ``status: live``; the
    same frame with ``status: stale`` or ``error`` and its reason when the frame
    is past its ceiling or the cycle raised before ``step`` recorded; or the
    decision error that kept it from one with ``status: error``.
    ``serve_view`` hosts the runtime view and shares one observation read
    with that view's decision page.
    """

    timeout_s = max(0.1, float(timeout_s))
    try:
        # Broken staging is the first thing to fix; it names the command that replaces it.
        require_valid_activations(bundle_paths(host_name), host_name=host_name, steps=DECISION_STEPS)
        base_url = runtime_base_url(host_name)
    except DecisionSurfaceError as exc:
        return preflight_failure(message=exc.message_text, live=_error_probe(exc))
    except RuntimeHostError as exc:
        return preflight_failure(
            message=str(exc),
            live=unavailable_probe(f"host_{step}_stream", host_name, str(exc)),
        )

    view = StreamView(host_name, base_url=base_url, timeout_s=timeout_s) if serve_view else None
    try:
        return poll_events(
            step=step,
            probe=lambda: _probe_decision(
                step, view=view, host_name=host_name, base_url=base_url, timeout_s=timeout_s,
            ),
            refresh_s=refresh_s, once=once, emit=lambda event: emit(with_view(view, event)),
        )
    finally:
        if view is not None:
            view.close()


def _unreadable(base_url: str, host_name: str, detail: str) -> DecisionSurfaceError:
    return DecisionSurfaceError(
        "runtime_decision_unavailable",
        f"Could not read the decision publication at {base_url}: {detail}",
        host_name=host_name,
        details={"reason": "missing", "transport": detail},
    )


def _read_decision_frame(
    step: str,
    publication: FramePublication | None,
    *,
    host_name: str,
    base_url: str,
    timeout_s: float,
) -> dict[str, object]:
    try:
        if publication is None:
            publication = fetch_frame_publication(base_url, timeout_s=timeout_s)
    except (ConnectionError, OSError) as exc:
        raise _unreadable(base_url, host_name, str(exc)) from exc
    report = accept_decision_publication(publication, host_name=host_name)
    activation = report.values["activation"]
    frame = {
        "schema": f"host_{step}_stream",
        "host_name": report.host_name,
        "run_id": report.run_id,
        "generation_id": report.generation_id,
        "frame_id": report.frame_id,
        "frame_index": report.frame_index,
        "timestamp_ms": report.timestamp_ms,
        # The host computes the result age on its own clock.
        "freshness": {"age_ms": publication.result_age_ms, "max_age_ms": publication.stale_after_ms},
        "plugins": list((activation["steps"].get(step) or {}).get("plugins") or []),
        "record": deepcopy(report.cycle.get(step)),
        # The cycle's timing; a host that does not publish it leaves None.
        "timing": deepcopy(report.values.get("timing")),
    }
    if step == "action":
        frame["application"] = report.application.to_dict()
    if step == "proposal":
        frame["observation"] = deepcopy(report.cycle["observation"])
        frame["memory"] = deepcopy(report.cycle["memory"])
    unavailable = step_unavailable(publication, step)
    if unavailable is None:
        return {**frame, "status": "live"}
    return {**frame, "status": unavailable[0], "error": unavailable[1]}


def _probe_decision(
    step: str,
    *,
    view: StreamView | None,
    host_name: str,
    base_url: str,
    timeout_s: float,
) -> tuple[dict[str, object], None]:
    publication = None
    try:
        if view is not None:
            publication, error = view.read_observation()
            view.publish(publication)
            if error is not None:
                raise _unreadable(base_url, host_name, error)
        return _read_decision_frame(
            step, publication, host_name=host_name, base_url=base_url, timeout_s=timeout_s,
        ), None
    except DecisionSurfaceError as exc:
        return _error_probe(exc), None


def _error_probe(exc: DecisionSurfaceError) -> dict[str, object]:
    """A decision error as a stream probe: ``error`` is its text and ``error_code`` its code."""

    return {
        **decision_error_payload(
            error_code=exc.error_code, error=exc.message_text, exit_code=exc.exit_code,
            host_name=exc.host_name, details=exc.details,
        ),
        "status": "error",
    }
