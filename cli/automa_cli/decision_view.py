"""Consumer-only decision-view projections for PiRacer host telemetry.

The accepted telemetry contract is additive to the existing decision view.  The
canonical milestone base does not contain PR #202's live decision server, so
this module deliberately stays a pure projection layer: it does not create a
runtime server, rewrite authority, or invent a physical decision publication.
"""

from __future__ import annotations

import html
import json
from typing import Any

from .physical_observation import (
    HOST_TELEMETRY_CAPTURE_SCHEMA,
    HOST_TELEMETRY_PANEL_SCHEMA,
    HOST_TELEMETRY_REASONS,
    build_host_telemetry_capture,
    host_telemetry_failure,
)


class DecisionViewError(ValueError):
    """A stable, fail-closed projection error."""

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.message_text = message
        self.details = details or {}


def _copy_json(value: Any) -> Any:
    try:
        return json.loads(
            json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)
        )
    except (TypeError, ValueError) as exc:
        raise DecisionViewError(
            "field_invalid",
            "Decision view payload is not strict JSON.",
        ) from exc


def project_host_telemetry_panel(
    panel: object,
    *,
    decision_frame_id: str | None = None,
) -> dict[str, Any]:
    """Return a safe host-telemetry panel for a view or capture.

    The panel is intentionally a separate object.  A telemetry payload that
    attempts to supply either authority envelope is rejected rather than being
    silently merged into the decision's authority fields.
    """

    if not isinstance(panel, dict):
        return host_telemetry_failure(
            "schema_invalid",
            message="Host telemetry panel is not an object.",
        )
    if panel.get("schema") != HOST_TELEMETRY_PANEL_SCHEMA:
        return host_telemetry_failure(
            "schema_invalid",
            message="Host telemetry panel schema is invalid.",
        )
    if "authority" in panel or "host_application" in panel:
        return host_telemetry_failure(
            "field_invalid",
            message="Host telemetry panel cannot contain decision authority fields.",
        )
    if decision_frame_id is not None:
        decision = panel.get("decision")
        if isinstance(decision, dict) and decision.get("frame_id") != decision_frame_id:
            return host_telemetry_failure(
                "identity_mismatch",
                message="Host telemetry panel frame does not match the decision frame.",
            )
    try:
        copied = _copy_json(panel)
    except DecisionViewError as exc:
        return host_telemetry_failure(exc.reason, message=exc.message_text)
    if not isinstance(copied, dict):
        raise DecisionViewError("schema_invalid", "Host telemetry panel is not an object.")
    return copied


def project_decision_with_host_telemetry(
    decision_payload: object,
    panel: object,
    *,
    decision_frame_id: str | None = None,
) -> dict[str, Any]:
    """Add a sibling ``host_telemetry`` projection without changing a decision.

    Existing offline/live decision keys, including ``authority``, are copied
    byte-for-byte at the JSON-value level.  The function never derives host
    output from ``authorized_output`` or ``host_application``.
    """

    if not isinstance(decision_payload, dict):
        raise DecisionViewError("schema_invalid", "Decision view payload is not an object.")
    try:
        copied = _copy_json(decision_payload)
    except DecisionViewError:
        raise
    if not isinstance(copied, dict):
        raise DecisionViewError("schema_invalid", "Decision view payload is not an object.")
    frame_id = decision_frame_id
    if frame_id is None:
        frame_id = copied.get("frame_id")
        if frame_id is None and isinstance(copied.get("decision"), dict):
            frame_id = copied["decision"].get("frame_id")
    copied["host_telemetry"] = project_host_telemetry_panel(
        panel,
        decision_frame_id=frame_id if isinstance(frame_id, str) else None,
    )
    return copied


def build_decision_host_telemetry_capture(
    *,
    decision_payload: object,
    panel: object,
    records_result: dict[str, Any] | None = None,
    vehicle_id: str | None = None,
) -> dict[str, Any]:
    """Build a separate capture envelope and retain the original decision.

    ``build_host_telemetry_capture`` is the authority for interval coverage;
    this wrapper adds the unmodified decision payload as a sibling for callers
    preparing an evidence package.
    """

    if not isinstance(decision_payload, dict):
        raise DecisionViewError("schema_invalid", "Decision capture payload is not an object.")
    safe_panel = project_host_telemetry_panel(panel)
    try:
        copied_decision = _copy_json(decision_payload)
    except DecisionViewError:
        raise
    capture = build_host_telemetry_capture(
        joined_point=safe_panel,
        records_result=records_result,
        vehicle_id=vehicle_id,
    )
    return {
        "schema": "automa_physical_decision_capture_v0",
        "decision": copied_decision,
        "host_telemetry": capture,
    }


def render_host_telemetry_panel_html(panel: object) -> str:
    """Render only the additive telemetry panel for a static/live view."""

    safe_panel = project_host_telemetry_panel(panel)
    serialized = html.escape(json.dumps(safe_panel, indent=2, sort_keys=True), quote=True)
    return (
        '<section id="host_telemetry" aria-label="Host telemetry separate observation">'
        "<h2>Host telemetry · separate observation</h2>"
        f"<pre>{serialized}</pre>"
        "</section>"
    )


def unavailable_host_telemetry_panel(
    reason: str = "publisher_missing",
    *,
    message: str | None = None,
) -> dict[str, Any]:
    """Return a valid empty panel for a missing or rejected publisher."""

    if reason not in HOST_TELEMETRY_REASONS:
        reason = "field_invalid"
    return host_telemetry_failure(reason, message=message)


__all__ = [
    "DecisionViewError",
    "HOST_TELEMETRY_CAPTURE_SCHEMA",
    "HOST_TELEMETRY_PANEL_SCHEMA",
    "build_decision_host_telemetry_capture",
    "project_decision_with_host_telemetry",
    "project_host_telemetry_panel",
    "render_host_telemetry_panel_html",
    "unavailable_host_telemetry_panel",
]
