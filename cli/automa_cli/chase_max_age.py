"""Chase live max-age expiry proof for memory check (no reset, no movement)."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from implementations.vehicle.chase_sim.frame_identity import format_chase_frame_id

# Always-on camera/floor evidence keeps refreshing and is not a max-age proof.
_ALWAYS_ON_KEY_MARKERS = (
    "front_camera_frame",
    "front_camera_available",
    "traversable_floor",
    "floor_visible",
    "camera_frame",
)


def is_chase_always_on_key(record_id: str) -> bool:
    lowered = record_id.lower()
    return any(marker in lowered for marker in _ALWAYS_ON_KEY_MARKERS)


def _chase_frame_index(frame: dict[str, Any]) -> int | None:
    raw = frame.get("simulator_frame_index")
    if raw is None:
        raw = frame.get("frame_index")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def record_ids_from_memory(memory: dict[str, Any]) -> set[str]:
    records = memory.get("records") if isinstance(memory.get("records"), list) else []
    return {
        str(record.get("record_id"))
        for record in records
        if isinstance(record, dict) and record.get("record_id")
    }


def extract_chase_lifecycle_keys(frames: list[dict[str, Any]]) -> set[str]:
    """Return retained-prior record ids suitable for max-age expiry proof."""

    observed_index: dict[str, int] = {}
    lifecycle: set[str] = set()
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        containing_index = _chase_frame_index(frame)
        containing_frame_id = str(frame.get("frame_id") or "").strip()
        if containing_index is None:
            continue
        if containing_frame_id:
            observed_index[containing_frame_id] = containing_index
        observed_index[format_chase_frame_id(containing_index)] = containing_index

        memory = frame.get("memory") if isinstance(frame.get("memory"), dict) else None
        if memory is None:
            continue
        records = memory.get("records") if isinstance(memory.get("records"), list) else []
        for record in records:
            if not isinstance(record, dict):
                continue
            record_id = str(record.get("record_id") or "").strip()
            if not record_id or is_chase_always_on_key(record_id):
                continue
            provenance = (
                record.get("provenance")
                if isinstance(record.get("provenance"), dict)
                else {}
            )
            prov_frame = str(provenance.get("frame_id") or "").strip()
            if not prov_frame:
                continue
            source_index = observed_index.get(prov_frame)
            if source_index is None:
                continue
            if source_index < containing_index:
                lifecycle.add(record_id)
    return lifecycle


def parse_required_max_age_ms(bounds: Any) -> int:
    """Require a positive configured max_age_ms (no silent fallback)."""

    if not isinstance(bounds, dict):
        raise ValueError("memory bounds are missing; cannot prove max-age expiry")
    raw = bounds.get("max_age_ms")
    if raw is None:
        raise ValueError("bounds.max_age_ms is missing; cannot prove max-age expiry")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bounds.max_age_ms is invalid: {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"bounds.max_age_ms must be positive; got {value}")
    return value


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _record_updated_at_ms(record: dict[str, Any]) -> int | None:
    for key in ("updated_at_ms", "observed_at_ms"):
        if key in record:
            parsed = _optional_int(record.get(key))
            if parsed is not None:
                return parsed
    provenance = record.get("provenance")
    if isinstance(provenance, dict):
        for key in ("updated_at_ms", "observed_at_ms"):
            if key in provenance:
                parsed = _optional_int(provenance.get(key))
                if parsed is not None:
                    return parsed
    return None


def lifecycle_key_anchors_ms(
    frames: list[dict[str, Any]],
    lifecycle_keys: set[str],
) -> dict[str, int]:
    """Last known update time for each lifecycle key while present in sampled frames."""

    anchors: dict[str, int] = {}
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        frame_ts = _optional_int(frame.get("timestamp_ms"))
        memory = frame.get("memory") if isinstance(frame.get("memory"), dict) else None
        if memory is None:
            continue
        records = memory.get("records") if isinstance(memory.get("records"), list) else []
        for record in records:
            if not isinstance(record, dict):
                continue
            record_id = str(record.get("record_id") or "").strip()
            if record_id not in lifecycle_keys:
                continue
            updated = _record_updated_at_ms(record)
            if updated is None:
                updated = frame_ts
            if updated is None:
                continue
            prior = anchors.get(record_id)
            if prior is None or updated > prior:
                anchors[record_id] = updated
    return anchors


def frame_control_is_strict_zero(frame: dict[str, Any]) -> tuple[bool, str | None]:
    """Require explicit observe-only zero control; missing fields fail."""

    control = frame.get("control")
    if not isinstance(control, dict):
        return False, "control payload missing"
    if control.get("applied") is not False:
        return False, f"control.applied={control.get('applied')!r} (require False)"
    for axis in ("steering", "throttle"):
        if axis not in control:
            return False, f"control.{axis} missing"
        value = control[axis]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            return False, f"control.{axis} invalid"
        if abs(float(value)) > 1e-9:
            return False, f"control.{axis}={value} (require zero)"
    action_policy = str(frame.get("action_policy") or "")
    if action_policy and action_policy != "observe_only":
        return False, f"action_policy={action_policy}"
    application = str(frame.get("control_application") or "")
    if application and application != "not_applied":
        return False, f"control_application={application}"
    return True, None


def require_valid_memory(frame: dict[str, Any]) -> dict[str, Any]:
    """Memory must be a dict with a list records field (may be empty)."""

    if "memory" not in frame:
        raise ValueError("frame is missing memory")
    memory = frame.get("memory")
    if not isinstance(memory, dict):
        raise ValueError("frame.memory is not an object")
    if "records" not in memory:
        raise ValueError("frame.memory.records is missing")
    if not isinstance(memory.get("records"), list):
        raise ValueError("frame.memory.records is not a list")
    return memory


@dataclass
class ChaseMaxAgeWaitResult:
    passed: bool
    reason: str
    expiry_frame: dict[str, Any] | None
    expiry_memory: dict[str, Any] | None
    wait_frames: list[dict[str, Any]] = field(default_factory=list)
    score: dict[str, Any] = field(default_factory=dict)


def score_chase_max_age_expiry(
    *,
    lifecycle_keys: set[str],
    final_memory: dict[str, Any],
    control_ok: bool,
    reset_used: bool,
    max_age_ms: int | None,
    age_elapsed_ms: int | None,
    reset_count_stable: bool,
    epoch_stable: bool,
    frames_advanced: bool,
    capacity_eviction_ambiguous: bool,
) -> dict[str, Any]:
    """Score a completed max-age wait against the live-expiry invariant."""

    remaining = lifecycle_keys.intersection(record_ids_from_memory(final_memory))
    age_ok = (
        max_age_ms is not None
        and max_age_ms > 0
        and age_elapsed_ms is not None
        and age_elapsed_ms >= max_age_ms
    )
    passed = (
        bool(lifecycle_keys)
        and not remaining
        and control_ok
        and not reset_used
        and age_ok
        and reset_count_stable
        and epoch_stable
        and frames_advanced
        and not capacity_eviction_ambiguous
    )
    if not lifecycle_keys:
        reason = "no lifecycle keys to expire"
    elif max_age_ms is None or max_age_ms <= 0:
        reason = "configured max_age_ms is missing or invalid"
    elif reset_used:
        reason = "reset was used; max-age expiry must not rely on reset"
    elif not control_ok:
        reason = "control was non-zero or incomplete during max-age wait"
    elif not reset_count_stable:
        reason = "reset_count changed during max-age wait"
    elif not epoch_stable:
        reason = "memory epoch changed during max-age wait"
    elif not frames_advanced:
        reason = "simulator frames did not advance during max-age wait"
    elif capacity_eviction_ambiguous:
        reason = "key loss may be capacity eviction rather than max-age expiry"
    elif remaining:
        reason = f"lifecycle keys still present after wait: {sorted(remaining)}"
    elif not age_ok:
        reason = (
            f"keys left before max-age elapsed "
            f"(age_elapsed_ms={age_elapsed_ms}, max_age_ms={max_age_ms})"
        )
    else:
        reason = (
            "lifecycle keys left live memory after max-age without reset "
            f"(keys={sorted(lifecycle_keys)}, age_elapsed_ms={age_elapsed_ms}, "
            f"max_age_ms={max_age_ms})"
        )
    return {
        "passed": passed,
        "reason": reason,
        "lifecycle_keys": sorted(lifecycle_keys),
        "remaining_keys": sorted(remaining),
        "control_ok": control_ok,
        "reset_used": reset_used,
        "max_age_ms": max_age_ms,
        "age_elapsed_ms": age_elapsed_ms,
        "reset_count_stable": reset_count_stable,
        "epoch_stable": epoch_stable,
        "frames_advanced": frames_advanced,
        "capacity_eviction_ambiguous": capacity_eviction_ambiguous,
        "record_ids": sorted(record_ids_from_memory(final_memory)),
    }


def wait_for_chase_memory_key_expiry(
    *,
    load_latest_frame: Callable[[], dict[str, Any] | None],
    probe_fn: Callable[[], dict[str, Any]],
    present_keys: set[str],
    max_age_ms: int,
    timeout_s: float,
    key_anchors_ms: dict[str, int],
    baseline_reset_count: int | None,
    baseline_epoch_id: str | None,
    max_records: int | None = None,
) -> ChaseMaxAgeWaitResult:
    """Poll live Chase frames until lifecycle keys age out without reset/movement."""

    if not present_keys:
        raise ValueError("present_keys must be non-empty")
    if max_age_ms <= 0:
        raise ValueError("max_age_ms must be positive")

    deadline = time.monotonic() + max(1.0, float(timeout_s))
    wait_frames: list[dict[str, Any]] = []
    first_index: int | None = None
    max_index: int | None = None
    saw_keys_present = False
    anchors = dict(key_anchors_ms)
    wait_start_wall_ms = int(time.time() * 1000)
    last_error: str | None = None

    while time.monotonic() < deadline:
        frame = load_latest_frame()
        if not isinstance(frame, dict):
            last_error = "load_latest_frame returned non-object"
            time.sleep(0.2)
            continue

        try:
            memory = require_valid_memory(frame)
        except ValueError as exc:
            last_error = str(exc)
            time.sleep(0.2)
            continue

        control_ok, control_reason = frame_control_is_strict_zero(frame)
        if not control_ok:
            return ChaseMaxAgeWaitResult(
                passed=False,
                reason=f"movement or incomplete control during max-age wait: {control_reason}",
                expiry_frame=frame,
                expiry_memory=memory,
                wait_frames=wait_frames + [frame],
                score=score_chase_max_age_expiry(
                    lifecycle_keys=present_keys,
                    final_memory=memory,
                    control_ok=False,
                    reset_used=False,
                    max_age_ms=max_age_ms,
                    age_elapsed_ms=None,
                    reset_count_stable=True,
                    epoch_stable=True,
                    frames_advanced=False,
                    capacity_eviction_ambiguous=False,
                ),
            )

        probe = probe_fn()
        if not isinstance(probe, dict) or probe.get("status") != "live":
            last_error = (
                f"worker not live during max-age wait "
                f"(status={None if not isinstance(probe, dict) else probe.get('status')})"
            )
            time.sleep(0.2)
            continue

        reset_count = _optional_int(probe.get("reset_count"))
        epoch_id = str(probe.get("last_epoch_id") or "") or None
        if baseline_reset_count is not None and reset_count is not None:
            if reset_count != baseline_reset_count:
                return ChaseMaxAgeWaitResult(
                    passed=False,
                    reason=(
                        f"reset_count changed during max-age wait "
                        f"({baseline_reset_count} -> {reset_count})"
                    ),
                    expiry_frame=frame,
                    expiry_memory=memory,
                    wait_frames=wait_frames + [frame],
                    score=score_chase_max_age_expiry(
                        lifecycle_keys=present_keys,
                        final_memory=memory,
                        control_ok=True,
                        reset_used=True,
                        max_age_ms=max_age_ms,
                        age_elapsed_ms=None,
                        reset_count_stable=False,
                        epoch_stable=True,
                        frames_advanced=False,
                        capacity_eviction_ambiguous=False,
                    ),
                )
        if baseline_epoch_id is not None and epoch_id is not None:
            if epoch_id != baseline_epoch_id:
                return ChaseMaxAgeWaitResult(
                    passed=False,
                    reason=(
                        f"memory epoch changed during max-age wait "
                        f"({baseline_epoch_id!r} -> {epoch_id!r})"
                    ),
                    expiry_frame=frame,
                    expiry_memory=memory,
                    wait_frames=wait_frames + [frame],
                    score=score_chase_max_age_expiry(
                        lifecycle_keys=present_keys,
                        final_memory=memory,
                        control_ok=True,
                        reset_used=False,
                        max_age_ms=max_age_ms,
                        age_elapsed_ms=None,
                        reset_count_stable=True,
                        epoch_stable=False,
                        frames_advanced=False,
                        capacity_eviction_ambiguous=False,
                    ),
                )

        index = _chase_frame_index(frame)
        if index is not None:
            if first_index is None:
                first_index = index
            max_index = index if max_index is None else max(max_index, index)

        present_ids = record_ids_from_memory(memory)
        remaining = present_keys.intersection(present_ids)
        if remaining:
            saw_keys_present = True
            frame_ts = _optional_int(frame.get("timestamp_ms")) or int(time.time() * 1000)
            for record in memory.get("records") or []:
                if not isinstance(record, dict):
                    continue
                rid = str(record.get("record_id") or "")
                if rid not in present_keys:
                    continue
                updated = _record_updated_at_ms(record) or frame_ts
                prior = anchors.get(rid)
                if prior is None or updated > prior:
                    anchors[rid] = updated

        wait_frames.append(frame)

        if remaining:
            # Capacity eviction ambiguity: full ledger and new keys while lifecycle keys drop.
            if (
                max_records is not None
                and max_records > 0
                and len(present_ids) >= max_records
                and not present_keys.issubset(present_ids)
            ):
                # Only fail if keys already missing before age elapsed.
                now_ms = int(time.time() * 1000)
                min_age = None
                for key in present_keys - present_ids:
                    anchor = anchors.get(key, wait_start_wall_ms)
                    age = now_ms - anchor
                    min_age = age if min_age is None else min(min_age, age)
                if min_age is not None and min_age < max_age_ms:
                    return ChaseMaxAgeWaitResult(
                        passed=False,
                        reason=(
                            "lifecycle key loss while ledger at max_records before "
                            "max-age elapsed (capacity eviction ambiguous)"
                        ),
                        expiry_frame=frame,
                        expiry_memory=memory,
                        wait_frames=wait_frames,
                        score=score_chase_max_age_expiry(
                            lifecycle_keys=present_keys,
                            final_memory=memory,
                            control_ok=True,
                            reset_used=False,
                            max_age_ms=max_age_ms,
                            age_elapsed_ms=min_age,
                            reset_count_stable=True,
                            epoch_stable=True,
                            frames_advanced=(
                                first_index is not None
                                and max_index is not None
                                and max_index > first_index
                            ),
                            capacity_eviction_ambiguous=True,
                        ),
                    )
            time.sleep(0.25)
            continue

        # Keys gone.
        if not saw_keys_present:
            # Never observed keys present during wait — reject stale/keyless frames.
            last_error = "lifecycle keys never observed present during wait (stale or keyless frame)"
            time.sleep(0.2)
            continue

        frames_advanced = (
            first_index is not None
            and max_index is not None
            and max_index > first_index
        )
        now_ms = int(time.time() * 1000)
        ages = []
        for key in present_keys:
            anchor = anchors.get(key, wait_start_wall_ms)
            ages.append(now_ms - anchor)
        age_elapsed_ms = min(ages) if ages else now_ms - wait_start_wall_ms
        score = score_chase_max_age_expiry(
            lifecycle_keys=present_keys,
            final_memory=memory,
            control_ok=True,
            reset_used=False,
            max_age_ms=max_age_ms,
            age_elapsed_ms=age_elapsed_ms,
            reset_count_stable=True,
            epoch_stable=True,
            frames_advanced=frames_advanced,
            capacity_eviction_ambiguous=False,
        )
        return ChaseMaxAgeWaitResult(
            passed=bool(score["passed"]),
            reason=str(score["reason"]),
            expiry_frame=frame,
            expiry_memory=memory,
            wait_frames=wait_frames,
            score=score,
        )

    detail = f" last_error={last_error}" if last_error else ""
    raise TimeoutError(
        f"live Chase memory did not complete max-age expiry within {timeout_s}s.{detail}"
    )
