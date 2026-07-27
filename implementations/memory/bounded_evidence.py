"""Bounded recency ledger of observation evidence.

Retains attributed things and signals across cycles with finite capacity and
age. Recurring evidence_ids update the same ledger slot while their structure
remains compatible. An incompatible recurrence invalidates the slot rather than
choosing either claim as truth.

Record ids are namespaced by source plugin so two plugins cannot silently
overwrite one another with the same local evidence id.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from autonomy.decision import (
    DEFAULT_MAX_PROPERTY_BYTES,
    DEFAULT_MAX_SERIALIZED_BYTES,
    DecisionFrameContext,
    MemoryBounds,
    MemoryProvenance,
    MemorySnapshot,
    Observation,
    RetainedEvidence,
    detach_memory_snapshot,
    empty_memory_snapshot,
    ensure_strict_json_value,
    serialized_mapping_bytes,
)
from autonomy.perception import ViewLocation


CONFLICT_POLICY = "invalidate_incompatible_slot"


class BoundedEvidenceLedger:
    """Simple recency ledger used as the first packaged memory implementation."""

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
        self.bounds = MemoryBounds(
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
        self._records: dict[str, RetainedEvidence] = {}
        self._latest = self.reset()

    def update(
        self,
        context: DecisionFrameContext,
        observation: Observation | None,
    ) -> MemorySnapshot:
        now_ms = int(context.timestamp_ms)
        self._last_update_conflict_count = 0
        self._expire(now_ms=now_ms)
        if observation is not None:
            self._apply_records(
                self._extract_records(context, observation, now_ms=now_ms)
            )
        self._enforce_capacity()
        self._latest = self._build_snapshot(
            memory_id=f"memory-{context.frame_id}",
            created_at_ms=now_ms,
            observation=observation,
        )
        return detach_memory_snapshot(self._latest)

    def reset(self) -> MemorySnapshot:
        self._epoch += 1
        self._records = {}
        self._capacity_eviction_count = 0
        self._conflict_count = 0
        self._last_update_conflict_count = 0
        self._latest = empty_memory_snapshot(
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
            metadata={
                "policy": "bounded_evidence_recency",
                "claims_identity": False,
                "capacity_eviction_count": self._capacity_eviction_count,
                "conflict_policy": CONFLICT_POLICY,
                "conflict_count": self._conflict_count,
                "last_update_conflict_count": self._last_update_conflict_count,
            },
        )
        return detach_memory_snapshot(self._latest)

    def snapshot(self) -> MemorySnapshot:
        return detach_memory_snapshot(self._latest)

    def _extract_records(
        self,
        context: DecisionFrameContext,
        observation: Observation,
        *,
        now_ms: int,
    ) -> list[RetainedEvidence]:
        records: list[RetainedEvidence] = []
        if self.retain_things:
            for thing in observation.things:
                if not isinstance(thing, dict):
                    continue
                confidence = float(thing.get("confidence") or 0.0)
                if confidence < self.min_confidence:
                    continue
                evidence_id = str(thing.get("thing_id") or "").strip()
                if not evidence_id:
                    continue
                location = _location_from_payload(thing.get("location"))
                coordinate_frame = (
                    location.frame if location is not None else "image"
                )
                source_plugin = thing.get("source_plugin_id")
                if source_plugin is None:
                    source_plugin = observation.perception_plugin_id
                try:
                    properties = ensure_strict_json_value(
                        deepcopy(dict(thing.get("properties") or {}))
                    )
                except ValueError:
                    continue
                if not isinstance(properties, dict):
                    continue
                if not self._properties_within_bound(properties):
                    continue
                records.append(
                    RetainedEvidence(
                        record_id=namespaced_record_id(
                            "thing", evidence_id, source_plugin
                        ),
                        kind=str(thing.get("kind") or "thing"),
                        label=str(thing.get("label") or evidence_id),
                        confidence=confidence,
                        provenance=MemoryProvenance(
                            observation_id=observation.observation_id,
                            evidence_id=evidence_id,
                            coordinate_frame=coordinate_frame,
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
        if self.retain_signals:
            for signal in observation.signals:
                if not isinstance(signal, dict):
                    continue
                confidence = float(signal.get("confidence") or 0.0)
                if confidence < self.min_confidence:
                    continue
                signal_id = str(signal.get("signal_id") or "").strip()
                if not signal_id:
                    continue
                value = signal.get("value")
                # Keep affirmative / present signals; skip explicit false.
                if value is False:
                    continue
                source_plugin = signal.get("source_plugin_id")
                if source_plugin is None:
                    source_plugin = observation.perception_plugin_id
                try:
                    properties = ensure_strict_json_value(
                        deepcopy(dict(signal.get("properties") or {}))
                    )
                except ValueError:
                    continue
                if not isinstance(properties, dict):
                    continue
                properties = dict(properties)
                properties["value"] = value
                try:
                    properties = ensure_strict_json_value(properties)
                except ValueError:
                    continue
                if not isinstance(properties, dict):
                    continue
                if not self._properties_within_bound(properties):
                    continue
                records.append(
                    RetainedEvidence(
                        record_id=namespaced_record_id(
                            "signal", signal_id, source_plugin
                        ),
                        kind="signal",
                        label=signal_id,
                        confidence=confidence,
                        provenance=MemoryProvenance(
                            observation_id=observation.observation_id,
                            evidence_id=signal_id,
                            coordinate_frame="observation",
                            observed_at_ms=int(observation.created_at_ms),
                            updated_at_ms=now_ms,
                            source_plugin_id=(
                                str(source_plugin) if source_plugin is not None else None
                            ),
                            frame_id=context.frame_id,
                        ),
                        location=None,
                        properties=properties,
                    )
                )
        return records

    def _properties_within_bound(self, properties: dict[str, Any]) -> bool:
        limit = self.bounds.max_property_bytes
        if limit is None:
            return True
        return serialized_mapping_bytes(properties) <= limit

    def _apply_records(self, records: list[RetainedEvidence]) -> None:
        """Apply one observation without making tuple order a conflict tiebreaker."""

        grouped: dict[str, list[RetainedEvidence]] = {}
        for record in records:
            grouped.setdefault(record.record_id, []).append(record)

        for record_id in sorted(grouped):
            candidates = grouped[record_id]
            candidate = candidates[0]
            if any(item != candidate for item in candidates[1:]):
                self._invalidate_conflict(record_id)
                continue

            retained = self._records.get(record_id)
            if retained is not None and not _structurally_compatible(
                retained, candidate
            ):
                self._invalidate_conflict(record_id)
                continue
            self._records[record_id] = candidate

    def _invalidate_conflict(self, record_id: str) -> None:
        self._records.pop(record_id, None)
        self._conflict_count += 1
        self._last_update_conflict_count += 1

    def _expire(self, *, now_ms: int) -> None:
        max_age_ms = self.bounds.max_age_ms
        if max_age_ms is None:
            return
        keep: dict[str, RetainedEvidence] = {}
        for record_id, record in self._records.items():
            age = now_ms - int(record.provenance.updated_at_ms)
            if age <= max_age_ms:
                keep[record_id] = record
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

    def _build_snapshot(
        self,
        *,
        memory_id: str,
        created_at_ms: int,
        observation: Observation | None,
    ) -> MemorySnapshot:
        conflict_summary = (
            (
                f"conflict_count={self._conflict_count}",
                f"last_update_conflicts={self._last_update_conflict_count}",
            )
            if self._conflict_count
            else ()
        )
        metadata = {
            "policy": "bounded_evidence_recency",
            "claims_identity": False,
            "observation_id": (
                observation.observation_id if observation is not None else None
            ),
            "capacity_eviction_count": self._capacity_eviction_count,
            "conflict_policy": CONFLICT_POLICY,
            "conflict_count": self._conflict_count,
            "last_update_conflict_count": self._last_update_conflict_count,
        }
        records = tuple(
            sorted(
                self._records.values(),
                key=lambda item: (
                    -int(item.provenance.updated_at_ms),
                    item.record_id,
                ),
            )
        )
        if not records:
            return empty_memory_snapshot(
                memory_id=memory_id,
                epoch_id=f"epoch-{self._epoch}",
                bounds=self.bounds,
                created_at_ms=created_at_ms,
                implementation_id=self.implementation_id,
                summary=(
                    "memory_empty=true",
                    f"epoch_id=epoch-{self._epoch}",
                    (
                        "reason=no_observation"
                        if observation is None
                        else "reason=no_retained_evidence"
                    ),
                    *conflict_summary,
                ),
                metadata=metadata,
            )
        kinds = sorted({record.kind for record in records})
        return MemorySnapshot(
            memory_id=memory_id,
            epoch_id=f"epoch-{self._epoch}",
            health="healthy",
            bounds=self.bounds,
            created_at_ms=created_at_ms,
            records=records,
            summary=(
                f"retained_count={len(records)}",
                f"epoch_id=epoch-{self._epoch}",
                f"kinds={','.join(kinds)}",
                "policy=bounded_evidence_recency",
                *conflict_summary,
            ),
            implementation_id=self.implementation_id,
            metadata=metadata,
        )


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


def _structurally_compatible(
    retained: RetainedEvidence,
    candidate: RetainedEvidence,
) -> bool:
    return _record_structure(retained) == _record_structure(candidate)


def _record_structure(record: RetainedEvidence) -> tuple[Any, ...]:
    """Return the schema that must remain stable while a slot is retained."""

    location = record.location
    location_structure = (
        None
        if location is None
        else (
            bool(location.bbox_xyxy_norm is not None),
            bool(location.polygon_xy_norm is not None),
        )
    )
    return (
        record.kind,
        record.provenance.coordinate_frame,
        location_structure,
        _json_structure(record.properties),
    )


def _json_structure(value: Any) -> tuple[Any, ...]:
    """Describe strict JSON shape while allowing ordinary scalar value changes."""

    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean",)
    if isinstance(value, (int, float)):
        return ("number",)
    if isinstance(value, str):
        return ("string",)
    if isinstance(value, list):
        item_shapes = {_json_structure(item) for item in value}
        return ("array", tuple(sorted(item_shapes, key=repr)))
    if isinstance(value, dict):
        return (
            "object",
            tuple(
                (str(key), _json_structure(item))
                for key, item in sorted(value.items())
            ),
        )
    raise TypeError(f"unsupported retained JSON value: {type(value).__name__}")


def _location_from_payload(payload: Any) -> ViewLocation | None:
    if not isinstance(payload, dict):
        return None
    try:
        return ViewLocation.from_dict(payload)
    except (TypeError, ValueError):
        return None
