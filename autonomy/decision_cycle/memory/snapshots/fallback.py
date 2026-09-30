"""Framework-owned fallback snapshots.

When a memory plugin fails to update or reset, the framework publishes a
minimal error or empty snapshot with fixed-width identities. Activation checks
that configured bounds can hold those snapshots.
"""

from __future__ import annotations

import time

from autonomy.decision_cycle.memory.snapshots.values import (
    MemoryBounds,
    MemorySnapshot,
    empty_memory_snapshot,
    error_memory_snapshot,
    serialized_memory_snapshot_bytes,
)

# Fixed ASCII marker for framework-owned fallback snapshots. Never a truncated
# copy of the activation implementation_id (avoids multibyte / masquerading).
FRAMEWORK_FALLBACK_IMPLEMENTATION_ID = "framework"
# Fixed-width counter suffix keeps identity length independent of runtime growth.
FALLBACK_COUNTER_WIDTH = 10
# Millisecond timestamps are capped to 13 ASCII digits for a fixed JSON width.
MAX_FALLBACK_TIMESTAMP_MS = 9_999_999_999_999


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
