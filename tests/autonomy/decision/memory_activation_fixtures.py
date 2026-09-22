from __future__ import annotations
import json
from pathlib import Path
from autonomy.decision import (
    MEMORY_ACTIVATION_SCHEMA,
    MemoryBounds,
    MemoryProvenance,
    MemorySnapshot,
    RetainedEvidence,
    empty_memory_snapshot,
)
from autonomy.perception import ViewLocation


class _RecordingMemory:
    """Test double used only by activation tests."""

    def __init__(
        self,
        *,
        max_records: int = 4,
        max_age_ms: int | None = 1_000,
        eviction_policy: str = "oldest_first",
        fail_on_update: bool = False,
        fail_on_reset: bool = False,
        implementation_id: str = "recording_test",
        max_property_bytes: int | None = 4_096,
        max_serialized_bytes: int | None = 262_144,
        **_ignored,
    ) -> None:
        self.implementation_id = implementation_id
        self.bounds = MemoryBounds(
            max_records=max_records,
            max_age_ms=max_age_ms,
            eviction_policy=eviction_policy,
            max_property_bytes=max_property_bytes,
            max_serialized_bytes=max_serialized_bytes,
        )
        self.fail_on_update = fail_on_update
        self.fail_on_reset = fail_on_reset
        self.epoch = 0
        self.updates = 0
        self._snapshot = self.reset()

    def update(self, context, observation):
        if self.fail_on_update:
            raise RuntimeError("forced-update-failure")
        self.updates += 1
        if observation is None:
            self._snapshot = empty_memory_snapshot(
                memory_id=f"mem-{context.frame_id}",
                epoch_id=f"epoch-{self.epoch}",
                bounds=self.bounds,
                created_at_ms=context.timestamp_ms,
                implementation_id=self.implementation_id,
                summary=("memory_empty=true reason=no_observation",),
            )
            return self._snapshot
        record = RetainedEvidence(
            record_id=f"rec-{observation.observation_id}",
            kind="observation_presence",
            label="observed",
            confidence=1.0,
            provenance=MemoryProvenance(
                observation_id=observation.observation_id,
                evidence_id="observation",
                coordinate_frame="image",
                observed_at_ms=observation.created_at_ms,
                updated_at_ms=context.timestamp_ms,
                frame_id=context.frame_id,
            ),
            location=ViewLocation(frame="image", zone="center"),
        )
        self._snapshot = MemorySnapshot(
            memory_id=f"mem-{context.frame_id}",
            epoch_id=f"epoch-{self.epoch}",
            health="healthy",
            bounds=self.bounds,
            created_at_ms=context.timestamp_ms,
            records=(record,),
            summary=("retained_count=1",),
            implementation_id=self.implementation_id,
        )
        return self._snapshot

    def reset(self):
        if self.fail_on_reset:
            raise RuntimeError("reset exploded")
        self.epoch += 1
        self._snapshot = empty_memory_snapshot(
            memory_id=f"mem-reset-{self.epoch}",
            epoch_id=f"epoch-{self.epoch}",
            bounds=self.bounds,
            created_at_ms=0,
            implementation_id=self.implementation_id,
        )
        return self._snapshot

    def snapshot(self):
        return self._snapshot


class _OverCapacityMemory(_RecordingMemory):
    """Returns more records than the activation permits."""

    def update(self, context, observation):
        records = tuple(
            RetainedEvidence(
                record_id=f"rec-{index}",
                kind="observation_presence",
                label="observed",
                confidence=1.0,
                provenance=MemoryProvenance(
                    observation_id="obs",
                    evidence_id=f"e-{index}",
                    coordinate_frame="image",
                    observed_at_ms=1,
                    updated_at_ms=1,
                ),
            )
            for index in range(self.bounds.max_records + 1)
        )
        return MemorySnapshot(
            memory_id="bad",
            epoch_id="epoch-x",
            health="healthy",
            bounds=MemoryBounds(max_records=self.bounds.max_records + 1),
            created_at_ms=1,
            records=records,
            implementation_id=self.implementation_id,
        )


class _WeakAgeMemory(_RecordingMemory):
    """Reports a weaker age bound than the activation allows."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Keep internal config as activation requested, but report no age limit.
        self.bounds = MemoryBounds(
            max_records=self.bounds.max_records,
            max_age_ms=None,
            eviction_policy=self.bounds.eviction_policy,
        )


class _MutatingSharedSnapshotMemory(_RecordingMemory):
    """Returns the same snapshot object on successive reads (isolation probe)."""

    def update(self, context, observation):
        super().update(context, observation)
        return self._snapshot

    def snapshot(self):
        return self._snapshot


class _NonJsonPropertyMemory(_RecordingMemory):
    """Returns a healthy snapshot with a non-JSON property value."""

    def update(self, context, observation):
        from autonomy.decision import (
            MemoryProvenance,
            MemorySnapshot,
            RetainedEvidence,
        )

        del observation
        return MemorySnapshot(
            memory_id=f"mem-{context.frame_id}",
            epoch_id=f"epoch-{self.epoch}",
            health="healthy",
            bounds=self.bounds,
            created_at_ms=context.timestamp_ms,
            records=(
                RetainedEvidence(
                    record_id="rec-opaque",
                    kind="observation_presence",
                    label="opaque",
                    confidence=1.0,
                    provenance=MemoryProvenance(
                        observation_id="obs",
                        evidence_id="opaque",
                        coordinate_frame="image",
                        observed_at_ms=1,
                        updated_at_ms=context.timestamp_ms,
                        frame_id=context.frame_id,
                    ),
                    properties={"opaque": object()},
                ),
            ),
            implementation_id=self.implementation_id,
        )


class _ConfigurableIdMemory(_RecordingMemory):
    """Allows activation to declare a custom implementation_id (including multibyte)."""


class _BrokenStringError(RuntimeError):
    def __str__(self) -> str:
        raise RuntimeError("stringification failed")


class _BrokenStrMemory(_RecordingMemory):
    def __init__(self, **kwargs):
        self._armed = False
        super().__init__(**kwargs)
        self._armed = True

    def update(self, context, observation):
        if self._armed:
            raise _BrokenStringError("payload")
        return super().update(context, observation)

    def reset(self):
        if self._armed:
            raise _BrokenStringError("payload")
        return super().reset()

    def snapshot(self):
        if self._armed:
            raise _BrokenStringError("payload")
        return super().snapshot()


class _SelfContradictingBoundsMemory(_RecordingMemory):
    """Advertises a tight serialized ceiling but returns an oversized empty snapshot."""

    def update(self, context, observation):
        from autonomy.decision import MemoryBounds, empty_memory_snapshot

        del observation
        tight = MemoryBounds(
            max_records=self.bounds.max_records,
            max_age_ms=self.bounds.max_age_ms,
            eviction_policy=self.bounds.eviction_policy,
            max_property_bytes=self.bounds.max_property_bytes or 4_096,
            max_serialized_bytes=512,
        )
        # Inflate epoch so the body exceeds the advertised 512-byte ceiling while
        # still remaining under the default activation ceiling.
        return empty_memory_snapshot(
            memory_id=f"mem-{context.frame_id}",
            epoch_id="e" + ("x" * 1_200),
            bounds=tight,
            created_at_ms=context.timestamp_ms,
            implementation_id=self.implementation_id,
            summary=("memory_empty=true",),
            metadata={"pad": "y" * 200},
        )


class _NormalizationInflatesSizeMemory(_RecordingMemory):
    """Snapshot fits its short eviction_policy but is rejected for policy mismatch."""

    def update(self, context, observation):
        from autonomy.decision import MemoryBounds, empty_memory_snapshot

        del observation
        short_bounds = MemoryBounds(
            max_records=self.bounds.max_records,
            max_age_ms=self.bounds.max_age_ms,
            eviction_policy="x",
            max_property_bytes=self.bounds.max_property_bytes or 4_096,
            max_serialized_bytes=self.bounds.max_serialized_bytes or 600,
        )
        return empty_memory_snapshot(
            memory_id=f"mem-{context.frame_id}",
            epoch_id="epoch-1",
            bounds=short_bounds,
            created_at_ms=context.timestamp_ms,
            implementation_id=self.implementation_id,
            summary=("memory_empty=true",),
            metadata={},
        )


class _TighterDeclaredSizeMemory(_RecordingMemory):
    """Same policy label; declared size fields are smaller so normalize grows the body."""

    def update(self, context, observation):
        from autonomy.decision import MemoryBounds, empty_memory_snapshot

        del observation
        tighter = MemoryBounds(
            max_records=self.bounds.max_records,
            max_age_ms=self.bounds.max_age_ms,
            eviction_policy=self.bounds.eviction_policy,
            max_property_bytes=64,
            max_serialized_bytes=512,
        )
        # Pad so declared body is just under 512 and activation-normalized body exceeds 512.
        return empty_memory_snapshot(
            memory_id="m",
            epoch_id="e",
            bounds=tighter,
            created_at_ms=1,
            implementation_id=self.implementation_id,
            summary=("memory_empty=true",),
            metadata={"p": "x" * 144},
        )


class _NearCeilingThenFailMemory(_RecordingMemory):
    """First update returns a near-ceiling empty snapshot; later updates raise."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._updates_seen = 0

    def update(self, context, observation):
        from autonomy.decision import (
            empty_memory_snapshot,
            serialized_memory_snapshot_bytes,
        )

        del observation
        self._updates_seen += 1
        if self._updates_seen >= 2:
            raise RuntimeError("forced-after-near-ceiling")
        # Grow epoch_id until the empty snapshot sits just under the ceiling.
        limit = self.bounds.max_serialized_bytes or 2_000
        epoch = "e"
        snapshot = empty_memory_snapshot(
            memory_id="m",
            epoch_id=epoch,
            bounds=self.bounds,
            created_at_ms=context.timestamp_ms,
            implementation_id=self.implementation_id,
        )
        # Binary-ish growth: expand epoch while still fitting.
        while True:
            candidate_epoch = epoch + ("x" * 32)
            candidate = empty_memory_snapshot(
                memory_id="m",
                epoch_id=candidate_epoch,
                bounds=self.bounds,
                created_at_ms=context.timestamp_ms,
                implementation_id=self.implementation_id,
            )
            size = serialized_memory_snapshot_bytes(candidate)
            if size > limit - 12:
                break
            epoch = candidate_epoch
            snapshot = candidate
        # Fine-tune with single chars.
        while True:
            candidate_epoch = epoch + "y"
            candidate = empty_memory_snapshot(
                memory_id="m",
                epoch_id=candidate_epoch,
                bounds=self.bounds,
                created_at_ms=context.timestamp_ms,
                implementation_id=self.implementation_id,
            )
            size = serialized_memory_snapshot_bytes(candidate)
            if size > limit:
                break
            epoch = candidate_epoch
            snapshot = candidate
        self._snapshot = snapshot
        return snapshot


def _valid_payload() -> dict:
    return {
        "schema": MEMORY_ACTIVATION_SCHEMA,
        "memory": {
            "implementation_id": "recording_test",
            "implementation_spec": (
                "tests.autonomy.decision.memory_activation_fixtures:_RecordingMemory"
            ),
            "implementation_config": {
                "max_records": 4,
                "max_age_ms": 1_000,
                "eviction_policy": "oldest_first",
            },
        },
    }


def _write_payload(root: str, payload: object) -> Path:
    path = Path(root) / "active.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
