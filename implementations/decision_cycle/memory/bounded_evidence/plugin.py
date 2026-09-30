"""Bounded recency ledger of observation evidence.

Retains attributed things and signals across cycles with finite capacity and
age. Recurring evidence_ids update the same ledger slot within an epoch; that
is recency bookkeeping, not semantic object identity or world truth.

Same-slot updates preserve kind and location shape while allowing properties to
change between observations. Duplicate candidates within one observation must
still agree (conflict policy ``bounded_evidence_structural_v2``). Record ids are
namespaced by source plugin so two plugins cannot silently overwrite one
another with the same local evidence id.

The plugin keeps its ledger at ``LEDGER_KEY`` in shared memory and publishes
the retained records at ``EVIDENCE_KEY`` for other plugins. Its reducer uses
mutable working data for one update and is then discarded.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.memory.evidence import MemoryProvenance, RetainedEvidence
from autonomy.serialization import ensure_strict_json_value
from autonomy.shared_memory import SharedMemory
from implementations.decision_cycle.memory.bounded_evidence.ledger import (
    DEFAULT_MAX_PROPERTY_BYTES,
    DEFAULT_MAX_SERIALIZED_BYTES,
    EVIDENCE_KEY,
    LEDGER_KEY,
    EvidenceLedger,
    LedgerBounds,
    detach_ledger,
    empty_ledger,
    serialized_ledger_bytes,
    serialized_mapping_bytes,
)
from autonomy.decision_cycle.perception.evidence.values import ViewLocation

CONFLICT_POLICY = "bounded_evidence_structural_v2"
MAX_REPORTED_DROPS = 12
MAX_REPORTED_ID_CHARS = 128


class _BoundedEvidenceReducer:
    """Temporary working state for one reduction, or a standalone algorithm test."""

    implementation_id = "bounded_evidence"

    def __init__(
        self,
        *,
        max_records: int = 32,
        max_age_ms: int | None = 10_000,
        eviction_policy: str = "oldest_first",
        min_confidence: float = 0.0,
        retain_things: bool = True,
        retain_signals: bool = True,
        max_property_bytes: int | None = DEFAULT_MAX_PROPERTY_BYTES,
        max_serialized_bytes: int | None = DEFAULT_MAX_SERIALIZED_BYTES,
        **_ignored: Any,
    ) -> None:
        if eviction_policy != "oldest_first":
            raise ValueError(
                "BoundedEvidenceLedger only supports eviction_policy='oldest_first'"
            )
        self.bounds = LedgerBounds(
            max_records=int(max_records),
            max_age_ms=int(max_age_ms) if max_age_ms is not None else None,
            eviction_policy=str(eviction_policy),
            max_property_bytes=(
                int(max_property_bytes) if max_property_bytes is not None else None
            ),
            max_serialized_bytes=(
                int(max_serialized_bytes) if max_serialized_bytes is not None else None
            ),
        )
        self.min_confidence = max(0.0, min(1.0, float(min_confidence)))
        self.retain_things = bool(retain_things)
        self.retain_signals = bool(retain_signals)
        self._epoch = 0
        self._capacity_eviction_count = 0
        self._conflict_count = 0
        self._last_update_conflict_count = 0
        self._last_update_drops: list[dict[str, str]] = []
        self._last_update_drop_count = 0
        self._records: dict[str, RetainedEvidence] = {}
        self._latest = self.reset()

    def update(
        self,
        context: DecisionFrameContext,
        observation: Observation | None,
    ) -> EvidenceLedger:
        now_ms = int(context.timestamp_ms)
        self._last_update_drops = []
        self._last_update_drop_count = 0
        candidates: list[RetainedEvidence] = []
        if observation is not None:
            candidates = self._extract_records(context, observation, now_ms=now_ms)

        # Expire before same-slot comparison (proposal update order).
        self._expire(now_ms=now_ms)

        update_conflicts = 0
        groups: dict[str, list[RetainedEvidence]] = {}
        for candidate in candidates:
            groups.setdefault(candidate.record_id, []).append(candidate)

        for record_id, group in groups.items():
            if _group_has_contradiction(group):
                action = "rejected_current"
                if record_id in self._records:
                    del self._records[record_id]
                    action = "removed_prior_and_rejected_current"
                self._report_drop(record_id, "contradictory_candidates", action)
                update_conflicts += 1
                continue
            # Payload-equal group collapses to one representative.
            candidate = group[0]
            retained = self._records.get(record_id)
            if retained is None:
                self._records[record_id] = candidate
                continue
            reason = structural_conflict_reason(retained, candidate)
            if reason is None:
                self._records[record_id] = candidate
                continue
            del self._records[record_id]
            self._report_drop(record_id, reason, "removed_prior_and_rejected_current")
            update_conflicts += 1

        self._last_update_conflict_count = update_conflicts
        self._conflict_count += update_conflicts
        self._enforce_capacity()
        self._latest = self._build_ledger(
            memory_id=f"memory-{context.frame_id}",
            created_at_ms=now_ms,
            observation=observation,
        )
        limit = self.bounds.max_serialized_bytes
        while (
            limit is not None
            and self._last_update_drops
            and serialized_ledger_bytes(self._latest) > limit
        ):
            self._last_update_drops.pop()
            self._latest = self._build_ledger(
                memory_id=f"memory-{context.frame_id}",
                created_at_ms=now_ms,
                observation=observation,
            )
        return detach_ledger(self._latest)

    def reset(self) -> EvidenceLedger:
        self._epoch += 1
        self._records = {}
        self._capacity_eviction_count = 0
        self._conflict_count = 0
        self._last_update_conflict_count = 0
        self._last_update_drops = []
        self._last_update_drop_count = 0
        self._latest = empty_ledger(
            memory_id=f"memory-reset-{self._epoch}",
            epoch_id=f"epoch-{self._epoch}",
            bounds=self.bounds,
            created_at_ms=0,
            implementation_id=self.implementation_id,
            summary=(
                "memory_empty=true",
                f"epoch_id=epoch-{self._epoch}",
                "policy=bounded_evidence_recency",
            ),
            metadata=self._metadata(observation_id=None),
        )
        return detach_ledger(self._latest)

    def ledger(self) -> EvidenceLedger:
        # Pure read: must not zero last_update_conflict_count.
        return detach_ledger(self._latest)

    def _metadata(self, *, observation_id: str | None) -> dict[str, Any]:
        return {
            "policy": "bounded_evidence_recency",
            "claims_identity": False,
            "observation_id": observation_id,
            "capacity_eviction_count": self._capacity_eviction_count,
            "conflict_policy": CONFLICT_POLICY,
            "conflict_count": self._conflict_count,
            "last_update_conflict_count": self._last_update_conflict_count,
            "last_update_drop_count": self._last_update_drop_count,
            "last_update_drops": list(self._last_update_drops),
            "last_update_drops_omitted": (
                self._last_update_drop_count - len(self._last_update_drops)
            ),
        }

    def _report_drop(self, record_id: str, reason: str, action: str) -> None:
        self._last_update_drop_count += 1
        display_id = (
            record_id
            if len(record_id) <= MAX_REPORTED_ID_CHARS
            else record_id[: MAX_REPORTED_ID_CHARS - 1] + "…"
        )
        event = {"record_id": display_id, "reason": reason, "action": action}
        if reason in {"expired", "capacity"}:
            if len(self._last_update_drops) < MAX_REPORTED_DROPS:
                self._last_update_drops.append(event)
        else:
            self._last_update_drops.insert(0, event)
            del self._last_update_drops[MAX_REPORTED_DROPS:]

    def _extract_records(
        self,
        context: DecisionFrameContext,
        observation: Observation,
        *,
        now_ms: int,
    ) -> list[RetainedEvidence]:
        records: list[RetainedEvidence] = []
        for kind, enabled, candidates in (
            ("thing", self.retain_things, observation.things),
            ("signal", self.retain_signals, observation.signals),
        ):
            if not enabled:
                continue
            is_thing = kind == "thing"
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    self._report_drop(f"<{kind}>", "invalid_candidate", "not_admitted")
                    continue
                confidence = float(candidate.get("confidence") or 0.0)
                if confidence < self.min_confidence:
                    continue
                evidence_id = str(candidate.get(f"{kind}_id") or "").strip()
                if not evidence_id:
                    self._report_drop(f"<{kind}>", "missing_evidence_id", "not_admitted")
                    continue
                value = candidate.get("value") if kind == "signal" else None
                # Keep affirmative / present signals; skip explicit false.
                if kind == "signal" and value is False:
                    continue
                source_plugin = candidate.get("source_plugin_id")
                if source_plugin is None:
                    source_plugin = observation.perception_plugin_id
                record_id = namespaced_record_id(kind, evidence_id, source_plugin)
                location = (
                    _location_from_payload(candidate.get("location"))
                    if is_thing else None
                )
                try:
                    properties = ensure_strict_json_value(
                        deepcopy(dict(candidate.get("properties") or {}))
                    )
                except (TypeError, ValueError):
                    self._report_drop(record_id, "invalid_properties", "not_admitted")
                    continue
                if kind == "signal":
                    properties["value"] = value
                    try:
                        properties = ensure_strict_json_value(properties)
                    except ValueError:
                        self._report_drop(record_id, "invalid_properties", "not_admitted")
                        continue
                if not self._properties_within_bound(properties):
                    self._report_drop(record_id, "properties_too_large", "not_admitted")
                    continue
                label = str(candidate.get("label") or evidence_id) if is_thing else evidence_id
                records.append(
                    RetainedEvidence(
                        record_id=record_id,
                        kind=str(candidate.get("kind") or "thing") if is_thing else "signal",
                        label=label,
                        confidence=confidence,
                        provenance=MemoryProvenance(
                            observation_id=observation.observation_id,
                            evidence_id=evidence_id,
                            coordinate_frame=(
                                location.frame if location is not None else "image"
                            ) if is_thing else "observation",
                            observed_at_ms=int(observation.created_at_ms),
                            updated_at_ms=now_ms,
                            source_plugin_id=(
                                str(source_plugin) if source_plugin is not None else None
                            ),
                            frame_id=context.frame_id,
                        ),
                        location=location,
                        properties=properties,
                    )
                )
        return records

    def _properties_within_bound(self, properties: dict[str, Any]) -> bool:
        limit = self.bounds.max_property_bytes
        if limit is None:
            return True
        return serialized_mapping_bytes(properties) <= limit

    def _expire(self, *, now_ms: int) -> None:
        max_age_ms = self.bounds.max_age_ms
        if max_age_ms is None:
            return
        keep: dict[str, RetainedEvidence] = {}
        for record_id, record in self._records.items():
            age = now_ms - int(record.provenance.updated_at_ms)
            if age <= max_age_ms:
                keep[record_id] = record
            else:
                self._report_drop(record_id, "expired", "removed")
        self._records = keep

    def _enforce_capacity(self) -> None:
        overflow = len(self._records) - self.bounds.max_records
        if overflow <= 0:
            return
        ordered = sorted(
            self._records.values(),
            key=lambda item: (
                int(item.provenance.updated_at_ms),
                item.record_id,
            ),
        )
        for record in ordered[:overflow]:
            self._records.pop(record.record_id, None)
            self._capacity_eviction_count += 1
            self._report_drop(record.record_id, "capacity", "evicted")

    def _build_ledger(
        self,
        *,
        memory_id: str,
        created_at_ms: int,
        observation: Observation | None,
    ) -> EvidenceLedger:
        epoch_id = f"epoch-{self._epoch}"
        records = tuple(
            sorted(
                self._records.values(),
                key=lambda item: (
                    -int(item.provenance.updated_at_ms),
                    item.record_id,
                ),
            )
        )
        if records:
            summary = (
                f"retained_count={len(records)}",
                f"epoch_id={epoch_id}",
                f"kinds={','.join(sorted({record.kind for record in records}))}",
                "policy=bounded_evidence_recency",
            )
        else:
            summary = (
                "memory_empty=true",
                f"epoch_id={epoch_id}",
                "reason=no_observation"
                if observation is None else "reason=no_retained_evidence",
            )
        return EvidenceLedger(
            memory_id=memory_id,
            epoch_id=epoch_id,
            health="healthy" if records else "empty",
            bounds=self.bounds,
            created_at_ms=created_at_ms,
            records=records,
            summary=summary,
            implementation_id=self.implementation_id,
            metadata=self._metadata(
                observation_id=(
                    observation.observation_id if observation is not None else None
                )
            ),
        )


def reduce_evidence(
    previous: EvidenceLedger,
    context: DecisionFrameContext,
    observation: Observation | None,
    *,
    implementation_id: str,
    **config: Any,
) -> EvidenceLedger:
    """Reduce one cycle from an explicit prior ledger without retaining a reducer."""
    reducer = _BoundedEvidenceReducer(**config)
    reducer.implementation_id = implementation_id
    reducer._records = {record.record_id: record for record in previous.records}
    reducer._capacity_eviction_count = int(
        previous.metadata.get("capacity_eviction_count", 0)
    )
    reducer._conflict_count = int(previous.metadata.get("conflict_count", 0))
    ledger = reducer.update(context, observation)
    return replace(
        ledger,
        epoch_id=previous.epoch_id,
        summary=tuple(
            f"epoch_id={previous.epoch_id}" if item.startswith("epoch_id=") else item
            for item in ledger.summary
        ),
    )


class BoundedEvidenceLedger:
    """Memory plugin that keeps bounded retained evidence.

    The plugin keeps its ``EvidenceLedger`` at ``LEDGER_KEY`` and publishes the
    ledger's records at ``EVIDENCE_KEY``. Other keys in the map belong to other
    producers.
    """

    implementation_id = "bounded_evidence"

    def __init__(self, **config: Any) -> None:
        self.config = config
        reducer = _BoundedEvidenceReducer(**config)
        self.bounds = reducer.bounds
        self._empty = reducer.ledger()

    def ledger(self, shared_memory: SharedMemory | None) -> EvidenceLedger:
        """The stored ledger, or the initial empty ledger before the first update."""

        current = shared_memory.get(LEDGER_KEY) if shared_memory is not None else None
        return detach_ledger(current if isinstance(current, EvidenceLedger) else self._empty)

    def status(self, shared_memory: SharedMemory | None) -> dict[str, Any]:
        return self.ledger(shared_memory).to_dict()

    def reset(self, shared_memory: SharedMemory) -> None:
        previous = self.ledger(shared_memory)
        next_epoch = _numbered_epoch(previous.epoch_id) + 1
        epoch = f"epoch-{next_epoch}"
        publish_ledger(
            shared_memory,
            replace(
                self._empty,
                memory_id=f"memory-reset-{next_epoch}",
                epoch_id=epoch,
                summary=(
                    "memory_empty=true",
                    f"epoch_id={epoch}",
                    "policy=bounded_evidence_recency",
                ),
            ),
        )

    def update(
        self,
        context: DecisionFrameContext,
        observation: Observation | None,
    ) -> None:
        if context.shared_memory is None:
            raise ValueError("bounded evidence requires a shared-memory map")
        publish_ledger(
            context.shared_memory,
            reduce_evidence(
                self.ledger(context.shared_memory),
                context,
                observation,
                implementation_id=self.implementation_id,
                **self.config,
            ),
        )


def publish_ledger(shared_memory: SharedMemory, ledger: EvidenceLedger) -> None:
    """Store the ledger and publish its records for other plugins."""

    shared_memory[LEDGER_KEY] = ledger
    shared_memory[EVIDENCE_KEY] = ledger.records


def _numbered_epoch(epoch_id: str) -> int:
    number = epoch_id.removeprefix("epoch-") if epoch_id.startswith("epoch-") else ""
    return max(1, int(number)) if number.isdecimal() else 1


def namespaced_record_id(
    kind_prefix: str,
    evidence_id: str,
    source_plugin_id: str | None,
) -> str:
    """Build an injective plugin-safe ledger key.

    Formats:
    - absent source: ``{kind}:0:{evidence_len}:{evidence}``
    - present source: ``{kind}:1:{plugin_len}:{plugin}:{evidence_len}:{evidence}``

    Optional presence is encoded explicitly so ``None`` never collides with a
    plugin literally named ``\"unknown\"``. Plugin and evidence strings are kept
    exactly as supplied (no strip), so whitespace-distinct IDs remain distinct.
    Length-prefixed components keep delimiter-containing IDs collision-free.
    """

    evidence = str(evidence_id)
    if source_plugin_id is None:
        return f"{kind_prefix}:0:{len(evidence)}:{evidence}"
    plugin = str(source_plugin_id)
    return (
        f"{kind_prefix}:1:{len(plugin)}:{plugin}:{len(evidence)}:{evidence}"
    )


def _location_from_payload(payload: Any) -> ViewLocation | None:
    if not isinstance(payload, dict):
        return None
    try:
        return ViewLocation.from_dict(payload)
    except (TypeError, ValueError):
        return None


def location_geometry_signature(
    location: ViewLocation | None,
) -> tuple[bool, bool] | None:
    """Return (has_bbox, has_polygon), or None when location is absent."""

    if location is None:
        return None
    return (
        location.bbox_xyxy_norm is not None,
        location.polygon_xy_norm is not None,
    )


def json_values_equal(left: Any, right: Any) -> bool:
    """Deep JSON value equality with exact int/float compare (no float())."""

    if type(left) is bool or type(right) is bool:
        return type(left) is bool and type(right) is bool and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if left is None and right is None:
        return True
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            json_values_equal(a, b) for a, b in zip(left, right)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right):
            return False
        return all(json_values_equal(left[key], right[key]) for key in left)
    return False


def payload_equal(left: RetainedEvidence, right: RetainedEvidence) -> bool:
    """Same-observation payload equality (provenance excluded)."""

    return (
        left.record_id == right.record_id
        and left.kind == right.kind
        and left.label == right.label
        and left.confidence == right.confidence
        and (
            (left.location is None and right.location is None)
            or (
                left.location is not None
                and right.location is not None
                and left.location.to_dict() == right.location.to_dict()
            )
        )
        and json_values_equal(left.properties, right.properties)
    )


def structural_conflict_reason(
    retained: RetainedEvidence, candidate: RetainedEvidence
) -> str | None:
    """Report identity or location changes; properties are observation data."""

    if retained.kind != candidate.kind:
        return "kind_changed"
    if (retained.location is None) != (candidate.location is None):
        return "location_presence_changed"
    if retained.location is not None and candidate.location is not None:
        if retained.location.frame != candidate.location.frame:
            return "coordinate_frame_changed"
        if location_geometry_signature(retained.location) != location_geometry_signature(
            candidate.location
        ):
            return "geometry_changed"
    return None


def structurally_compatible(
    retained: RetainedEvidence, candidate: RetainedEvidence
) -> bool:
    return structural_conflict_reason(retained, candidate) is None


# Public aliases used by tests for table-driven coverage of pure helpers.
_structurally_compatible = structurally_compatible
_payload_equal = payload_equal


def _group_has_contradiction(group: list[RetainedEvidence]) -> bool:
    if len(group) < 2:
        return False
    first = group[0]
    return any(not payload_equal(first, other) for other in group[1:])
