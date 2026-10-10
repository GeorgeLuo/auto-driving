"""Acceptance of a vehicle's published decision cycle.

A runtime host publishes one report per cycle with the proposal, plan, and
action records. ``decision_steps`` streams each step's record from it; this
module decodes the report strictly and accepts it only when its records,
aggregates, and generation agree with the step activations it names.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from autonomy.decision_cycle.plan.values import (
    ACTION_PLAN_SCHEMA,
    SELECTOR_ID,
    ActionPlan,
    PlanContribution,
)
from autonomy.decision_cycle.proposal.values import (
    ACTION_PROPOSAL_SCHEMA,
    PROPOSED_VEHICLE_COMMAND_SCHEMA,
    ActionProposal,
    ProposedVehicleCommand,
)
from autonomy.decision_cycle.proposal.inputs import (
    DECISION_DATA_SOURCE_SCHEMA,
    ComponentEnvelope,
    DecisionDataSource,
)
from autonomy.decision_cycle.action.hold import (
    HoldAction,
    idle_output,
)
from autonomy.decision_cycle.action.values import (
    AUTHORITY_RESULT_SCHEMA,
    AuthorityResult,
    control_output,
)
from autonomy.decision_cycle.action.result import (
    ACTION_RESULT_SCHEMA,
    ActionResult,
)
from autonomy.decision_cycle.activation import DECISION_STEPS, activation_generation_id
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.plan.runner import PlanRunner
from autonomy.decision_cycle.proposal.result import PROPOSAL_RESULT_SCHEMA, ProposalResult
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.action_identifiers import (
    require_ascii_id,
)
from autonomy.decision_cycle.memory.evidence import RetainedEvidence
from autonomy.serialization import canonical_json_utf8
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.report import (
    REPORT_SCHEMA,
)

from .decision_records import DecisionRecords, activations_from_payloads
from .step_activations import (
    bundle_activation_problems,
    format_activation_problems,
    proposal_plugin_ids,
)
from .host_publications import (
    DecisionPublicationError,
    normalize_decision_publication,
)

ERROR_SCHEMA = "vehicle_decision_error_v0"
# A decision frame's cycle holds the proposal, plan, and action step records.
CYCLE_EXACT_KEYS = frozenset({"proposal", "plan", "action"})
PROPOSAL_RECORD_EXACT_KEYS = frozenset(
    {"schema", "frame_id", "status", "reason", "source", "candidates"}
)
ACTION_RECORD_EXACT_KEYS = frozenset({"schema", "frame_id", "status", "reason", "authority"})
AUTHORITY_EXACT_KEYS = frozenset(
    {
        "schema",
        "frame_id",
        "proposed",
        "authorized_output",
        "proposed_applied",
        "host_application",
        "proposed_equals_authorized",
        "cycle_status",
        "cycle_reason",
        "gate_id",
        "drive_mode_gate",
    }
)
PLAN_EXACT_KEYS = frozenset(
    {
        "schema",
        "plan_id",
        "frame_id",
        "timestamp_ms",
        "status",
        "selected_proposal_id",
        "contributions",
        "candidates",
        "selector_id",
        "metadata",
    }
)
SOURCE_EXACT_KEYS = frozenset(
    {
        "schema",
        "source_id",
        "frame_id",
        "frame_index",
        "timestamp_ms",
        "observation",
        "evidence",
        "capabilities",
        "prior_host_applied_command",
        "metadata",
    }
)
ENVELOPE_EXACT_KEYS = frozenset({"status", "value", "reason", "updated_at_ms"})
PROPOSAL_EXACT_KEYS = frozenset(
    {
        "schema",
        "proposal_id",
        "plugin_id",
        "frame_id",
        "lifecycle",
        "freshness",
        "confidence",
        "reason",
        "command",
        "assumptions",
        "source_refs",
        "available",
        "metadata",
    }
)
CONTRIBUTION_EXACT_KEYS = frozenset({"proposal_id", "plugin_id", "weight", "role"})
SOURCE_REF_EXACT_KEYS = frozenset(
    {"kind", "id", "frame_id", "observation_id", "plugin_id", "note"}
)
COMMAND_EXACT_KEYS = frozenset(
    {"schema", "steering", "throttle", "gear", "normalized"}
)


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


class DecisionSurfaceError(Exception):
    """Operator/config/input error mapped to exit 2 and stable error codes."""

    def __init__(
        self,
        error: str,
        message: str,
        *,
        vehicle_id: str | None = None,
        details: dict[str, Any] | None = None,
        exit_code: int = 2,
    ) -> None:
        super().__init__(message)
        self.error = error
        self.message_text = message
        self.vehicle_id = vehicle_id
        self.details = details or {}
        self.exit_code = exit_code


def decision_error_payload(
    *,
    error: str,
    message: str,
    exit_code: int = 2,
    vehicle_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": ERROR_SCHEMA,
        "exit_code": exit_code,
        "error": error,
        "message": message,
        "vehicle_id": vehicle_id,
        "details": details or {},
    }


def _error_result(
    exc: DecisionSurfaceError,
    *,
    json_output: bool,
) -> CommandResult:
    if json_output:
        return CommandResult(
            exc.exit_code,
            json.dumps(
                decision_error_payload(
                    error=exc.error,
                    message=exc.message_text,
                    exit_code=exc.exit_code,
                    vehicle_id=exc.vehicle_id,
                    details=exc.details,
                ),
                indent=2,
                sort_keys=True,
            ),
        )
    return CommandResult(exc.exit_code, exc.message_text)


def _require_valid_activations(
    bundle: dict[str, str],
    *,
    vehicle_id: str,
    steps: tuple[str, ...],
) -> None:
    """Refuse invalid staged documents with each owning step's restage command."""

    problems = bundle_activation_problems(bundle, vehicle_id, steps=steps)
    if problems:
        raise DecisionSurfaceError(
            "activation_invalid",
            format_activation_problems(problems),
            vehicle_id=vehicle_id,
            details={"activation_problems": problems},
        )


def _require_identity_steps(identity: object, *, error: str) -> dict[str, Any]:
    """The decision steps of an identity; its generation ID must match their content."""

    if not isinstance(identity, dict) or not isinstance(identity.get("steps"), dict):
        raise DecisionSurfaceError(
            error,
            "The decision identity names no proposal, plan, and action steps.",
        )
    steps = identity["steps"]
    if set(steps) != set(DECISION_STEPS):
        raise DecisionSurfaceError(
            "activation_invalid" if error == "activation_missing" else error,
            f"Decision identity must name exactly the steps {list(DECISION_STEPS)!r}.",
        )
    if steps.get("proposal") is None:
        raise DecisionSurfaceError(
            error,
            "The decision identity has no proposal step. "
            "Run: ./cli/automa vehicles update proposal --id <vehicle>",
        )
    try:
        expected = activation_generation_id(activations_from_payloads(steps), prefix="decision")
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "activation_invalid" if error == "activation_missing" else error,
            f"Decision step activations are invalid: {exc}",
        ) from exc
    if identity.get("generation_id") != expected:
        raise DecisionSurfaceError(
            "activation_invalid" if error == "activation_missing" else error,
            "Decision generation_id does not match its step activations.",
        )
    return steps


def _require_report_cycle_alignment(
    report: dict[str, Any],
    activation: dict[str, Any],
) -> None:
    """The same typed cycle and staged-step checks for every report transport."""

    steps = _require_identity_steps(activation, error="latest_frame_invalid")
    cycle = _require_exact_cycle_export(report["cycle"])
    _require_runner_plan_alignment(cycle, steps)
    _require_aggregate_cycle_alignment(report, cycle)
    if report["generation_id"] != activation["generation_id"]:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "Report generation does not match its step activations.",
        )


def accept_decision_publication(
    publication: object,
    *,
    vehicle_id: str,
    now_ms: int,
    max_age_ms: int | None = None,
) -> dict[str, Any]:
    """Normalize and accept a host's decision publication without fabricating local state."""

    try:
        normalized = normalize_decision_publication(
            publication,
            vehicle_id=vehicle_id,
            now_ms=now_ms,
            max_age_ms=max_age_ms,
        )
    except DecisionPublicationError as exc:
        raise DecisionSurfaceError(
            "decision_publication_unavailable",
            exc.message_text,
            vehicle_id=vehicle_id,
            details={"reason": exc.reason, **exc.details},
        ) from exc
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "decision_publication_unavailable",
            f"Decision publication is invalid: {exc}",
            vehicle_id=vehicle_id,
            details={"reason": "incomplete"},
        ) from exc

    decision = normalized["decision"]
    try:
        _require_report_cycle_alignment(decision, decision["values"]["activation"])
    except DecisionSurfaceError as exc:
        raise DecisionSurfaceError(
            "decision_publication_unavailable",
            f"Decision publication is invalid: {exc.message_text}",
            vehicle_id=vehicle_id,
            details={"reason": "mismatched", "source_error": exc.error, **exc.details},
        ) from exc
    return normalized


def _json_ready(value: Any) -> Any:
    """Recursively convert tuples to lists so canonical JSON encoding is possible."""

    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    return value


def _require_exact_keys(
    payload: dict[str, Any],
    exact: frozenset[str],
    *,
    field: str,
) -> None:
    keys = set(payload.keys())
    if keys != set(exact):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} key set is not exact export.",
            details={
                "field": field,
                "expected": sorted(exact),
                "got": sorted(keys),
                "extra": sorted(keys - set(exact)),
                "missing": sorted(set(exact) - keys),
            },
        )


def _require_canonical_export_equal(
    payload: object,
    exported: object,
    *,
    field: str,
) -> None:
    try:
        if canonical_json_utf8(_json_ready(payload)) != canonical_json_utf8(
            _json_ready(exported)
        ):
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                f"{field} is not a complete lossless typed export.",
                details={"field": field},
            )
    except ValueError as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} is not strictly JSON-serializable: {exc}",
            details={"field": field},
        ) from exc


def _strict_decode_command(payload: object, *, field: str) -> ProposedVehicleCommand:
    if not isinstance(payload, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} must be a ProposedVehicleCommand object.",
            details={"field": field},
        )
    _require_exact_keys(payload, COMMAND_EXACT_KEYS, field=field)
    if payload.get("schema") != PROPOSED_VEHICLE_COMMAND_SCHEMA:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field}.schema must be {PROPOSED_VEHICLE_COMMAND_SCHEMA!r}.",
            details={"field": field},
        )
    try:
        command = ProposedVehicleCommand.from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} failed ProposedVehicleCommand construction: {exc}",
            details={"field": field},
        ) from exc
    _require_canonical_export_equal(payload, command.to_dict(), field=field)
    return command


def _strict_decode_envelope(payload: object, *, field: str) -> ComponentEnvelope:
    if not isinstance(payload, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} must be a ComponentEnvelope object.",
            details={"field": field},
        )
    _require_exact_keys(payload, ENVELOPE_EXACT_KEYS, field=field)
    try:
        envelope = ComponentEnvelope(
            status=payload["status"],
            value=payload.get("value"),
            reason=payload.get("reason") or "",
            updated_at_ms=payload.get("updated_at_ms") or 0,
        )
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} failed ComponentEnvelope construction: {exc}",
            details={"field": field},
        ) from exc
    _require_canonical_export_equal(payload, envelope.to_dict(), field=field)
    return envelope


def _strict_decode_proposal(payload: object, *, field: str) -> ActionProposal:
    if not isinstance(payload, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} must be an ActionProposal object.",
            details={"field": field},
        )
    _require_exact_keys(payload, PROPOSAL_EXACT_KEYS, field=field)
    if payload.get("schema") != ACTION_PROPOSAL_SCHEMA:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field}.schema must be {ACTION_PROPOSAL_SCHEMA!r}.",
            details={"field": field},
        )
    command = payload.get("command")
    if command is not None:
        _strict_decode_command(command, field=f"{field}.command")
    refs = payload.get("source_refs")
    if not isinstance(refs, list):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field}.source_refs must be a list.",
            details={"field": field},
        )
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict):
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                f"{field}.source_refs[{index}] must be an object.",
            )
        _require_exact_keys(
            ref,
            SOURCE_REF_EXACT_KEYS,
            field=f"{field}.source_refs[{index}]",
        )
    try:
        proposal = ActionProposal.from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} failed ActionProposal construction: {exc}",
            details={"field": field},
        ) from exc
    _require_canonical_export_equal(payload, proposal.to_dict(), field=field)
    return proposal


def _strict_decode_plan(payload: object, *, field: str) -> ActionPlan:
    if not isinstance(payload, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} must be an ActionPlan object.",
            details={"field": field},
        )
    _require_exact_keys(payload, PLAN_EXACT_KEYS, field=field)
    if payload.get("schema") != ACTION_PLAN_SCHEMA:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field}.schema must be {ACTION_PLAN_SCHEMA!r}.",
            details={"field": field},
        )
    candidates_raw = payload.get("candidates")
    if not isinstance(candidates_raw, list):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field}.candidates must be a list.",
        )
    candidates = tuple(
        _strict_decode_proposal(item, field=f"{field}.candidates[{index}]")
        for index, item in enumerate(candidates_raw)
    )
    contributions_raw = payload.get("contributions")
    if not isinstance(contributions_raw, list):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field}.contributions must be a list.",
        )
    contributions: list[PlanContribution] = []
    for index, item in enumerate(contributions_raw):
        if not isinstance(item, dict):
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                f"{field}.contributions[{index}] must be an object.",
            )
        _require_exact_keys(
            item,
            CONTRIBUTION_EXACT_KEYS,
            field=f"{field}.contributions[{index}]",
        )
        contributions.append(
            PlanContribution(
                proposal_id=str(item["proposal_id"]),
                plugin_id=str(item["plugin_id"]),
                weight=float(item["weight"]),
                role=str(item["role"]),
            )
        )
    try:
        plan = ActionPlan(
            frame_id=str(payload["frame_id"]),
            timestamp_ms=payload["timestamp_ms"],
            status=str(payload["status"]),
            candidates=candidates,
            selected_proposal_id=payload.get("selected_proposal_id"),
            contributions=tuple(contributions),
            selector_id=str(payload.get("selector_id") or SELECTOR_ID),
            metadata=dict(payload.get("metadata") or {}),
            plan_id=str(payload.get("plan_id") or ""),
            schema=str(payload.get("schema") or ACTION_PLAN_SCHEMA),
        )
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} failed ActionPlan construction: {exc}",
            details={"field": field},
        ) from exc
    _require_canonical_export_equal(payload, plan.to_dict(), field=field)
    return plan


def _strict_decode_authority(payload: object, *, field: str) -> AuthorityResult:
    if not isinstance(payload, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} must be an AuthorityResult object.",
            details={"field": field},
        )
    _require_exact_keys(payload, AUTHORITY_EXACT_KEYS, field=field)
    if payload.get("schema") != AUTHORITY_RESULT_SCHEMA:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field}.schema must be {AUTHORITY_RESULT_SCHEMA!r}.",
            details={"field": field},
        )
    if "applied_control" in payload:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} must not include applied_control.",
            details={"field": field},
        )
    # The deciding action plugin; the staged-activation check names which one.
    gate_id = payload.get("gate_id")
    try:
        require_ascii_id(gate_id, field_name="gate_id")
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field}.gate_id must be an action plugin id: {exc}",
            details={"field": f"{field}.gate_id"},
        ) from exc
    if type(payload.get("proposed_applied")) is not bool:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field}.proposed_applied must be a bool.",
            details={"field": field},
        )
    authorized_output = payload.get("authorized_output")
    if gate_id == HoldAction.plugin_id:
        if payload.get("proposed_applied") is not False:
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                f"{field}.proposed_applied must be false for the hold gate.",
                details={"field": field},
            )
        # The hold gate's authorized output is the exact idle command.
        _require_canonical_export_equal(
            authorized_output,
            idle_output(),
            field=f"{field}.authorized_output",
        )
    else:
        try:
            control = AutonomyControl(**authorized_output)
        except TypeError as exc:
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                f"{field}.authorized_output is not a control output: {exc}",
                details={"field": f"{field}.authorized_output"},
            ) from exc
        _require_canonical_export_equal(
            authorized_output,
            control_output(control),
            field=f"{field}.authorized_output",
        )
    proposed_payload = payload.get("proposed")
    proposed: ProposedVehicleCommand | None
    if proposed_payload is None:
        proposed = None
    else:
        proposed = _strict_decode_command(
            proposed_payload, field=f"{field}.proposed"
        )
    host = _strict_decode_envelope(
        payload.get("host_application"),
        field=f"{field}.host_application",
    )
    try:
        authority = AuthorityResult(
            frame_id=str(payload["frame_id"]),
            gate_id=str(gate_id),
            proposed=proposed,
            cycle_status=str(payload["cycle_status"]),
            cycle_reason=str(payload.get("cycle_reason") or ""),
            authorized_output=dict(authorized_output),
            host_application=host,
            drive_mode_gate=str(payload.get("drive_mode_gate") or "unknown"),
            proposed_applied=payload["proposed_applied"],
            schema=str(payload.get("schema") or AUTHORITY_RESULT_SCHEMA),
        )
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} failed AuthorityResult construction: {exc}",
            details={"field": field},
        ) from exc
    _require_canonical_export_equal(payload, authority.to_dict(), field=field)
    return authority


def _strict_decode_source_envelope(
    payload: object,
    *,
    component: str,
    field: str,
) -> ComponentEnvelope:
    """Decode one source envelope, hydrating observation/memory typed values."""

    if not isinstance(payload, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} must be a ComponentEnvelope object.",
            details={"field": field},
        )
    _require_exact_keys(payload, ENVELOPE_EXACT_KEYS, field=field)
    status = payload.get("status")
    value = payload.get("value")
    reason = payload.get("reason") or ""
    updated_at_ms = payload.get("updated_at_ms") or 0
    if status == "ready":
        if component == "observation":
            if not isinstance(value, dict):
                raise DecisionSurfaceError(
                    "latest_frame_invalid",
                    f"{field}.value must be an Observation export object when ready.",
                    details={"field": field},
                )
            try:
                typed = Observation.from_dict(value)
            except (TypeError, ValueError) as exc:
                raise DecisionSurfaceError(
                    "latest_frame_invalid",
                    f"{field}.value failed Observation construction: {exc}",
                    details={"field": field},
                ) from exc
            _require_canonical_export_equal(
                value, typed.to_dict(), field=f"{field}.value"
            )
            value = typed
        elif component == "evidence":
            if not isinstance(value, list):
                raise DecisionSurfaceError(
                    "latest_frame_invalid",
                    f"{field}.value must be a list of RetainedEvidence exports when ready.",
                    details={"field": field},
                )
            try:
                typed = tuple(RetainedEvidence.from_dict(item) for item in value)
            except (AttributeError, TypeError, ValueError) as exc:
                raise DecisionSurfaceError(
                    "latest_frame_invalid",
                    f"{field}.value failed RetainedEvidence construction: {exc}",
                    details={"field": field},
                ) from exc
            _require_canonical_export_equal(
                value, [record.to_dict() for record in typed], field=f"{field}.value"
            )
            value = typed
    try:
        envelope = ComponentEnvelope(
            status=status,
            value=value,
            reason=reason,
            updated_at_ms=updated_at_ms,
        )
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} failed ComponentEnvelope construction: {exc}",
            details={"field": field},
        ) from exc
    # Compare against the original export shape (dict values, not typed objects).
    _require_canonical_export_equal(payload, envelope.to_dict(), field=field)
    return envelope


def _strict_decode_source(payload: object, *, field: str) -> DecisionDataSource:
    if not isinstance(payload, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} must be a DecisionDataSource object.",
            details={"field": field},
        )
    _require_exact_keys(payload, SOURCE_EXACT_KEYS, field=field)
    if payload.get("schema") != DECISION_DATA_SOURCE_SCHEMA:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field}.schema must be {DECISION_DATA_SOURCE_SCHEMA!r}.",
            details={"field": field},
        )
    envelopes: dict[str, ComponentEnvelope] = {}
    for env_key in (
        "observation",
        "evidence",
        "capabilities",
        "prior_host_applied_command",
    ):
        envelopes[env_key] = _strict_decode_source_envelope(
            payload.get(env_key),
            component=env_key,
            field=f"{field}.{env_key}",
        )
    try:
        source = DecisionDataSource(
            frame_id=str(payload["frame_id"]),
            frame_index=payload["frame_index"],
            timestamp_ms=payload["timestamp_ms"],
            observation=envelopes["observation"],
            evidence=envelopes["evidence"],
            capabilities=envelopes["capabilities"],
            prior_host_applied_command=envelopes["prior_host_applied_command"],
            metadata=dict(payload.get("metadata") or {}),
            schema=str(payload.get("schema") or DECISION_DATA_SOURCE_SCHEMA),
            source_id=str(payload.get("source_id") or ""),
        )
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} failed DecisionDataSource construction: {exc}",
            details={"field": field},
        ) from exc
    _require_canonical_export_equal(payload, source.to_dict(), field=field)
    return source


def _require_exact_cycle_export(cycle: dict[str, Any]) -> DecisionRecords:
    """Strict reconstruction + full canonical export equality for the cycle.

    One owning boundary: reconstruct the typed proposal, plan, and action
    records (source, candidates, plan, authority) and require lossless
    ``to_dict()`` equality. Adjacent nested authority/command/mode tampering
    is rejected as a class. Returns the records for alignment checks.
    """

    _require_exact_keys(cycle, CYCLE_EXACT_KEYS, field="cycle")
    proposal_payload = cycle.get("proposal")
    action_payload = cycle.get("action")
    if not isinstance(proposal_payload, dict) or not isinstance(action_payload, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "cycle.proposal and cycle.action must be objects.",
            details={"field": "cycle"},
        )
    _require_exact_keys(proposal_payload, PROPOSAL_RECORD_EXACT_KEYS, field="cycle.proposal")
    _require_exact_keys(action_payload, ACTION_RECORD_EXACT_KEYS, field="cycle.action")
    for field, payload, schema in (
        ("cycle.proposal", proposal_payload, PROPOSAL_RESULT_SCHEMA),
        ("cycle.action", action_payload, ACTION_RESULT_SCHEMA),
    ):
        if payload.get("schema") != schema:
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                f"{field}.schema must be {schema!r}.",
                details={"field": f"{field}.schema"},
            )
        if payload.get("status") not in {"ok", "error"}:
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                f"{field}.status must be ok or error.",
            )
        if type(payload.get("reason")) is not str:
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                f"{field}.reason must be a string.",
            )

    authority = _strict_decode_authority(
        action_payload.get("authority"), field="cycle.action.authority"
    )
    plan_payload = cycle.get("plan")
    plan = None if plan_payload is None else _strict_decode_plan(plan_payload, field="cycle.plan")
    source_payload = proposal_payload.get("source")
    source = (
        None
        if source_payload is None
        else _strict_decode_source(source_payload, field="cycle.proposal.source")
    )
    raw_candidates = proposal_payload.get("candidates")
    if not isinstance(raw_candidates, list):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "cycle.proposal.candidates must be a list.",
            details={"field": "cycle.proposal.candidates"},
        )
    candidates = [
        _strict_decode_proposal(item, field=f"cycle.proposal.candidates[{index}]")
        for index, item in enumerate(raw_candidates)
    ]

    try:
        reconstructed = DecisionRecords(
            proposal=ProposalResult(
                frame_id=str(proposal_payload["frame_id"]),
                status=str(proposal_payload["status"]),
                reason=str(proposal_payload["reason"]),
                source=source,
                candidates=tuple(candidates),
                schema=str(proposal_payload["schema"]),
            ),
            plan=plan,
            action=ActionResult(
                frame_id=str(action_payload["frame_id"]),
                status=str(action_payload["status"]),
                reason=str(action_payload["reason"]),
                authority=authority,
                schema=str(action_payload["schema"]),
            ),
        )
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"cycle failed record construction: {exc}",
            details={"field": "cycle"},
        ) from exc
    _require_canonical_export_equal(cycle, reconstructed.to_dict(), field="cycle")
    return reconstructed


def _require_aggregate_cycle_alignment(
    frame: dict[str, Any],
    cycle: DecisionRecords,
) -> None:
    """Enforce cross-object cycle alignment the nested constructors do not own.

    - proposal / plan / action / authority / source share one frame_id
    - the plan is over exactly the proposal step's candidates
    - plan/source timestamps agree when both present
    - report timing agrees with the cycle source when source construction succeeds
    - authority.proposed is the selected plan command (or null for idle/error)
    """

    frame_id = cycle.frame_id
    if frame.get("frame_id") != frame_id:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "stream frame_id must equal cycle.frame_id.",
            details={"field": "frame_id"},
        )
    if cycle.authority.frame_id != frame_id:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "authority.frame_id must equal cycle.frame_id.",
            details={"field": "cycle.action.authority.frame_id"},
        )
    if cycle.proposal.frame_id != frame_id:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "proposal.frame_id must equal cycle.frame_id.",
            details={"field": "cycle.proposal.frame_id"},
        )
    if cycle.proposal.status != "ok" and cycle.status != "error":
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "A failed proposal step must end the action with status error.",
            details={"field": "cycle.action.status"},
        )
    if cycle.plan is not None and sorted(c.proposal_id for c in cycle.plan.candidates) != sorted(
        c.proposal_id for c in cycle.proposal.candidates
    ):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "plan candidates must be the proposal step's candidates.",
            details={"field": "cycle.plan.candidates"},
        )

    plan = cycle.plan
    source = cycle.source
    if plan is not None and plan.frame_id != frame_id:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "plan.frame_id must equal cycle.frame_id.",
            details={"field": "cycle.plan.frame_id"},
        )
    if source is not None and source.frame_id != frame_id:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "source.frame_id must equal cycle.frame_id.",
            details={"field": "cycle.proposal.source.frame_id"},
        )
    if plan is not None and source is not None:
        if plan.timestamp_ms != source.timestamp_ms:
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                "plan.timestamp_ms must equal source.timestamp_ms.",
                details={"field": "cycle.plan.timestamp_ms"},
            )

    # A failed source construction has no nested timing identity. The report
    # still names the host frame that failed; never replace it with zeroes.
    if source is None:
        if cycle.proposal.status != "error" or plan is not None or cycle.proposal.candidates:
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                "A missing source requires a failed proposal with no candidates or plan.",
                details={"field": "cycle.proposal.source"},
            )
    else:
        for field, expected in (
            ("frame_index", source.frame_index),
            ("timestamp_ms", source.timestamp_ms),
        ):
            if frame.get(field) != expected:
                raise DecisionSurfaceError(
                    "latest_frame_invalid",
                    f"Report {field} must match cycle source {field}.",
                    details={"field": field, "expected": expected, "got": frame.get(field)},
                )

    # authority.proposed must be the selected plan command (detached equal value).
    proposed = cycle.authority.proposed
    if cycle.status == "error" or plan is None or plan.status == "idle":
        if proposed is not None:
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                "authority.proposed must be null when plan is idle/absent or the cycle failed.",
                details={"field": "cycle.action.authority.proposed"},
            )
        return

    selected = plan.selected_candidate()
    if selected is None:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "selected plan is missing the selected candidate.",
            details={"field": "cycle.plan.selected_proposal_id"},
        )
    selected_command = selected.command
    if selected_command is None:
        if proposed is not None:
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                "authority.proposed must be null when the selected candidate has no command.",
                details={"field": "cycle.action.authority.proposed"},
            )
        return
    if proposed is None:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "authority.proposed must equal the selected plan command.",
            details={"field": "cycle.action.authority.proposed"},
        )
    try:
        if canonical_json_utf8(_json_ready(proposed.to_dict())) != canonical_json_utf8(
            _json_ready(selected_command.to_dict())
        ):
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                "authority.proposed must equal the selected plan command.",
                details={"field": "cycle.action.authority.proposed"},
            )
    except ValueError as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"authority.proposed/selected command are not strictly JSON-serializable: {exc}",
            details={"field": "cycle.action.authority.proposed"},
        ) from exc


def _require_runner_plan_alignment(
    cycle: DecisionRecords,
    steps: Mapping[str, Any],
) -> None:
    """Enforce the staged action plugin, candidate membership, and plan output."""

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
    try:
        plan_step = PlanRunner.from_activation(
            activations_from_payloads(dict(steps))["plan"]
        )
        expected = plan_step(
            DecisionFrameContext(
                frame_id=plan.frame_id, frame_index=0, timestamp_ms=plan.timestamp_ms
            ),
            cycle.proposal,
        )
    except (TypeError, ValueError, ImportError, AttributeError) as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"cycle.plan failed staged plan validation: {exc}",
            details={"field": "cycle.plan"},
        ) from exc
    if expected is None:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"cycle.plan failed staged plan validation: {plan_step.last_error}",
            details={"field": "cycle.plan"},
        )
    _require_canonical_export_equal(
        plan.to_dict(),
        expected.to_dict(),
        field="cycle.plan.selector_result",
    )


def decision_view_frame(normalized: dict[str, Any]) -> dict[str, Any]:
    """The accepted decision publication is the shared vehicle report."""

    decision = normalized["decision"]
    if not isinstance(decision, dict) or decision.get("schema") != REPORT_SCHEMA:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "Decision view requires a vehicle report.",
        )
    return deepcopy(decision)
