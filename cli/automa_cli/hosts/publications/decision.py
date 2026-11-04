"""Acceptance of a vehicle's published decision cycle.

A runtime host publishes one report per cycle. Autonomy decodes its proposal,
plan, and action records. This module accepts the report when those records
and its generation agree with the step activations it names.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from autonomy.decision_cycle.activation import DECISION_STEPS, activation_generation_id
from autonomy.runtime.report import HostReport

from ..step_activations import bundle_activation_problems, proposal_plugin_ids
from ..step_formatting import format_activation_problems
from .decision_publication import (
    DecisionPublicationError,
    normalize_decision_publication,
)
from .decision_records import DecisionRecords, activations_from_payloads

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


def _require_identity_steps(identity: object, *, error_code: str) -> dict[str, Any]:
    """The decision steps of an identity; its generation ID must match their content."""

    if not isinstance(identity, dict) or not isinstance(identity.get("steps"), dict):
        raise DecisionSurfaceError(
            error_code,
            "The decision identity names no proposal, plan, and action steps.",
        )
    steps = identity["steps"]
    if set(steps) != set(DECISION_STEPS):
        raise DecisionSurfaceError(
            "activation_invalid" if error_code == "activation_missing" else error_code,
            f"Decision identity must name exactly the steps {list(DECISION_STEPS)!r}.",
        )
    if steps.get("proposal") is None:
        raise DecisionSurfaceError(
            error_code,
            "The decision identity has no proposal step. "
            "Arm one with: ./cli/automa plugins arm --host-name <host> --name proposal/<plugin>",
        )
    try:
        expected = activation_generation_id(activations_from_payloads(steps), prefix="decision")
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "activation_invalid" if error_code == "activation_missing" else error_code,
            f"Decision step activations are invalid: {exc}",
        ) from exc
    if identity.get("generation_id") != expected:
        raise DecisionSurfaceError(
            "activation_invalid" if error_code == "activation_missing" else error_code,
            "Decision generation_id does not match its step activations.",
        )
    return steps


def _require_report_cycle_alignment(report: HostReport) -> None:
    """The same typed cycle and staged-step checks for every report transport."""

    activation = report.values.get("activation")
    steps = _require_identity_steps(activation, error_code="latest_frame_invalid")
    try:
        decoded = report.decode_records()
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError("latest_frame_invalid", str(exc)) from exc
    cycle = DecisionRecords(proposal=decoded.proposal, plan=decoded.plan, action=decoded.action)
    _require_runner_plan_alignment(cycle, steps)
    if not isinstance(activation, dict) or report.generation_id != activation.get("generation_id"):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "Report generation does not match its step activations.",
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
        normalized = normalize_decision_publication(
            publication,
            host_name=host_name,
            now_ms=now_ms,
            max_age_ms=max_age_ms,
        )
    except DecisionPublicationError as exc:
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

    try:
        _require_report_cycle_alignment(normalized["report"])
    except DecisionSurfaceError as exc:
        raise DecisionSurfaceError(
            "decision_publication_unavailable",
            f"Decision publication is invalid: {exc.message_text}",
            host_name=host_name,
            details={"reason": "mismatched", "source_error_code": exc.error_code, **exc.details},
        ) from exc
    return normalized


def _require_runner_plan_alignment(
    cycle: DecisionRecords,
    steps: Mapping[str, Any],
) -> None:
    """Enforce the staged action plugin and the plan's candidate membership."""

    action_plugins = (steps.get("action") or {}).get("plugins") or []
    expected_gate = action_plugins[0] if len(action_plugins) == 1 else None
    if cycle.authority.gate_id != expected_gate:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"cycles must come from the staged {expected_gate!r} action plugin.",
            details={
                "field": "cycle.action.authority.gate_id",
                "expected_gate_id": expected_gate,
                "got_gate_id": cycle.authority.gate_id,
            },
        )
    if cycle.status == "error":
        if cycle.plan is not None and cycle.proposal.status != "ok":
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                "a failed proposal step must not be followed by a plan.",
                details={"field": "cycle.plan"},
            )
        return

    plan = cycle.plan
    if plan is None:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "ok cycle must include an action plan.",
            details={"field": "cycle.plan"},
        )
    expected_plugins = tuple(sorted(proposal_plugin_ids(dict(steps))))
    actual_plugins = tuple(candidate.plugin_id for candidate in plan.candidates)
    if actual_plugins != expected_plugins:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "cycle.plan candidates must match the staged proposal plugins exactly.",
            details={
                "field": "cycle.plan.candidates",
                "expected_plugins": list(expected_plugins),
                "got_plugins": list(actual_plugins),
            },
        )


def decision_view_frame(normalized: dict[str, Any]) -> HostReport:
    """The accepted report, already decoded with its publication."""

    report = normalized.get("report")
    if not isinstance(report, HostReport):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "Decision view requires a vehicle report.",
        )
    return report
