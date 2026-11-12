"""Steer-away proposal from retained obstruction evidence.

Reads retained evidence from shared memory at ``evidence_key`` (by default
the key ``bounded_evidence`` publishes). It accepts records by kind and
location, regardless of which perception or memory implementation produced
them.
"""

from __future__ import annotations

import math
from typing import Sequence, cast

from autonomy.decision_cycle.confidence import confidence_rank
from autonomy.decision_cycle.proposal.values import (
    ActionProposal,
    ProposedVehicleCommand,
    SourceRef,
)
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.evidence import RetainedEvidence
from autonomy.decision_cycle.action_identifiers import require_ascii_id
from autonomy.decision_cycle.memory.publication import EVIDENCE_KEY

DEFAULT_ACCEPTED_KINDS = ("floor_boundary", "obstacle", "obstruction_evidence")
DEFAULT_RETAINED_MAX_AGE_MS = 1000
DEFAULT_STEER_MAGNITUDE = 1.0
DEFAULT_FORWARD_THROTTLE = 0.60
BASE_ASSUMPTIONS = (
    "no_object_identity",
    "image_relative_only",
    "single_primary_record",
)


def _bbox_mid_x(record: RetainedEvidence) -> float | None:
    location = record.location
    if location is None:
        return None
    bbox = location.bbox_xyxy_norm
    if bbox is None or len(bbox) != 4:
        return None
    try:
        mid_x = (float(bbox[0]) + float(bbox[2])) / 2.0
    except (TypeError, ValueError):
        return None
    if not math.isfinite(mid_x):
        return None
    return mid_x


# Zones that mean "no explicit left/right" so bbox mid_x may supply the cue.
# "unknown" is ViewLocation.from_dict's canonical missing-zone value.
_BBOX_FALLBACK_ZONES = frozenset({None, "", "center", "unknown"})
_IMAGE_LATERAL_ZONES = {
    "left": "left",
    "right": "right",
    "near_left": "left",
    "mid_left": "left",
    "far_left": "left",
    "near_right": "right",
    "mid_right": "right",
    "far_right": "right",
}


def _explicit_lateral_side(record: RetainedEvidence) -> str | None:
    """Resolve the horizontal side from an image-relative zone, if present."""

    location = record.location
    if location is None:
        return None
    return _IMAGE_LATERAL_ZONES.get(location.zone)


def _has_lateral_cue(record: RetainedEvidence) -> bool:
    """True when a lateral zone or a finite bbox supplies the cue."""

    location = record.location
    if location is None:
        return False
    if _explicit_lateral_side(record) is not None:
        return True
    # Bbox mid_x is a cue only for missing / unknown / exact "center".
    zone = location.zone
    if zone not in _BBOX_FALLBACK_ZONES:
        return False
    return _bbox_mid_x(record) is not None


def _lateral_side(record: RetainedEvidence) -> str | None:
    """Resolved side for command emission; center-band cue yields None (try next)."""

    location = record.location
    if location is None:
        return None
    explicit_side = _explicit_lateral_side(record)
    if explicit_side is not None:
        return explicit_side
    zone = location.zone
    if zone not in _BBOX_FALLBACK_ZONES:
        return None
    mid_x = _bbox_mid_x(record)
    if mid_x is None:
        return None
    if mid_x < 0.45:
        return "left"
    if mid_x > 0.55:
        return "right"
    return None


def _bbox_bottom(record: RetainedEvidence) -> float:
    """Return the lower image edge for a deterministic foreground tie-break."""

    location = record.location
    if location is None or location.bbox_xyxy_norm is None:
        return -1.0
    try:
        bottom = float(location.bbox_xyxy_norm[3])
    except (IndexError, TypeError, ValueError):
        return -1.0
    return bottom if math.isfinite(bottom) else -1.0


def _is_image_located_accepted_kind(
    record: RetainedEvidence, *, accepted_kinds: set[str]
) -> bool:
    if record.kind not in accepted_kinds:
        return False
    location = record.location
    return location is not None and location.frame == "image"


def _is_accepted_obstruction_candidate(
    record: RetainedEvidence, *, accepted_kinds: set[str]
) -> bool:
    """Accepted kind, image location, and a qualifying lateral cue (exact policy)."""

    if not _is_image_located_accepted_kind(record, accepted_kinds=accepted_kinds):
        return False
    return _has_lateral_cue(record)


def _freshness_class(
    record: RetainedEvidence,
    *,
    now: int,
    frame_id: str,
    retained_max_age_ms: int,
) -> str:
    updated = int(record.origin.updated_at_ms)
    if updated > now:
        return "invalid_future"
    if record.origin.frame_id == frame_id:
        return "fresh"
    age = now - updated
    if 0 <= age <= retained_max_age_ms:
        return "retained"
    return "stale"


def _source_ref(record: RetainedEvidence) -> SourceRef:
    return SourceRef(
        kind="memory_record",
        id=record.record_id,
        frame_id=record.origin.frame_id,
        observation_id=record.origin.observation_id,
        plugin_id=record.origin.source_plugin_id,
        note="primary_obstruction",
    )


def _proposal(
    context: DecisionFrameContext,
    *,
    lifecycle: str,
    reason: str,
    confidence: float = 0.0,
    command: ProposedVehicleCommand | None = None,
    source_refs: tuple[SourceRef, ...] = (),
    available: bool = False,
    freshness: str = "none",
) -> ActionProposal:
    return ActionProposal(
        plugin_id=AvoidRecentObstruction.plugin_id,
        frame_id=context.frame_id,
        lifecycle=lifecycle,
        freshness=freshness,
        confidence=confidence,
        reason=reason,
        command=command,
        assumptions=BASE_ASSUMPTIONS,
        source_refs=source_refs,
        available=available,
    )


def _inactive(context: DecisionFrameContext, reason: str) -> ActionProposal:
    return _proposal(context, lifecycle="inactive", reason=reason)


def _highest_confidence(records: list[RetainedEvidence]) -> RetainedEvidence:
    return sorted(records, key=lambda record: (*confidence_rank(record.confidence), record.record_id))[0]


def _admitted_candidates(
    context: DecisionFrameContext,
    *,
    evidence_key: str,
    accepted_kinds: Sequence[str],
) -> ActionProposal | list[RetainedEvidence]:
    shared_memory = context.shared_memory
    records = shared_memory.get(evidence_key) if shared_memory is not None else None
    if records is None:
        return _proposal(context, lifecycle="missing_input", reason="memory_unavailable")
    if not isinstance(records, (tuple, list)) or not all(
        isinstance(record, RetainedEvidence) for record in records
    ):
        return _proposal(context, lifecycle="error", reason="invalid_memory_value")

    kinds = set(accepted_kinds)
    accepted_kind_records = [record for record in records if record.kind in kinds]
    image_located = [
        record
        for record in accepted_kind_records
        if _is_image_located_accepted_kind(record, accepted_kinds=kinds)
    ]
    # Lateral cue is part of candidate admission — no-cue records never enter
    # freshness / future-dated classification.
    candidates = [
        record
        for record in image_located
        if _is_accepted_obstruction_candidate(record, accepted_kinds=kinds)
    ]
    if accepted_kind_records and not image_located:
        return _proposal(
            context,
            lifecycle="incompatible",
            reason="accepted_kind_non_image_location",
        )
    if not candidates:
        return _inactive(context, "no_accepted_obstruction_evidence")
    return candidates


def _classified_pools(
    context: DecisionFrameContext,
    candidates: list[RetainedEvidence],
    *,
    retained_max_age_ms: int,
) -> ActionProposal | tuple[list[RetainedEvidence] | None, str | None, list[RetainedEvidence]]:
    classified = [
        (
            _freshness_class(
                record,
                now=context.timestamp_ms,
                frame_id=context.frame_id,
                retained_max_age_ms=retained_max_age_ms,
            ),
            record,
        )
        for record in candidates
    ]
    if classified and all(cls == "invalid_future" for cls, _record in classified):
        return _proposal(context, lifecycle="error", reason="future_dated_provenance")
    usable = [(cls, record) for cls, record in classified if cls != "invalid_future"]
    fresh = [record for cls, record in usable if cls == "fresh"]
    retained = [record for cls, record in usable if cls == "retained"]
    stale = [record for cls, record in usable if cls == "stale"]
    # Fresh pool first. A fresh pool with no lateral side does not fall through to retained.
    if fresh:
        return fresh, "fresh", stale
    if retained:
        return retained, "retained", stale
    return None, None, stale


def _steer_away(
    context: DecisionFrameContext,
    pool: list[RetainedEvidence],
    lifecycle: str,
    steer_magnitude: float,
) -> ActionProposal | None:
    # Confidence remains primary. For equal-confidence physical boundaries,
    # prefer the foreground cue before the stable record-id tie-break.
    ordered = sorted(
        pool,
        key=lambda record: (*confidence_rank(record.confidence), -_bbox_bottom(record), record.record_id),
    )
    primary = None
    side = None
    for record in ordered:
        side = _lateral_side(record)
        if side is not None:
            primary = record
            break
    if primary is None or side is None:
        return None
    magnitude = float(steer_magnitude)
    steering = magnitude if side == "left" else -magnitude
    command = ProposedVehicleCommand(steering=steering, throttle=DEFAULT_FORWARD_THROTTLE)
    return _proposal(
        context,
        lifecycle=lifecycle,
        freshness=lifecycle,
        confidence=primary.confidence,
        reason=f"steer_away_{side}_obstruction",
        command=command,
        source_refs=(_source_ref(primary),),
        available=True,
    )


def propose(
    context: DecisionFrameContext,
    *,
    evidence_key: str = EVIDENCE_KEY,
    accepted_kinds: Sequence[str] = DEFAULT_ACCEPTED_KINDS,
    retained_max_age_ms: int = DEFAULT_RETAINED_MAX_AGE_MS,
    steer_magnitude: float = DEFAULT_STEER_MAGNITUDE,
) -> ActionProposal:
    """Emit exactly one ActionProposal from the context and retained evidence."""

    admitted = _admitted_candidates(
        context, evidence_key=evidence_key, accepted_kinds=accepted_kinds
    )
    if not isinstance(admitted, list):
        return admitted
    pooled = _classified_pools(context, admitted, retained_max_age_ms=retained_max_age_ms)
    if not isinstance(pooled, tuple):
        return pooled
    active_pool, pool_lifecycle, stale = pooled
    if active_pool is not None:
        chosen = _steer_away(context, active_pool, pool_lifecycle or "retained", steer_magnitude)
        if chosen is not None:
            return chosen
        # Active freshness class present but no lateral cue in that class.
        return _inactive(context, "no_lateral_obstruction_evidence")
    if stale:
        primary = _highest_confidence(stale)
        return _proposal(
            context,
            lifecycle="stale",
            freshness="stale",
            confidence=primary.confidence,
            reason="stale_obstruction_evidence",
            source_refs=(_source_ref(primary),),
        )
    return _inactive(context, "no_accepted_obstruction_evidence")


class AvoidRecentObstruction:
    """Proposal plugin: the module's ``propose`` bound to validated settings."""

    plugin_id = "avoid_recent_obstruction"

    def __init__(
        self,
        *,
        accepted_kinds: Sequence[str] = DEFAULT_ACCEPTED_KINDS,
        retained_max_age_ms: int = DEFAULT_RETAINED_MAX_AGE_MS,
        steer_magnitude: float = DEFAULT_STEER_MAGNITUDE,
        evidence_key: str = EVIDENCE_KEY,
    ) -> None:
        if type(accepted_kinds) not in (list, tuple):
            raise ValueError("accepted_kinds must be a list or tuple of kind ids")
        kinds = tuple(accepted_kinds)
        if not kinds or len(kinds) > 8:
            raise ValueError("accepted_kinds must contain 1..8 entries")
        if len(kinds) != len(set(kinds)):
            raise ValueError("accepted_kinds must be unique")
        for kind in kinds:
            require_ascii_id(kind, field_name="accepted_kind")
        if type(retained_max_age_ms) is not int:
            raise ValueError("retained_max_age_ms must be a non-bool int")
        if not 1 <= retained_max_age_ms <= 60_000:
            raise ValueError("retained_max_age_ms must be in 1..60000")
        try:
            magnitude = float(steer_magnitude)
        except (TypeError, ValueError) as exc:
            raise ValueError("steer_magnitude must be numeric") from exc
        if not math.isfinite(magnitude) or not (0.0 < magnitude <= 1.0):
            raise ValueError("steer_magnitude must satisfy 0 < value <= 1")
        # Annotated str, but a caller can pass another type. Reject that with ValueError.
        raw_key = cast(object, evidence_key)
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise ValueError("evidence_key must be a non-empty string")
        self.accepted_kinds = kinds
        self.retained_max_age_ms = retained_max_age_ms
        self.steer_magnitude = magnitude
        self.evidence_key = raw_key

    def propose(self, context: DecisionFrameContext) -> ActionProposal:
        return propose(
            context,
            evidence_key=self.evidence_key,
            accepted_kinds=self.accepted_kinds,
            retained_max_age_ms=self.retained_max_age_ms,
            steer_magnitude=self.steer_magnitude,
        )
