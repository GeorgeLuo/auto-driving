"""A host's completed frame, as the CLI reads it.

``host_publications`` fetches the publication. ``read_frame_publication``
checks the report. The rest turn that report into the text the CLI shows.
"""

from __future__ import annotations

from functools import partial

from autonomy.decision_cycle.activation import activation_presets
from autonomy.decision_cycle.perception.interface import PerceptionText
from autonomy.decision_cycle.timing import step_timing
from autonomy.runtime.frame_loop import FramePublication
from autonomy.runtime.report import HostReport, diagnostic_ceiling
from .publication_fields import PublicationError, require_host_name, require_id, require_mapping


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


def read_frame_publication(publication: FramePublication, *, host_name: str) -> HostReport:
    """The completed report of one frame. A null step, a raised cycle, or a stale age stays visible.

    The envelope was already parsed. This checks that the report is the
    requested host's, that its stale ceiling is the one advertised, and that
    the selection and source are present. Its age and ceiling stay on
    ``publication``.
    """

    host_name = require_host_name(host_name)
    report = publication.report
    if report is None:
        raise _publication_error(
            "missing",
            "Frame publication has no completed report.",
            field="report",
        )
    if report.host_name != host_name:
        raise _publication_error(
            "mismatched",
            f"Frame publication is from host {report.host_name!r}, not {host_name!r}.",
            field="report.host_name",
        )
    try:
        ceiling = diagnostic_ceiling(report.values)
    except ValueError as exc:
        raise _publication_error(
            "incomplete",
            str(exc),
            field="report.values.stale_after_ms",
        ) from exc
    if publication.stale_after_ms != ceiling:
        raise _publication_error(
            "incomplete",
            "Frame publication stale_after_ms does not match the report.",
            field="stale_after_ms",
        )
    _require_activation_steps(report.values)
    _required_id(report.values.get("source_id"), field="report.values.source_id")
    return report


def presets_text(presets: object) -> str:
    """``step=preset`` pairs for each step a runtime reports a preset for."""

    if not isinstance(presets, dict) or not presets:
        return "none"
    return " ".join(f"{step}={preset}" for step, preset in presets.items())


def _steps(report: HostReport | None) -> dict[str, object] | None:
    if report is None:
        return None
    activation = report.values.get("activation")
    steps = activation.get("steps") if isinstance(activation, dict) else None
    return steps if isinstance(steps, dict) else None


def publication_capture(publication: FramePublication) -> tuple[object, ...]:
    """What identifies one frame: its id, capture, completion, and generation."""

    report = publication.report
    if report is None:
        return (None, None, None, None, None, None)
    return (
        report.host_name,
        report.run_id,
        report.frame_id,
        report.timestamp_ms,
        report.published_at_ms,
        report.generation_id,
    )


def publication_presets(publication: FramePublication) -> dict[str, str]:
    """Preset labels carried on the report's activation."""

    steps = _steps(publication.report)
    if steps is None:
        return {}
    return activation_presets(steps)


def perception_duration_ms(publication: FramePublication) -> object:
    """The perception step's duration from the cycle timing, or None without it."""

    report = publication.report
    timing = None if report is None else report.values.get("timing")
    step = step_timing(timing, "perception")
    return None if step is None else step.get("duration_ms")


def perception_text_from_publication(publication: FramePublication) -> str:
    """The perception text rendered from stored signals and things."""

    report = publication.report
    perception = None if report is None else report.cycle.get("perception")
    if isinstance(perception, dict):
        text = PerceptionText.from_dict(perception).text
        if text.strip():
            return text.strip()
    if report is not None and report.status == "error" and report.error:
        return f"status=error\nerror={report.error}"
    return f"health={publication.health}\n(no perception payload in latest state)"


def health_error(publication: FramePublication | None) -> str | None:
    """Why the host is not publishing live frames; None while its latest frame is fresh.

    A stale frame still has a report, and a page may keep showing it.
    """

    if publication is None:
        return "No frame publication was read."
    health = publication.health
    if publication.report is None:
        return f"The frame publication health is {health!r}."
    if health == "stale":
        return (
            f"The latest frame is {publication.result_age_ms} ms old, past its "
            f"{publication.stale_after_ms} ms ceiling."
        )
    if health != "healthy":
        return f"The frame publication health is {health!r}."
    return None


def step_unavailable(publication: FramePublication | None, step: str) -> tuple[str, str] | None:
    """A step stream's status and reason when it is not live, or None while it is.

    ``absent``: no completed frame. ``stale``: the latest frame is past its
    ceiling. ``error``: the cycle raised before ``step`` recorded.
    """

    error = health_error(publication)
    if publication is None or publication.report is None:
        return "absent", error or "No frame publication was read."
    if error is not None:
        return ("stale" if publication.health == "stale" else "error"), error
    report = publication.report
    if report.status == "error" and report.cycle.get(step) is None:
        return "error", (
            f"The cycle for frame {report.frame_id} raised before {step} recorded: {report.error}"
        )
    return None
