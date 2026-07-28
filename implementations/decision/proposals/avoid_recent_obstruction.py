"""Reference avoid_recent_obstruction proposal plugin (M006-04)."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Sequence

from autonomy.decision.action_proposal import (
    ActionProposal,
    ProposedVehicleCommand,
    SourceRef,
)
from autonomy.decision.decision_data import DecisionDataSource
from autonomy.decision.memory import MemorySnapshot, RetainedEvidence

PLUGIN_ID = "avoid_recent_obstruction"
DEFAULT_ACCEPTED_KINDS = ("floor_boundary", "obstacle", "obstruction_evidence")
DEFAULT_RETAINED_MAX_AGE_MS = 1000
DEFAULT_STEER_MAGNITUDE = 0.35
BASE_ASSUMPTIONS = (
    "no_object_identity",
    "image_relative_only",
    "shadow_only",
    "single_primary_record",
)


def _lateral_side(record: RetainedEvidence) -> str | None:
    location = record.location
    if location is None:
        return None
    zone = (location.zone or "").lower()
    if zone == "left":
        return "left"
    if zone == "right":
        return "right"
    bbox = location.bbox_xyxy_norm
    if bbox is None or len(bbox) != 4:
        return None
    try:
        mid_x = (float(bbox[0]) + float(bbox[2])) / 2.0
    except (TypeError, ValueError):
        return None
    if not math.isfinite(mid_x):
        return None
    if mid_x < 0.45:
        return "left"
    if mid_x > 0.55:
        return "right"
    return None


def _is_accepted_kind_image_located(
    record: RetainedEvidence, *, accepted_kinds: set[str]
) -> bool:
    """Accepted kind with image location (may still lack lateral side / be center)."""

    if record.kind not in accepted_kinds:
        return False
    location = record.location
    return location is not None and location.frame == "image"


def _freshness_class(
    record: RetainedEvidence,
    *,
    now: int,
    frame_id: str,
    retained_max_age_ms: int,
) -> str:
    updated = int(record.provenance.updated_at_ms)
    if updated > now:
        return "invalid_future"
    if record.provenance.frame_id == frame_id:
        return "fresh"
    age = now - updated
    if 0 <= age <= retained_max_age_ms:
        return "retained"
    return "stale"


def _source_ref(record: RetainedEvidence) -> SourceRef:
    return SourceRef(
        kind="memory_record",
        id=record.record_id,
        frame_id=record.provenance.frame_id,
        observation_id=record.provenance.observation_id,
        plugin_id=record.provenance.source_plugin_id,
        note="primary_obstruction",
    )


def _inactive(source: DecisionDataSource, reason: str) -> ActionProposal:
    return ActionProposal(
        plugin_id=PLUGIN_ID,
        frame_id=source.frame_id,
        lifecycle="inactive",
        freshness="none",
        confidence=0.0,
        reason=reason,
        command=None,
        assumptions=BASE_ASSUMPTIONS,
        source_refs=(),
        available=False,
    )


def propose(
    source: DecisionDataSource,
    *,
    accepted_kinds: Sequence[str] = DEFAULT_ACCEPTED_KINDS,
    retained_max_age_ms: int = DEFAULT_RETAINED_MAX_AGE_MS,
    steer_magnitude: float = DEFAULT_STEER_MAGNITUDE,
) -> ActionProposal:
    """Emit exactly one ActionProposal for the current DecisionDataSource."""

    if not isinstance(source, DecisionDataSource):
        return ActionProposal(
            plugin_id=PLUGIN_ID,
            frame_id="invalid",
            lifecycle="error",
            freshness="none",
            confidence=0.0,
            reason="decision_source_missing",
            command=None,
            assumptions=BASE_ASSUMPTIONS,
            available=False,
        )

    memory = source.memory
    if memory.status != "ready":
        reason = (
            "memory_unavailable"
            if memory.status == "unavailable"
            else str(memory.reason or "memory_error")
        )
        return ActionProposal(
            plugin_id=PLUGIN_ID,
            frame_id=source.frame_id,
            lifecycle="missing_input",
            freshness="none",
            confidence=0.0,
            reason=reason,
            command=None,
            assumptions=BASE_ASSUMPTIONS,
            available=False,
        )

    snapshot = memory.value
    if not isinstance(snapshot, MemorySnapshot):
        return ActionProposal(
            plugin_id=PLUGIN_ID,
            frame_id=source.frame_id,
            lifecycle="error",
            freshness="none",
            confidence=0.0,
            reason="invalid_memory_value",
            command=None,
            assumptions=BASE_ASSUMPTIONS,
            available=False,
        )

    kinds = set(accepted_kinds)
    accepted_kind_records = [r for r in snapshot.records if r.kind in kinds]
    structural = [
        r
        for r in accepted_kind_records
        if _is_accepted_kind_image_located(r, accepted_kinds=kinds)
    ]

    if accepted_kind_records and not structural:
        # Accepted kinds present but none image-located.
        return ActionProposal(
            plugin_id=PLUGIN_ID,
            frame_id=source.frame_id,
            lifecycle="incompatible",
            freshness="none",
            confidence=0.0,
            reason="accepted_kind_non_image_location",
            command=None,
            assumptions=BASE_ASSUMPTIONS,
            available=False,
        )

    if not structural:
        return _inactive(source, "no_accepted_obstruction_evidence")

    classified: list[tuple[str, RetainedEvidence]] = []
    for record in structural:
        cls = _freshness_class(
            record,
            now=source.timestamp_ms,
            frame_id=source.frame_id,
            retained_max_age_ms=retained_max_age_ms,
        )
        classified.append((cls, record))

    if classified and all(cls == "invalid_future" for cls, _ in classified):
        return ActionProposal(
            plugin_id=PLUGIN_ID,
            frame_id=source.frame_id,
            lifecycle="error",
            freshness="none",
            confidence=0.0,
            reason="future_dated_provenance",
            command=None,
            assumptions=BASE_ASSUMPTIONS,
            source_refs=(),
            available=False,
        )

    usable = [(cls, rec) for cls, rec in classified if cls != "invalid_future"]
    fresh = [rec for cls, rec in usable if cls == "fresh"]
    retained = [rec for cls, rec in usable if cls == "retained"]
    stale = [rec for cls, rec in usable if cls == "stale"]

    def pick(records: list[RetainedEvidence]) -> RetainedEvidence:
        return sorted(records, key=lambda r: (-r.confidence, r.record_id))[0]

    # Fresh pool first if any fresh records exist — do not fall back to retained
    # when the fresh pool is only center-band (no usable lateral side).
    if fresh:
        active_pool: list[RetainedEvidence] | None = fresh
        pool_lifecycle = "fresh"
    elif retained:
        active_pool = retained
        pool_lifecycle = "retained"
    else:
        active_pool = None
        pool_lifecycle = None

    if active_pool is not None:
        ordered = sorted(active_pool, key=lambda r: (-r.confidence, r.record_id))
        primary = None
        side = None
        for record in ordered:
            side = _lateral_side(record)
            if side is not None:
                primary = record
                break
        if primary is None:
            # Active freshness class present but no lateral cue in that class.
            return _inactive(source, "no_lateral_obstruction_evidence")

        if not math.isfinite(primary.confidence) or not (
            0.0 <= primary.confidence <= 1.0
        ):
            return ActionProposal(
                plugin_id=PLUGIN_ID,
                frame_id=source.frame_id,
                lifecycle="error",
                freshness="none",
                confidence=0.0,
                reason="invalid_record_confidence",
                command=None,
                assumptions=BASE_ASSUMPTIONS,
                source_refs=(_source_ref(primary),),
                available=False,
            )

        assumptions = list(BASE_ASSUMPTIONS)
        magnitude = float(steer_magnitude)
        caps = source.capabilities
        if caps.status == "ready":
            # Ready but non-mapping or invalid fields fail closed — do not treat
            # as unavailable fallback. Frozen JSON objects are Mapping, not dict.
            caps_value = caps.value
            if hasattr(caps_value, "to_dict") and callable(
                getattr(caps_value, "to_dict")
            ):
                caps_value = caps_value.to_dict()
            if isinstance(caps_value, Mapping) and not isinstance(
                caps_value, (str, bytes)
            ):
                caps_value = dict(caps_value)
            if not isinstance(caps_value, dict):
                return ActionProposal(
                    plugin_id=PLUGIN_ID,
                    frame_id=source.frame_id,
                    lifecycle="error",
                    freshness="none",
                    confidence=0.0,
                    reason="invalid_capabilities",
                    command=None,
                    assumptions=BASE_ASSUMPTIONS,
                    available=False,
                )
            try:
                max_abs = float(caps_value.get("max_abs_steering"))
            except (TypeError, ValueError):
                max_abs = None
            if max_abs is None or not math.isfinite(max_abs) or not (0.0 < max_abs <= 1.0):
                return ActionProposal(
                    plugin_id=PLUGIN_ID,
                    frame_id=source.frame_id,
                    lifecycle="error",
                    freshness="none",
                    confidence=0.0,
                    reason="invalid_capabilities",
                    command=None,
                    assumptions=BASE_ASSUMPTIONS,
                    available=False,
                )
            magnitude = min(magnitude, max_abs)
        else:
            assumptions.append("capabilities_not_ready")

        steering = magnitude if side == "left" else -magnitude
        lifecycle = pool_lifecycle or "retained"

        command = ProposedVehicleCommand(
            steering=steering, throttle=0.0, gear="hold"
        )
        return ActionProposal(
            plugin_id=PLUGIN_ID,
            frame_id=source.frame_id,
            lifecycle=lifecycle,
            freshness=lifecycle,
            confidence=primary.confidence,
            reason=f"steer_away_{side}_obstruction",
            command=command,
            assumptions=tuple(assumptions),
            source_refs=(_source_ref(primary),),
            available=True,
        )

    if stale:
        primary = pick(stale)
        conf = primary.confidence
        if not math.isfinite(conf) or not (0.0 <= conf <= 1.0):
            conf = 0.0
        return ActionProposal(
            plugin_id=PLUGIN_ID,
            frame_id=source.frame_id,
            lifecycle="stale",
            freshness="stale",
            confidence=conf,
            reason="stale_obstruction_evidence",
            command=None,
            assumptions=BASE_ASSUMPTIONS,
            source_refs=(_source_ref(primary),),
            available=False,
        )

    return _inactive(source, "no_accepted_obstruction_evidence")
