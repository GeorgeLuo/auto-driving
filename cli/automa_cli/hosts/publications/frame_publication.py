"""A host's completed frame, as the CLI reads it.

``host_publications`` fetches the publication. ``read_frame_publication``
checks the report. The rest turn that report into the text the CLI shows.
"""

from __future__ import annotations

from functools import partial

from autonomy.decision_cycle.action_identifiers import require_ascii_id
from autonomy.decision_cycle.activation import activation_presets
from autonomy.decision_cycle.perception.interface import PerceptionText
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
    details: dict[str, object] = {}
    if field is not None:
        details["field"] = field
    return FramePublicationError(reason, message, details=details)


def _frame_field_error(text: str, field: str) -> FramePublicationError:
    return _publication_error("incomplete", f"Frame publication {text}", field=field)


_required_id = partial(require_id, invalid=_frame_field_error)
_require_mapping = partial(require_mapping, invalid=_frame_field_error)


def _publication_object(publication: object) -> dict[str, object]:
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
    if publication.get("report") is None:
        raise _publication_error(
            "missing",
            "Frame publication has no completed report.",
            field="report",
        )
    return publication


def _host_report(publication: dict[str, object], host_name: str) -> HostReport:
    try:
        host_name = require_ascii_id(host_name, field_name="host_name")
    except ValueError as exc:
        raise ValueError("host_name must be a valid identity") from exc
    try:
        report = HostReport.from_dict(publication.get("report"))
    except (TypeError, ValueError) as exc:
        raise _publication_error(
            "incomplete",
            f"Frame publication is not a vehicle report: {exc}",
            field="report",
        ) from exc
    if report.host_name != host_name:
        raise _publication_error(
            "mismatched",
            f"Frame publication is from host {report.host_name!r}, not {host_name!r}.",
            field="report.host_name",
        )
    return report


def _stale_ceiling(publication: dict[str, object], report: HostReport) -> int:
    try:
        ceiling = diagnostic_ceiling(report.values)
    except ValueError as exc:
        raise _publication_error(
            "incomplete",
            str(exc),
            field="report.values.stale_after_ms",
        ) from exc
    advertised = publication.get("stale_after_ms")
    if type(advertised) is int and advertised != ceiling:
        raise _publication_error(
            "incomplete",
            "Frame publication stale_after_ms does not match the report.",
            field="stale_after_ms",
        )
    return ceiling


def _require_activation_steps(values: dict[str, object]) -> None:
    """The selection rides in ``activation.steps``. Generation stays on the report."""

    activation = _require_mapping(values.get("activation"), field="report.values.activation")
    if "steps" not in activation:
        raise _publication_error(
            "incomplete",
            "Frame publication report.values.activation.steps is missing.",
            field="report.values.activation.steps",
        )
    if not isinstance(activation.get("steps"), dict):
        raise _publication_error(
            "incomplete",
            "Frame activation steps must be an object.",
            field="report.values.activation.steps",
        )


def _publication_times(
    publication: dict[str, object], report: HostReport, now_ms: int,
) -> tuple[int, int]:
    if type(now_ms) is not int:
        raise ValueError("now_ms must be a non-bool int")
    read_at_ms = publication.get("read_at_ms")
    if type(read_at_ms) is not int:
        read_at_ms = int(now_ms)
    age_ms = publication.get("result_age_ms")
    if type(age_ms) is not int or age_ms < 0:
        age_ms = max(0, int(now_ms) - report.published_at_ms)
    return read_at_ms, age_ms


def read_frame_publication(
    publication: object,
    *,
    host_name: str,
    now_ms: int,
) -> dict[str, object]:
    """Read one completed frame. A null step, a raised cycle, or a stale age stays visible.

    The reader does not invent identity. A missing report is the only absence.
    The age is the one the host computed on its own clock, when it sent one.
    """

    body = _publication_object(publication)
    report = _host_report(body, host_name)
    ceiling = _stale_ceiling(body, report)
    _require_activation_steps(report.values)
    read_at_ms, age_ms = _publication_times(body, report, now_ms)
    return {
        "host_name": report.host_name,
        "source_id": _required_id(report.values.get("source_id"), field="report.values.source_id"),
        "run_id": report.run_id,
        "generation_id": report.generation_id,
        "frame_id": report.frame_id,
        "frame_index": report.frame_index,
        "timestamp_ms": report.timestamp_ms,
        "published_at_ms": report.published_at_ms,
        "read_at_ms": read_at_ms,
        "result_age_ms": age_ms,
        "max_age_ms": ceiling,
        "report": report,
        "publication": body,
    }


def presets_text(presets: object) -> str:
    """``step=preset`` pairs for each step a runtime reports a preset for."""

    if not isinstance(presets, dict) or not presets:
        return "none"
    return " ".join(f"{step}={preset}" for step, preset in presets.items())


def _report(publication: dict[str, object]) -> dict[str, object]:
    report = publication.get("report")
    return report if isinstance(report, dict) else {}


def _cycle(publication: dict[str, object]) -> dict[str, object]:
    cycle = _report(publication).get("cycle")
    return cycle if isinstance(cycle, dict) else {}


def publication_capture(publication: dict[str, object]) -> tuple[object, ...]:
    """What identifies one frame: its id, capture, completion, and generation."""

    report = _report(publication)
    return (
        report.get("host_name"),
        report.get("run_id"),
        report.get("frame_id"),
        report.get("timestamp_ms"),
        report.get("published_at_ms"),
        report.get("generation_id"),
    )


def publication_presets(publication: dict[str, object]) -> dict[str, str]:
    """Preset labels carried on the report's activation."""

    values = _report(publication).get("values")
    activation = values.get("activation") if isinstance(values, dict) else None
    steps = activation.get("steps") if isinstance(activation, dict) else None
    if not isinstance(steps, dict):
        return {}
    return activation_presets(steps)


def perception_duration_ms(publication: dict[str, object]) -> object:
    """The perception step's duration from the cycle timing, or None without it."""

    values = _report(publication).get("values")
    timing = values.get("timing") if isinstance(values, dict) else None
    step = step_timing(timing if isinstance(timing, dict) else None, "perception")
    return None if step is None else step.get("duration_ms")


def perception_text_from_publication(publication: dict[str, object]) -> str:
    """The perception text rendered from stored signals and things."""

    perception = _cycle(publication).get("perception")
    if isinstance(perception, dict):
        text = PerceptionText.from_dict(perception).text
        if text.strip():
            return text.strip()
    report = _report(publication)
    if report.get("status") == "error" and report.get("error"):
        return f"status=error\nerror={report['error']}"
    health = publication.get("health") or "unknown"
    return f"health={health}\n(no perception payload in latest state)"


def health_error(publication: dict[str, object] | None) -> str | None:
    """Why the host is not publishing live frames; None while its latest frame is fresh.

    A stale frame still has a report, and a page may keep showing it.
    """

    if not isinstance(publication, dict):
        return "No frame publication was read."
    health = str(publication.get("health") or "unknown")
    if publication.get("report") is None:
        return f"The frame publication health is {health!r}."
    if health == "stale":
        return (
            f"The latest frame is {publication.get('result_age_ms')} ms old, past its "
            f"{publication.get('stale_after_ms')} ms ceiling."
        )
    if health != "healthy":
        return f"The frame publication health is {health!r}."
    return None


def step_unavailable(publication: dict[str, object] | None, step: str) -> tuple[str, str] | None:
    """A step stream's status and reason when it is not live, or None while it is.

    ``absent``: no completed frame. ``stale``: the latest frame is past its
    ceiling. ``error``: the cycle raised before ``step`` recorded.
    """

    error = health_error(publication)
    if error is not None:
        if not isinstance(publication, dict) or publication.get("report") is None:
            return "absent", error
        return ("stale" if publication.get("health") == "stale" else "error"), error
    report = _report(publication)
    if report.get("status") == "error" and _cycle(publication).get(step) is None:
        return "error", f"The cycle for frame {report.get('frame_id')} raised before {step} recorded: {report.get('error')}"
    return None
