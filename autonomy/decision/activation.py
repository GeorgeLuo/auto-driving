"""Memory activation documents and framework-owned stage execution."""

from __future__ import annotations

import importlib
import json
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cycle import DecisionFrameContext
from .memory import (
    DEFAULT_MAX_DIAGNOSTIC_CHARS,
    DEFAULT_MAX_PROPERTY_BYTES,
    DEFAULT_MAX_SERIALIZED_BYTES,
    MemoryBounds,
    MemorySnapshot,
    detach_memory_snapshot,
    empty_memory_snapshot,
    error_memory_snapshot,
    serialized_mapping_bytes,
    serialized_memory_snapshot_bytes,
)
from .observation import Observation
from .plugin import MemoryImplementation


MEMORY_ACTIVATION_SCHEMA = "automa_memory_activation_v0"
DEFAULT_MAX_RECORDS = 32
DEFAULT_MAX_AGE_MS = 10_000
DEFAULT_EVICTION_POLICY = "oldest_first"
# Fixed ASCII marker for framework-owned fallback snapshots. Never a truncated
# copy of the activation implementation_id (avoids multibyte / masquerading).
FRAMEWORK_FALLBACK_IMPLEMENTATION_ID = "framework"
# Fixed-width counter suffix keeps identity length independent of runtime growth.
FALLBACK_COUNTER_WIDTH = 10
# Millisecond timestamps are capped to 13 ASCII digits for a fixed JSON width.
MAX_FALLBACK_TIMESTAMP_MS = 9_999_999_999_999


@dataclass(frozen=True)
class MemoryActivation:
    implementation_id: str
    implementation_spec: str
    implementation_config: dict[str, Any]
    bounds: MemoryBounds
    source_path: Path
    payload: dict[str, Any]


def read_memory_activation(path: Path) -> MemoryActivation:
    if not path.exists():
        raise FileNotFoundError(f"memory activation is missing: {path}")
    payload = json_load_object(path)
    if payload.get("schema") != MEMORY_ACTIVATION_SCHEMA:
        raise ValueError(
            f"memory activation has unsupported schema {payload.get('schema')!r}: {path}"
        )
    memory = payload.get("memory")
    if not isinstance(memory, dict):
        raise ValueError(f"memory activation has no memory section: {path}")

    implementation_id = memory.get("implementation_id")
    implementation_spec = memory.get("implementation_spec")
    implementation_config = memory.get("implementation_config")
    if not isinstance(implementation_id, str) or not implementation_id.strip():
        raise ValueError(f"memory activation has no implementation_id: {path}")
    if not isinstance(implementation_spec, str) or not implementation_spec.strip():
        raise ValueError(f"memory activation has no implementation_spec: {path}")
    if not isinstance(implementation_config, dict):
        raise ValueError(f"memory activation has invalid implementation_config: {path}")

    config = deepcopy(implementation_config)
    bounds = bounds_from_config(config)
    return MemoryActivation(
        implementation_id=implementation_id.strip(),
        implementation_spec=implementation_spec.strip(),
        implementation_config=config,
        bounds=bounds,
        source_path=path,
        payload=payload,
    )


def bounds_from_config(config: dict[str, Any]) -> MemoryBounds:
    max_records = config.get("max_records", DEFAULT_MAX_RECORDS)
    max_age_ms = config.get("max_age_ms", DEFAULT_MAX_AGE_MS)
    eviction_policy = config.get("eviction_policy", DEFAULT_EVICTION_POLICY)
    max_property_bytes = config.get("max_property_bytes", DEFAULT_MAX_PROPERTY_BYTES)
    max_serialized_bytes = config.get(
        "max_serialized_bytes", DEFAULT_MAX_SERIALIZED_BYTES
    )
    bounds = MemoryBounds(
        max_records=int(max_records),
        max_age_ms=int(max_age_ms) if max_age_ms is not None else None,
        eviction_policy=str(eviction_policy or DEFAULT_EVICTION_POLICY),
        max_property_bytes=(
            int(max_property_bytes) if max_property_bytes is not None else None
        ),
        max_serialized_bytes=(
            int(max_serialized_bytes) if max_serialized_bytes is not None else None
        ),
    )
    validate_framework_fallback_capacity(bounds)
    return bounds


def _format_fallback_counter(n: int) -> str:
    """Zero-pad counters so identity field width is independent of growth."""

    max_value = 10**FALLBACK_COUNTER_WIDTH - 1
    capped = max(0, min(int(n), max_value))
    return f"{capped:0{FALLBACK_COUNTER_WIDTH}d}"


def framework_fallback_timestamp_ms(now_ms: int | None = None) -> int:
    """Return a timestamp clamped to the fixed max width used in validation."""

    value = int(time.time() * 1000) if now_ms is None else int(now_ms)
    return max(0, min(value, MAX_FALLBACK_TIMESTAMP_MS))


def framework_error_identity(failure_count: int) -> tuple[str, str]:
    tag = _format_fallback_counter(failure_count)
    return f"memory-error-{tag}", f"epoch-error-{tag}"


def framework_reset_identity(reset_count: int) -> tuple[str, str]:
    tag = _format_fallback_counter(reset_count)
    return f"memory-reset-failed-{tag}", f"epoch-reset-failed-{tag}"


def build_minimal_framework_fallback(
    bounds: MemoryBounds,
    *,
    health: str,
    memory_id: str,
    epoch_id: str,
    created_at_ms: int,
    summary_prefix: str = "memory_error",
) -> MemorySnapshot:
    """Shared last-resort fallback shape used by validation and runtime."""

    if health == "error":
        return error_memory_snapshot(
            memory_id=memory_id,
            epoch_id=epoch_id,
            bounds=bounds,
            created_at_ms=created_at_ms,
            error="truncated",
            implementation_id=FRAMEWORK_FALLBACK_IMPLEMENTATION_ID,
            metadata={},
        )
    return empty_memory_snapshot(
        memory_id=memory_id,
        epoch_id=epoch_id,
        bounds=bounds,
        created_at_ms=created_at_ms,
        implementation_id=FRAMEWORK_FALLBACK_IMPLEMENTATION_ID,
        summary=(f"{summary_prefix}=truncated",),
        metadata={},
    )


def validate_framework_fallback_capacity(bounds: MemoryBounds) -> None:
    """Reject bounds that cannot host the activation-specific minimal fallbacks.

    Probes the same last-resort shapes runtime uses, with worst-case fixed-width
    identity counters and the maximum reserved timestamp width.
    """

    limit = bounds.max_serialized_bytes
    if limit is None:
        return

    # Worst-case identity width and timestamp width that runtime may emit.
    error_memory_id, error_epoch_id = framework_error_identity(
        10**FALLBACK_COUNTER_WIDTH - 1
    )
    reset_memory_id, reset_epoch_id = framework_reset_identity(
        10**FALLBACK_COUNTER_WIDTH - 1
    )
    created_at_ms = MAX_FALLBACK_TIMESTAMP_MS
    probes = (
        build_minimal_framework_fallback(
            bounds,
            health="error",
            memory_id=error_memory_id,
            epoch_id=error_epoch_id,
            created_at_ms=created_at_ms,
        ),
        build_minimal_framework_fallback(
            bounds,
            health="empty",
            memory_id=reset_memory_id,
            epoch_id=reset_epoch_id,
            created_at_ms=created_at_ms,
            summary_prefix="memory_reset_failed",
        ),
    )
    for snapshot in probes:
        size = serialized_memory_snapshot_bytes(snapshot)
        if size > limit:
            raise ValueError(
                "max_serialized_bytes="
                f"{limit} is too small for framework failure/reset snapshots "
                f"with these bounds (minimal fallback serializes to {size} bytes). "
                "Reduce bound-field size (for example eviction_policy) or raise "
                "max_serialized_bytes."
            )


def load_memory_implementation(
    activation: MemoryActivation,
    *,
    reload_module: bool = False,
) -> MemoryImplementation:
    return instantiate_memory_implementation(
        activation.implementation_spec,
        activation.implementation_config,
        expected_implementation_id=activation.implementation_id,
        reload_module=reload_module,
    )


def load_memory_stage_if_present(path: Path) -> ActivatedMemoryStage | None:
    """Load an activated memory stage when the activation document exists.

    Missing paths return None so Chase and Donkey hosts can share optional
    wiring without requiring memory before package activation exists.
    """

    if not path.exists():
        return None
    return ActivatedMemoryStage(read_memory_activation(path))


def instantiate_memory_implementation(
    implementation_spec: str,
    implementation_config: dict[str, Any],
    *,
    expected_implementation_id: str | None = None,
    reload_module: bool = False,
) -> MemoryImplementation:
    module_name, separator, class_name = implementation_spec.partition(":")
    if not separator or not module_name or not class_name:
        raise ValueError("memory implementation spec must be 'module.path:ClassName'")
    importlib.invalidate_caches()
    module = importlib.import_module(module_name)
    if reload_module:
        module = importlib.reload(module)
    implementation_cls = getattr(module, class_name)
    implementation = implementation_cls(**deepcopy(implementation_config))
    if not isinstance(implementation, MemoryImplementation):
        raise TypeError(
            "configured memory implementation does not satisfy MemoryImplementation: "
            f"{implementation_spec}"
        )
    if not isinstance(implementation.implementation_id, str) or not implementation.implementation_id.strip():
        raise TypeError(
            f"memory implementation must declare a non-empty implementation_id: {implementation_spec}"
        )
    if (
        expected_implementation_id is not None
        and implementation.implementation_id != expected_implementation_id
    ):
        raise ValueError(
            "memory implementation_id mismatch: activation declares "
            f"{expected_implementation_id!r} but loaded {implementation.implementation_id!r}"
        )
    return implementation


class ActivatedMemoryStage:
    """Decision-cycle memory stage backed by one activated implementation.

    The framework owns load, reset, timing, status, and failure isolation.
    Implementations only express update/reset/snapshot policy. Source
    observations are treated as read-only inputs; failures produce an error
    snapshot with no retained claims and do not raise into the cycle.
    """

    def __init__(self, activation: MemoryActivation) -> None:
        self.activation = activation
        self.implementation = load_memory_implementation(activation)
        self.last_snapshot: MemorySnapshot | None = None
        self.last_duration_ms: float | None = None
        self.last_error: str | None = None
        self.update_count = 0
        self.reset_count = 0
        self.failure_count = 0
        self.last_snapshot = self.reset()

    def __call__(
        self,
        context: DecisionFrameContext,
        observation: Observation | None,
    ) -> MemorySnapshot:
        return self.update(context, observation)

    def update(
        self,
        context: DecisionFrameContext,
        observation: Observation | None,
    ) -> MemorySnapshot:
        started = time.perf_counter()
        try:
            # Observations are frozen; deepcopy defensive metadata only if needed
            # by refusing mutation contracts: never pass writable shared state.
            snapshot = self.implementation.update(context, observation)
            owned = self._accept_snapshot(snapshot, operation="update")
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 - stage isolation boundary
            self.failure_count += 1
            self.last_error = self._bound_diagnostic(format_exception_safely(exc))
            owned = self._error_snapshot(self.last_error)
        self.last_duration_ms = (time.perf_counter() - started) * 1000.0
        self.update_count += 1
        return self._publish_snapshot(owned)

    def reset(self) -> MemorySnapshot:
        started = time.perf_counter()
        try:
            snapshot = self.implementation.reset()
            owned = self._accept_snapshot(snapshot, operation="reset")
            if owned.health not in {"empty", "unavailable"}:
                raise ValueError(
                    "memory reset must return empty or unavailable health; "
                    f"got {owned.health!r}"
                )
            if owned.records:
                raise ValueError("memory reset must not retain records")
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 - stage isolation boundary
            self.failure_count += 1
            self.last_error = self._bound_diagnostic(format_exception_safely(exc))
            memory_id, epoch_id = framework_reset_identity(self.reset_count + 1)
            owned = self._bounded_fallback_snapshot(
                memory_id=memory_id,
                epoch_id=epoch_id,
                health="empty",
                error=self.last_error,
                summary_prefix="memory_reset_failed",
                metadata_key="reset_error",
            )
        self.last_duration_ms = (time.perf_counter() - started) * 1000.0
        self.reset_count += 1
        return self._publish_snapshot(owned)

    def snapshot(self) -> MemorySnapshot:
        try:
            current = self.implementation.snapshot()
            owned = self._accept_snapshot(current, operation="snapshot")
        except Exception as exc:  # noqa: BLE001 - stage isolation boundary
            self.failure_count += 1
            self.last_error = self._bound_diagnostic(format_exception_safely(exc))
            owned = self._error_snapshot(self.last_error)
        return self._publish_snapshot(owned)

    def status(self) -> dict[str, Any]:
        last = self.last_snapshot
        return {
            "implementation_id": self.activation.implementation_id,
            "implementation_spec": self.activation.implementation_spec,
            "activation": str(self.activation.source_path),
            "bounds": self.activation.bounds.to_dict(),
            "update_count": self.update_count,
            "reset_count": self.reset_count,
            "failure_count": self.failure_count,
            "last_duration_ms": self.last_duration_ms,
            "last_error": self.last_error,
            "last_health": last.health if last is not None else None,
            "last_epoch_id": last.epoch_id if last is not None else None,
            "last_record_count": last.record_count if last is not None else None,
        }

    def _accept_snapshot(
        self,
        snapshot: MemorySnapshot,
        *,
        operation: str,
    ) -> MemorySnapshot:
        if not isinstance(snapshot, MemorySnapshot):
            raise TypeError(
                f"memory {operation} must return MemorySnapshot; "
                f"got {type(snapshot).__name__}"
            )
        if snapshot.implementation_id not in (
            None,
            self.activation.implementation_id,
        ):
            raise ValueError(
                "memory snapshot implementation_id "
                f"{snapshot.implementation_id!r} does not match activation "
                f"{self.activation.implementation_id!r}"
            )
        configured = self.activation.bounds
        declared = snapshot.bounds
        if declared.max_records > configured.max_records:
            raise ValueError(
                "memory snapshot max_records "
                f"{declared.max_records} exceeds activation max_records "
                f"{configured.max_records}"
            )
        if snapshot.record_count > configured.max_records:
            raise ValueError(
                f"memory snapshot retains {snapshot.record_count} records but "
                f"activation max_records is {configured.max_records}"
            )
        # Reject removed or weakened age/size bounds. None is weaker than a
        # finite activation ceiling; a larger window/limit is also weaker.
        if configured.max_age_ms is not None:
            if declared.max_age_ms is None:
                raise ValueError(
                    "memory snapshot removed max_age_ms while activation requires "
                    f"max_age_ms={configured.max_age_ms}"
                )
            if declared.max_age_ms > configured.max_age_ms:
                raise ValueError(
                    "memory snapshot max_age_ms "
                    f"{declared.max_age_ms} exceeds activation max_age_ms "
                    f"{configured.max_age_ms}"
                )
        if configured.max_property_bytes is not None:
            if declared.max_property_bytes is None:
                raise ValueError(
                    "memory snapshot removed max_property_bytes while activation "
                    f"requires max_property_bytes={configured.max_property_bytes}"
                )
            if declared.max_property_bytes > configured.max_property_bytes:
                raise ValueError(
                    "memory snapshot max_property_bytes "
                    f"{declared.max_property_bytes} exceeds activation "
                    f"max_property_bytes={configured.max_property_bytes}"
                )
        if configured.max_serialized_bytes is not None:
            if declared.max_serialized_bytes is None:
                raise ValueError(
                    "memory snapshot removed max_serialized_bytes while activation "
                    f"requires max_serialized_bytes={configured.max_serialized_bytes}"
                )
            if declared.max_serialized_bytes > configured.max_serialized_bytes:
                raise ValueError(
                    "memory snapshot max_serialized_bytes "
                    f"{declared.max_serialized_bytes} exceeds activation "
                    f"max_serialized_bytes={configured.max_serialized_bytes}"
                )

        # Enforce the tighter of activation and declared property ceilings.
        property_limit = configured.max_property_bytes
        if declared.max_property_bytes is not None:
            property_limit = (
                declared.max_property_bytes
                if property_limit is None
                else min(property_limit, declared.max_property_bytes)
            )
        if property_limit is not None:
            for record in snapshot.records:
                size = serialized_mapping_bytes(record.properties)
                if size > property_limit:
                    raise ValueError(
                        "memory record "
                        f"{record.record_id!r} properties are {size} bytes; "
                        f"allowed max_property_bytes is {property_limit}"
                    )

        # Detach, measure total size against activation and declared ceilings,
        # then normalize bounds to the authoritative activation policy.
        detached = detach_memory_snapshot(snapshot)
        size = serialized_memory_snapshot_bytes(detached)
        if (
            configured.max_serialized_bytes is not None
            and size > configured.max_serialized_bytes
        ):
            raise ValueError(
                f"memory snapshot serializes to {size} bytes; "
                f"activation max_serialized_bytes is {configured.max_serialized_bytes}"
            )
        if (
            declared.max_serialized_bytes is not None
            and size > declared.max_serialized_bytes
        ):
            raise ValueError(
                f"memory snapshot serializes to {size} bytes but declares "
                f"max_serialized_bytes={declared.max_serialized_bytes}"
            )
        if detached.bounds == configured:
            return detached
        return detach_memory_snapshot(
            MemorySnapshot(
                memory_id=detached.memory_id,
                epoch_id=detached.epoch_id,
                health=detached.health,
                bounds=configured,
                created_at_ms=detached.created_at_ms,
                records=detached.records,
                summary=detached.summary,
                implementation_id=detached.implementation_id,
                error=detached.error,
                metadata=detached.metadata,
                schema=detached.schema,
            )
        )

    def _publish_snapshot(self, owned: MemorySnapshot) -> MemorySnapshot:
        """Store stage-owned state and return a second detached caller copy."""

        self.last_snapshot = owned
        return detach_memory_snapshot(owned)

    def _bound_diagnostic(self, message: str) -> str:
        """Truncate diagnostics once for last_error, status, and fallbacks."""

        limit = self.activation.bounds.max_serialized_bytes
        # Keep status/worker-facing text modest even when snapshot ceiling is large.
        budget = DEFAULT_MAX_DIAGNOSTIC_CHARS
        if limit is not None:
            budget = max(64, min(budget, limit // 4))
        return _truncate_text(str(message), budget)

    def _error_snapshot(self, error: str) -> MemorySnapshot:
        previous = self.last_snapshot
        # Identity is framework-owned and fixed-width. Never reuse prior epoch_id /
        # memory_id values — a near-ceiling accepted snapshot can make those
        # fields too large for a failure fallback under the same byte limit.
        memory_id, epoch_id = framework_error_identity(self.failure_count)
        return self._bounded_fallback_snapshot(
            memory_id=memory_id,
            epoch_id=epoch_id,
            health="error",
            error=error,
            summary_prefix="memory_error",
            metadata_key="error",
            previous_health=previous.health if previous is not None else None,
        )

    def _bounded_fallback_snapshot(
        self,
        *,
        memory_id: str,
        epoch_id: str,
        health: str,
        error: str | None,
        summary_prefix: str,
        metadata_key: str,
        previous_health: str | None = None,
    ) -> MemorySnapshot:
        """Build a framework failure/reset-fallback snapshot under size limits.

        Identity and timestamp width match the shapes validated at activation.
        """

        configured = self.activation.bounds
        limit = configured.max_serialized_bytes
        created_at_ms = framework_fallback_timestamp_ms()
        safe_impl_id = FRAMEWORK_FALLBACK_IMPLEMENTATION_ID
        safe_previous_health = (
            previous_health
            if previous_health in {"empty", "healthy", "unavailable", "error"}
            else None
        )

        # Prefer the already-bounded diagnostic from the exception boundary.
        base_diagnostic = str(error or "unknown failure")
        diagnostic_budget = len(base_diagnostic) if base_diagnostic else 64
        if limit is not None:
            diagnostic_budget = max(16, min(diagnostic_budget, limit // 4, 4_096))

        budgets: list[int] = [diagnostic_budget]
        for candidate_budget in (256, 128, 64, 32, 16):
            if candidate_budget not in budgets and candidate_budget <= diagnostic_budget:
                budgets.append(candidate_budget)

        for budget in budgets:
            diagnostic = _truncate_text(base_diagnostic, budget)
            candidate = self._build_fallback_candidate(
                health=health,
                memory_id=memory_id,
                epoch_id=epoch_id,
                implementation_id=safe_impl_id,
                diagnostic=diagnostic,
                summary_prefix=summary_prefix,
                metadata_key=metadata_key,
                previous_health=safe_previous_health,
                include_metadata=True,
                created_at_ms=created_at_ms,
            )
            if limit is None or serialized_memory_snapshot_bytes(candidate) <= limit:
                return detach_memory_snapshot(candidate)

        # Last resort: same shared shape measured at activation time.
        candidate = build_minimal_framework_fallback(
            configured,
            health=health,
            memory_id=memory_id,
            epoch_id=epoch_id,
            created_at_ms=created_at_ms,
            summary_prefix=summary_prefix,
        )
        if limit is not None and serialized_memory_snapshot_bytes(candidate) > limit:
            # Unreachable when activation validated fallback capacity.
            raise ValueError(
                "framework could not construct a failure snapshot under "
                f"max_serialized_bytes={limit}"
            )
        return detach_memory_snapshot(candidate)

    def _build_fallback_candidate(
        self,
        *,
        health: str,
        memory_id: str,
        epoch_id: str,
        implementation_id: str,
        diagnostic: str,
        summary_prefix: str,
        metadata_key: str,
        previous_health: str | None,
        include_metadata: bool,
        created_at_ms: int,
    ) -> MemorySnapshot:
        configured = self.activation.bounds
        if health == "error":
            metadata: dict[str, Any] = {}
            if include_metadata:
                metadata = {
                    "failure_count": self.failure_count,
                    "previous_health": previous_health,
                }
            return error_memory_snapshot(
                memory_id=memory_id,
                epoch_id=epoch_id,
                bounds=configured,
                created_at_ms=created_at_ms,
                error=diagnostic,
                implementation_id=implementation_id,
                metadata=metadata,
            )
        metadata = {metadata_key: diagnostic} if include_metadata else {}
        return empty_memory_snapshot(
            memory_id=memory_id,
            epoch_id=epoch_id,
            bounds=configured,
            created_at_ms=created_at_ms,
            implementation_id=implementation_id,
            summary=(f"{summary_prefix}={diagnostic}",),
            metadata=metadata,
        )


def json_load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"memory activation must be a JSON object: {path}")
    return payload


def format_exception_safely(exc: BaseException) -> str:
    """Format an exception without letting ``__str__`` bypass isolation."""

    type_name = type(exc).__name__
    try:
        detail = str(exc)
    except Exception:  # noqa: BLE001 - secondary failure must not escape
        return f"{type_name}: <unprintable exception>"
    return f"{type_name}: {detail}"


def _truncate_text(value: str, max_chars: int) -> str:
    text = str(value)
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3] + "..."


def _timestamp_ms() -> int:
    return int(time.time() * 1000)
