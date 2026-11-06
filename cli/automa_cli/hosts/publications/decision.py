"""Acceptance of a vehicle's published frame.

A runtime host publishes one report per cycle. This module accepts that report
when the envelope names the requested host. A null step or a raised cycle
stays in the report.
"""

from __future__ import annotations

from typing import Any

from autonomy.runtime.report import HostReport

from ..step_activations import bundle_activation_problems
from ..step_formatting import format_activation_problems
from .frame_publication import (
    FramePublicationError,
    read_frame_publication,
)

ERROR_SCHEMA = "host_decision_error"


class DecisionSurfaceError(Exception):
    """Operator/config/input error mapped to exit 2 and stable error codes."""

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        host_name: str | None = None,
        details: dict[str, Any] | None = None,
        exit_code: int = 2,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message_text = message
        self.host_name = host_name
        self.details = details or {}
        self.exit_code = exit_code


def decision_error_payload(
    *,
    error_code: str,
    error: str,
    exit_code: int = 2,
    host_name: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": ERROR_SCHEMA,
        "exit_code": exit_code,
        "error_code": error_code,
        "error": error,
        "host_name": host_name,
        "details": details or {},
    }


def require_valid_activations(
    bundle: dict[str, str],
    *,
    host_name: str,
    steps: tuple[str, ...],
) -> None:
    """Refuse invalid staged documents with the command that replaces them."""

    problems = bundle_activation_problems(bundle, host_name, steps=steps)
    if problems:
        raise DecisionSurfaceError(
            "activation_invalid",
            format_activation_problems(problems),
            host_name=host_name,
            details={"activation_problems": problems},
        )


def accept_decision_publication(
    publication: object,
    *,
    host_name: str,
    now_ms: int,
    max_age_ms: int | None = None,
) -> dict[str, Any]:
    """Normalize and accept a host's decision publication without fabricating local state."""

    try:
        return read_frame_publication(
            publication,
            host_name=host_name,
            now_ms=now_ms,
            max_age_ms=max_age_ms,
        )
    except FramePublicationError as exc:
        raise DecisionSurfaceError(
            "decision_publication_unavailable",
            exc.message_text,
            host_name=host_name,
            details={"reason": exc.reason, **exc.details},
        ) from exc
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "decision_publication_unavailable",
            f"Decision publication is invalid: {exc}",
            host_name=host_name,
            details={"reason": "incomplete"},
        ) from exc


def decision_view_frame(normalized: dict[str, Any]) -> HostReport:
    """The accepted report, already decoded with its publication."""

    report = normalized.get("report")
    if not isinstance(report, HostReport):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "Decision view requires a vehicle report.",
        )
    return report
