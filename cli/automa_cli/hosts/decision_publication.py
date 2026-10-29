"""A host's decision publication, as the CLI reads it.

``host_publications`` fetches the publication; ``normalize_decision_publication``
checks it, and the rest turn it into the frame record and text the CLI shows.
"""

from __future__ import annotations

from copy import deepcopy
from functools import partial
from typing import Any

from autonomy.decision_cycle.action_identifiers import (
    require_ascii_id,
    require_safe_int,
)
from autonomy.runtime.frame_loop import DECISION_PUBLICATION_SCHEMA
from autonomy.runtime.report import HostReport, diagnostic_ceiling
from ..shared.publication_fields import PublicationError, require_id, require_mapping


class DecisionPublicationError(PublicationError):
    """A decision publication is absent or fails normalization."""


def _publication_error(
    reason: str,
    message: str,
    *,
    field: str | None = None,
) -> DecisionPublicationError:
    details: dict[str, Any] = {}
    if field is not None:
        details["field"] = field
    return DecisionPublicationError(reason, message, details=details)


def _required_int(
    value: object,
    *,
    field: str,
    allow_negative: bool = False,
) -> int:
    try:
        number = require_safe_int(value, field_name=field)
    except ValueError as exc:
        if allow_negative and type(value) is int:
            return int(value)
        raise _publication_error(
            "incomplete",
            f"Decision publication {field} must be a non-bool int.",
            field=field,
        ) from exc
    return number


def _decision_field_error(text: str, field: str) -> DecisionPublicationError:
    return _publication_error("incomplete", f"Decision publication {text}", field=field)


_required_id = partial(require_id, invalid=_decision_field_error)
_require_mapping = partial(require_mapping, invalid=_decision_field_error)


# Short aliases keep the consumer seam discoverable without making the
# producer or accepted decision schemas depend on these helpers.
def normalize_decision_publication(
    publication: object,
    *,
    host_name: str,
    now_ms: int,
    max_age_ms: int | None = None,
) -> dict[str, Any]:
    """Normalize and freshness-check a decision publication.

    The provider returns a detached view of the onboard transport and never
    creates vehicle/run/activation/frame identity. The typed cycle remains the
    ``action_result_v0`` export for common validation.
    """

    if publication is None:
        raise _publication_error(
            "missing",
            "Decision publication is missing.",
        )
    if not isinstance(publication, dict):
        raise _publication_error(
            "incomplete",
            "Decision publication must be a JSON object.",
        )
    if publication.get("schema") != DECISION_PUBLICATION_SCHEMA:
        raise _publication_error(
            "incomplete",
            f"Decision publication schema must be {DECISION_PUBLICATION_SCHEMA!r}.",
            field="schema",
        )
    try:
        host_name = require_ascii_id(host_name, field_name="host_name")
    except ValueError as exc:
        raise ValueError("host_name must be a valid identity") from exc
    if "status" not in publication or "ok" not in publication:
        raise _publication_error(
            "incomplete",
            "Decision publication must include status and ok.",
        )
    status = publication.get("status")
    ok = publication.get("ok")
    if status != "ready" or ok is not True:
        reason = publication.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            reason = "unavailable"
        reason = {
            "mismatched_frame": "mismatched",
            "future": "future_dated",
            "stale": "expired",
        }.get(reason, reason)
        allowed = {
            "missing",
            "incomplete",
            "mismatched",
            "future_dated",
            "expired",
            "reset",
            "failed_step",
            "publisher_missing",
            "unavailable",
        }
        if reason not in allowed:
            reason = "unavailable"
        raise _publication_error(
            reason,
            f"Decision publication is unavailable: {reason}.",
        )
    if publication.get("reason") != "":
        raise _publication_error(
            "incomplete",
            "Ready decision publication must have an empty reason.",
            field="reason",
        )

    read_at_ms = _required_int(
        publication.get("read_at_ms"), field="read_at_ms", allow_negative=True
    )
    result_age_ms = _required_int(
        publication.get("result_age_ms"),
        field="result_age_ms",
        allow_negative=True,
    )
    advertised_ceiling = publication.get("stale_after_ms")
    if type(advertised_ceiling) is not int or advertised_ceiling <= 0:
        raise _publication_error(
            "incomplete",
            "Decision publication stale_after_ms must be positive.",
            field="stale_after_ms",
        )

    try:
        report = HostReport.from_dict(publication.get("decision"))
    except (TypeError, ValueError) as exc:
        raise _publication_error(
            "incomplete",
            f"Decision publication is not a vehicle report: {exc}",
            field="decision",
        ) from exc
    try:
        published_ceiling = diagnostic_ceiling(report.values)
    except ValueError as exc:
        raise _publication_error(
            "incomplete",
            str(exc),
            field="decision.values.stale_after_ms",
        ) from exc
    if advertised_ceiling != published_ceiling:
        raise _publication_error(
            "incomplete",
            "Decision publication stale_after_ms does not match the report.",
            field="stale_after_ms",
        )
    if max_age_ms is None:
        ceiling = published_ceiling
    else:
        if type(max_age_ms) is not int or max_age_ms <= 0:
            raise ValueError("max_age_ms must be a positive non-bool int")
        ceiling = int(max_age_ms)
    decision = report.to_dict()
    values = report.values

    decision_host_name = report.host_name
    if decision_host_name != host_name:
        raise _publication_error(
            "mismatched",
            "Decision publication host_name does not match the requested host.",
            field="decision.host_name",
        )
    source_id = _required_id(values.get("source_id"), field="decision.values.source_id")
    run_id = report.run_id
    generation_id = report.generation_id
    frame_id = report.frame_id
    frame_index = report.frame_index
    timestamp_value = report.timestamp_ms
    published_at_ms = report.published_at_ms

    activation = _require_mapping(
        values.get("activation"), field="decision.values.activation"
    )
    for field in ("generation_id", "steps"):
        if field not in activation:
            raise _publication_error(
                "incomplete",
                f"Decision publication decision.values.activation.{field} is missing.",
                field=f"decision.values.activation.{field}",
            )
    if activation.get("generation_id") != generation_id:
        raise _publication_error(
            "mismatched",
            "Decision activation generation does not match its outer identity.",
            field="decision.values.activation.generation_id",
        )
    if not isinstance(activation.get("steps"), dict):
        raise _publication_error(
            "incomplete",
            "Decision activation steps must be an object.",
            field="decision.values.activation.steps",
        )

    # Cycle reconstruction and frame/activation alignment belong to the
    # shared report reader. This adapter owns only the HTTP envelope.
    if type(now_ms) is not int:
        raise ValueError("now_ms must be a non-bool int")
    age_ms = int(now_ms) - published_at_ms
    if age_ms < 0:
        raise _publication_error(
            "future_dated",
            f"Decision publication is future-dated by {-age_ms} ms.",
            field="decision.published_at_ms",
        )
    if age_ms > ceiling:
        raise _publication_error(
            "expired",
            f"Decision publication age {age_ms} ms exceeds {ceiling} ms.",
            field="decision.published_at_ms",
        )

    return {
        "publication": deepcopy(publication),
        "decision": deepcopy(decision),
        "host_name": decision_host_name,
        "source_id": source_id,
        "run_id": run_id,
        "generation_id": generation_id,
        "frame_id": frame_id,
        "frame_index": frame_index,
        "timestamp_ms": timestamp_value,
        "published_at_ms": published_at_ms,
        "read_at_ms": read_at_ms,
        "result_age_ms": age_ms,
        "max_age_ms": ceiling,
        "advertised_result_age_ms": result_age_ms,
    }


def publication_to_frame_record(publication: dict[str, Any]) -> dict[str, Any]:
    """Adapt an onboard publication, including its memory report."""
    frame = publication.get("frame") if isinstance(publication.get("frame"), dict) else {}
    perception = publication.get("perception")
    observation = publication.get("observation")
    control = publication.get("control")
    completed_at_ms = frame.get("completed_at_ms")
    duration_ms = publication.get("duration_ms")
    memory = publication.get("memory")
    return {
        "frame_id": frame.get("frame_id"),
        "frame_index": frame.get("frame_index"),
        "captured_at_ms": frame.get("captured_at_ms"),
        "perception_completed_at_ms": completed_at_ms,
        "perception_duration_ms": duration_ms,
        "cycle_duration_ms": duration_ms,
        "timing": deepcopy(publication.get("timing"))
        if isinstance(publication.get("timing"), dict)
        else None,
        "perception": perception if isinstance(perception, dict) else None,
        "observation": observation if isinstance(observation, dict) else None,
        "memory": memory if isinstance(memory, dict) else None,
        "control": control if isinstance(control, dict) else None,
        "generation_id": publication.get("generation_id"),
        "step_activations": deepcopy(publication.get("step_activations")),
        "context": deepcopy(publication.get("context")),
        "presets": deepcopy(publication.get("presets")),
        "health": publication.get("health"),
        "result_age_ms": publication.get("result_age_ms"),
        "action_policy": publication.get("mode"),
        "control_source": "runtime_host",
        "control_application": (
            "shared_execution" if publication.get("mode") == "autonomy" else "not_applied"
        ),
    }


def presets_text(presets: object) -> str:
    """``step=preset`` pairs for each step a runtime reports a preset for."""

    if not isinstance(presets, dict) or not presets:
        return "none"
    return " ".join(f"{step}={preset}" for step, preset in presets.items())


def perception_text_from_publication(publication: dict[str, Any]) -> str:
    perception = publication.get("perception")
    if isinstance(perception, dict):
        text = perception.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        lines = perception.get("lines")
        if isinstance(lines, list) and lines:
            return "\n".join(str(line) for line in lines)
        status = perception.get("status")
        thing_count = len(perception.get("things") or []) if isinstance(perception.get("things"), list) else 0
        signal_count = (
            len(perception.get("signals") or []) if isinstance(perception.get("signals"), list) else 0
        )
        return (
            f"perception status={status or 'unknown'} "
            f"signals={signal_count} things={thing_count}"
        )

    health = publication.get("health") or "unknown"
    error = publication.get("error")
    if error:
        return f"health={health}\nerror={error}"
    return f"health={health}\n(no perception payload in latest state)"
