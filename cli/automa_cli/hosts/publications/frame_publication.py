"""A host's completed frame, as the CLI reads it.

``host_publications`` fetches the publication. ``read_frame_publication``
checks the report. The rest turn that report into the text the CLI shows.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from autonomy.decision_cycle.action_identifiers import require_ascii_id
from autonomy.decision_cycle.activation import activation_presets
from autonomy.decision_cycle.perception.evidence.rendering import text_for_stored_perception
from autonomy.decision_cycle.timing import step_timing
from autonomy.runtime.frame_loop import FRAME_PUBLICATION_SCHEMA
from autonomy.runtime.report import HostReport, diagnostic_ceiling
from .publication_fields import PublicationError, require_id, require_mapping


class FramePublicationError(PublicationError):
    """A completed frame is absent or has an invalid report or identity."""


def _publication_error(
    reason: str,
    message: str,
    *,
    field: str | None = None,
) -> FramePublicationError:
    details: dict[str, Any] = {}
    if field is not None:
        details["field"] = field
    return FramePublicationError(reason, message, details=details)


def _frame_field_error(text: str, field: str) -> FramePublicationError:
    return _publication_error("incomplete", f"Frame publication {text}", field=field)


_required_id = partial(require_id, invalid=_frame_field_error)
_require_mapping = partial(require_mapping, invalid=_frame_field_error)


def read_frame_publication(
    publication: object,
    *,
    host_name: str,
    now_ms: int,
    max_age_ms: int | None = None,
) -> dict[str, Any]:
    """Read one completed frame. A null step, a raised cycle, or a stale age stays visible.

    The reader does not invent identity. A missing report is the only absence.
    ``max_age_ms`` overrides the ceiling reported to the caller and does not hide the frame.
    """

    if publication is None:
        raise _publication_error("missing", "Frame publication is missing.")
    if not isinstance(publication, dict):
        raise _publication_error("incomplete", "Frame publication must be a JSON object.")
    if publication.get("schema") != FRAME_PUBLICATION_SCHEMA:
        raise _publication_error(
            "incomplete",
            f"Frame publication schema must be {FRAME_PUBLICATION_SCHEMA!r}.",
            field="schema",
        )
    try:
        host_name = require_ascii_id(host_name, field_name="host_name")
    except ValueError as exc:
        raise ValueError("host_name must be a valid identity") from exc
    if publication.get("report") is None:
        raise _publication_error(
            "missing",
            "Frame publication has no completed report.",
            field="report",
        )
    try:
        report = HostReport.from_dict(publication.get("report"))
    except (TypeError, ValueError) as exc:
        raise _publication_error(
            "incomplete",
            f"Frame publication is not a vehicle report: {exc}",
            field="report",
        ) from exc
    try:
        published_ceiling = diagnostic_ceiling(report.values)
    except ValueError as exc:
        raise _publication_error(
            "incomplete",
            str(exc),
            field="report.values.stale_after_ms",
        ) from exc
    advertised_ceiling = publication.get("stale_after_ms")
    if type(advertised_ceiling) is int and advertised_ceiling != published_ceiling:
        raise _publication_error(
            "incomplete",
            "Frame publication stale_after_ms does not match the report.",
            field="stale_after_ms",
        )
    if max_age_ms is None:
        ceiling = published_ceiling
    else:
        if type(max_age_ms) is not int or max_age_ms <= 0:
            raise ValueError("max_age_ms must be a positive non-bool int")
        ceiling = int(max_age_ms)
    if report.host_name != host_name:
        raise _publication_error(
            "mismatched",
            "Frame publication host_name does not match the requested host.",
            field="report.host_name",
        )
    values = report.values
    source_id = _required_id(values.get("source_id"), field="report.values.source_id")
    activation = _require_mapping(values.get("activation"), field="report.values.activation")
    for field in ("generation_id", "steps"):
        if field not in activation:
            raise _publication_error(
                "incomplete",
                f"Frame publication report.values.activation.{field} is missing.",
                field=f"report.values.activation.{field}",
            )
    if activation.get("generation_id") != report.generation_id:
        raise _publication_error(
            "mismatched",
            "Frame activation generation does not match its outer identity.",
            field="report.values.activation.generation_id",
        )
    if not isinstance(activation.get("steps"), dict):
        raise _publication_error(
            "incomplete",
            "Frame activation steps must be an object.",
            field="report.values.activation.steps",
        )
    source_frame = values.get("source_frame")
    if source_frame is not None:
        expected = {"frame_id": report.frame_id, "frame_index": report.frame_index,
                    "captured_at_ms": report.timestamp_ms}
        if not isinstance(source_frame, dict) or any(source_frame.get(key) != value for key, value in expected.items()):
            raise _publication_error("mismatched", "Frame source identity does not match the report.", field="report.values.source_frame")
    if type(now_ms) is not int:
        raise ValueError("now_ms must be a non-bool int")
    read_at_ms = publication.get("read_at_ms")
    if type(read_at_ms) is not int:
        read_at_ms = int(now_ms)
    advertised_age = publication.get("result_age_ms")
    if type(advertised_age) is not int:
        advertised_age = max(0, int(now_ms) - report.published_at_ms)
    return {
        "host_name": report.host_name,
        "source_id": source_id,
        "run_id": report.run_id,
        "generation_id": report.generation_id,
        "frame_id": report.frame_id,
        "frame_index": report.frame_index,
        "timestamp_ms": report.timestamp_ms,
        "published_at_ms": report.published_at_ms,
        "read_at_ms": read_at_ms,
        "result_age_ms": int(now_ms) - report.published_at_ms,
        "max_age_ms": ceiling,
        "advertised_result_age_ms": advertised_age,
        "report": report,
        "publication": publication,
    }


def presets_text(presets: object) -> str:
    """``step=preset`` pairs for each step a runtime reports a preset for."""

    if not isinstance(presets, dict) or not presets:
        return "none"
    return " ".join(f"{step}={preset}" for step, preset in presets.items())


def _report(publication: dict[str, Any]) -> dict[str, Any]:
    report = publication.get("report") if isinstance(publication, dict) else None
    return report if isinstance(report, dict) else {}


def _cycle(publication: dict[str, Any]) -> dict[str, Any]:
    cycle = _report(publication).get("cycle")
    return cycle if isinstance(cycle, dict) else {}


def publication_capture(publication: dict[str, Any]) -> tuple[Any, ...]:
    """What identifies one frame: its id, capture, completion, and generation."""

    report = _report(publication)
    values = report.get("values") if isinstance(report.get("values"), dict) else {}
    source = values.get("source_frame") if isinstance(values.get("source_frame"), dict) else {}
    return (
        report.get("host_name"),
        report.get("run_id"),
        report.get("frame_id"),
        source.get("captured_at_ms", report.get("timestamp_ms")),
        source.get("completed_at_ms", report.get("published_at_ms")),
        report.get("generation_id"),
    )


def publication_presets(publication: dict[str, Any]) -> dict[str, str]:
    """Preset labels carried on the report's activation."""

    values = _report(publication).get("values")
    activation = values.get("activation") if isinstance(values, dict) else None
    steps = activation.get("steps") if isinstance(activation, dict) else None
    if not isinstance(steps, dict):
        return {}
    return activation_presets(steps)


def perception_duration_ms(publication: dict[str, Any]) -> Any:
    """The perception step's duration from the cycle timing, or None without it."""

    values = _report(publication).get("values")
    timing = values.get("timing") if isinstance(values, dict) else None
    step = step_timing(timing if isinstance(timing, dict) else None, "perception")
    return None if step is None else step.get("duration_ms")


def perception_text_from_publication(publication: dict[str, Any]) -> str:
    """The perception text rendered from stored signals and things."""

    perception = _cycle(publication).get("perception")
    if isinstance(perception, dict):
        text = text_for_stored_perception(perception)
        if text.strip():
            return text.strip()
    report = _report(publication)
    if report.get("status") == "error" and report.get("error"):
        return f"status=error\nerror={report['error']}"
    health = publication.get("health") or "unknown"
    return f"health={health}\n(no perception payload in latest state)"


def health_error(publication: dict[str, Any]) -> str | None:
    """Why no completed frame is visible. A stale, raised, or imageless frame stays visible."""

    if not isinstance(publication, dict) or publication.get("report") is None:
        health = str(publication.get("health") or "unknown") if isinstance(publication, dict) else "unknown"
        return f"The frame publication health is {health!r}."
    health = str(publication.get("health") or "")
    if health in {"absent", "warming"}:
        return f"The frame publication health is {health!r}."
    return None
