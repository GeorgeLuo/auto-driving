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


def frame_simulation_epoch(frame: dict[str, Any]) -> str | None:
    """Stable simulation identity from a Chase evaluation frame."""

    raw = frame.get("simulation_epoch")
    if raw is not None and str(raw).strip():
        return str(raw).strip()
    observation = frame.get("observation")
    if isinstance(observation, dict):
        snapshot = observation.get("sensor_snapshot")
        if isinstance(snapshot, dict):
            metadata = snapshot.get("metadata")
            if isinstance(metadata, dict):
                meta_epoch = metadata.get("simulation_epoch")
                if meta_epoch is not None and str(meta_epoch).strip():
                    return str(meta_epoch).strip()
    shadow = frame.get("shadow_reference")
    if isinstance(shadow, dict):
        shadow_epoch = shadow.get("simulation_epoch")
        if shadow_epoch is not None and str(shadow_epoch).strip():
            return str(shadow_epoch).strip()
    return None


def frame_memory_epoch_id(frame: dict[str, Any]) -> str | None:
    """Memory generation identity published on the evaluation frame."""

    memory = frame.get("memory")
    if not isinstance(memory, dict):
        return None
    epoch = str(memory.get("epoch_id") or "").strip()
    return epoch or None


def frame_run_id(frame: dict[str, Any]) -> str | None:
    raw = frame.get("run_id")
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def frame_worker_pid(frame: dict[str, Any]) -> int | None:
    return _optional_int(frame.get("worker_pid"))


def frame_capacity_eviction_count(frame: dict[str, Any]) -> int | None:
    """Authoritative capacity-eviction total from frame memory metadata."""

    memory = frame.get("memory")
    if not isinstance(memory, dict):
        return None
    metadata = memory.get("metadata")
    if isinstance(metadata, dict) and metadata.get("capacity_eviction_count") is not None:
        return _optional_int(metadata.get("capacity_eviction_count"))
    return _optional_int(memory.get("capacity_eviction_count"))


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


def parse_required_max_records(bounds: Any) -> int:
    """Require a positive configured max_records so capacity eviction can be ruled out."""

    if not isinstance(bounds, dict):
        raise ValueError("memory bounds are missing; cannot prove max-age expiry")
    raw = bounds.get("max_records")
    if raw is None:
        raise ValueError("bounds.max_records is missing; cannot prove max-age expiry")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bounds.max_records is invalid: {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"bounds.max_records must be positive; got {value}")
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
    if "action_policy" not in frame or frame.get("action_policy") in (None, ""):
        return False, "action_policy missing"
    action_policy = str(frame.get("action_policy"))
    if action_policy != "observe_only":
        return False, f"action_policy={action_policy}"
    if "control_application" not in frame or frame.get("control_application") in (None, ""):
        return False, "control_application missing"
    application = str(frame.get("control_application"))
    if application != "not_applied":
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


@dataclass(frozen=True)
class ChaseMaxAgeIdentity:
    """Worker + simulation identity that must remain continuous through expiry."""

    worker_pid: int
    run_id: str
    reset_count: int
    memory_epoch_id: str
    simulation_epoch: str
    capacity_eviction_count: int


def require_chase_max_age_identity(
    probe: Any,
    frame: dict[str, Any] | None,
) -> ChaseMaxAgeIdentity:
    """Fail closed when probe or frame cannot establish continuous identity.

    Continuity requires an immutable automation ``run_id`` (not just memory epoch
    strings, which restart at ``epoch-1``), matching frame/probe memory epochs,
    and a known capacity-eviction counter baseline.
    """

    if not isinstance(probe, dict):
        raise ValueError("live probe is missing; cannot prove max-age identity continuity")
    if probe.get("status") != "live":
        raise ValueError(
            f"worker not live at max-age baseline (status={probe.get('status')!r})"
        )
    pid = probe.get("worker_pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError(
            "worker_pid is missing or invalid on live probe; cannot prove worker continuity"
        )
    run_id = str(probe.get("run_id") or "").strip()
    if not run_id:
        raise ValueError(
            "run_id is missing on live probe; cannot prove automation generation continuity"
        )
    reset_count = _optional_int(probe.get("reset_count"))
    if reset_count is None:
        raise ValueError(
            "reset_count is missing on live probe; cannot prove reset did not clear memory"
        )
    epoch = str(probe.get("last_epoch_id") or "").strip()
    if not epoch:
        raise ValueError(
            "last_epoch_id is missing on live probe; cannot prove memory epoch continuity"
        )
    probe_evictions = _optional_int(probe.get("capacity_eviction_count"))
    if probe_evictions is None or probe_evictions < 0:
        raise ValueError(
            "capacity_eviction_count is missing on live probe; "
            "cannot prove age expiry versus capacity eviction"
        )
    if not isinstance(frame, dict):
        raise ValueError(
            "baseline frame is missing; cannot prove simulation epoch continuity"
        )
    simulation_epoch = frame_simulation_epoch(frame)
    if not simulation_epoch:
        raise ValueError(
            "simulation_epoch is missing on baseline frame; cannot prove simulation continuity"
        )
    frame_epoch = frame_memory_epoch_id(frame)
    if not frame_epoch:
        raise ValueError(
            "frame.memory.epoch_id is missing; cannot correlate frame with worker probe"
        )
    if frame_epoch != epoch:
        raise ValueError(
            "frame.memory.epoch_id does not match probe.last_epoch_id "
            f"({frame_epoch!r} != {epoch!r}); frame is not from the probed worker generation"
        )
    frame_rid = frame_run_id(frame)
    if not frame_rid:
        raise ValueError(
            "frame.run_id is missing; cannot correlate frame with automation generation"
        )
    if frame_rid != run_id:
        raise ValueError(
            f"frame.run_id does not match probe.run_id ({frame_rid!r} != {run_id!r})"
        )
    frame_pid = frame_worker_pid(frame)
    if frame_pid is None:
        raise ValueError(
            "frame.worker_pid is missing; cannot correlate frame with automation process"
        )
    if frame_pid != pid:
        raise ValueError(
            f"frame.worker_pid does not match probe.worker_pid ({frame_pid} != {pid})"
        )
    frame_evictions = frame_capacity_eviction_count(frame)
    if frame_evictions is None or frame_evictions < 0:
        raise ValueError(
            "frame capacity_eviction_count is missing; "
            "cannot prove age expiry versus capacity eviction"
        )
    if frame_evictions != probe_evictions:
        raise ValueError(
            "frame capacity_eviction_count does not match probe "
            f"({frame_evictions} != {probe_evictions})"
        )
    return ChaseMaxAgeIdentity(
        worker_pid=pid,
        run_id=run_id,
        reset_count=reset_count,
        memory_epoch_id=epoch,
        simulation_epoch=simulation_epoch,
        capacity_eviction_count=probe_evictions,
    )


def capacity_eviction_is_ambiguous(
    *,
    present_keys: set[str],
    present_ids: set[str],
    max_records: int,
    previous_present_ids: set[str] | None = None,
) -> bool:
    """True when tracked-key loss on a full ledger may be capacity replacement."""

    if max_records <= 0:
        return True
    remaining = present_keys.intersection(present_ids)
    if remaining == present_keys:
        return False
    # Some tracked keys missing.
    if len(present_ids) < max_records:
        return False
    # Full ledger with missing tracked keys: replacement non-always-on IDs are ambiguous.
    other_ids = present_ids - present_keys
    non_always_on_other = {rid for rid in other_ids if not is_chase_always_on_key(rid)}
    if non_always_on_other:
        return True
    if previous_present_ids is not None:
        replacements = present_ids - previous_present_ids
        if any(not is_chase_always_on_key(rid) for rid in replacements):
            return True
    # Full ledger of only always-on keys after tracked loss is still causally
    # ambiguous for a capacity-bounded stage (headroom was not available).
    return True


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
    identity_stable: bool,
    frames_advanced: bool,
    capacity_eviction_ambiguous: bool,
    headroom_proven: bool,
) -> dict[str, Any]:
    """Score a completed max-age wait against the live-expiry invariant."""

    remaining = lifecycle_keys.intersection(record_ids_from_memory(final_memory))
    age_ok = (
        max_age_ms is not None
        and max_age_ms > 0
        and age_elapsed_ms is not None
        and age_elapsed_ms >= max_age_ms
    )
    # Headroom must be proven on every sampled frame while tracked keys were
    # present; full-ledger loss remains independently ambiguous.
    capacity_ok = headroom_proven and not capacity_eviction_ambiguous
    passed = (
        bool(lifecycle_keys)
        and not remaining
        and control_ok
        and not reset_used
        and age_ok
        and identity_stable
        and frames_advanced
        and capacity_ok
    )
    if not lifecycle_keys:
        reason = "no lifecycle keys to expire"
    elif max_age_ms is None or max_age_ms <= 0:
        reason = "configured max_age_ms is missing or invalid"
    elif reset_used:
        reason = "reset was used; max-age expiry must not rely on reset"
    elif not control_ok:
        reason = "control was non-zero or incomplete during max-age wait"
    elif not identity_stable:
        reason = "worker or simulation identity changed during max-age wait"
    elif not frames_advanced:
        reason = "simulator frames did not advance during max-age wait"
    elif not headroom_proven:
        reason = (
            "capacity headroom was not proven while tracked keys were present "
            "(ledger reached max_records; key loss may be capacity eviction)"
        )
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
        "identity_stable": identity_stable,
        "frames_advanced": frames_advanced,
        "capacity_eviction_ambiguous": capacity_eviction_ambiguous,
        "headroom_proven": headroom_proven,
        "record_ids": sorted(record_ids_from_memory(final_memory)),
    }


def _fail_wait(
    *,
    reason: str,
    frame: dict[str, Any] | None,
    memory: dict[str, Any] | None,
    wait_frames: list[dict[str, Any]],
    present_keys: set[str],
    max_age_ms: int,
    control_ok: bool,
    reset_used: bool,
    identity_stable: bool,
    frames_advanced: bool,
    capacity_eviction_ambiguous: bool,
    headroom_proven: bool,
    age_elapsed_ms: int | None = None,
) -> ChaseMaxAgeWaitResult:
    final_memory = memory if isinstance(memory, dict) else {"records": []}
    score = score_chase_max_age_expiry(
        lifecycle_keys=present_keys,
        final_memory=final_memory,
        control_ok=control_ok,
        reset_used=reset_used,
        max_age_ms=max_age_ms,
        age_elapsed_ms=age_elapsed_ms,
        identity_stable=identity_stable,
        frames_advanced=frames_advanced,
        capacity_eviction_ambiguous=capacity_eviction_ambiguous,
        headroom_proven=headroom_proven,
    )
    score["reason"] = reason
    score["passed"] = False
    return ChaseMaxAgeWaitResult(
        passed=False,
        reason=reason,
        expiry_frame=frame,
        expiry_memory=memory,
        wait_frames=wait_frames if frame is None else wait_frames + [frame],
        score=score,
    )


def wait_for_chase_memory_key_expiry(
    *,
    load_latest_frame: Callable[[], dict[str, Any] | None],
    probe_fn: Callable[[], dict[str, Any]],
    present_keys: set[str],
    max_age_ms: int,
    timeout_s: float,
    key_anchors_ms: dict[str, int],
    identity: ChaseMaxAgeIdentity,
    max_records: int,
) -> ChaseMaxAgeWaitResult:
    """Poll live Chase frames until lifecycle keys age out without reset/movement."""

    if not present_keys:
        raise ValueError("present_keys must be non-empty")
    if max_age_ms <= 0:
        raise ValueError("max_age_ms must be positive")
    if max_records <= 0:
        raise ValueError("max_records must be positive")

    deadline = time.monotonic() + max(1.0, float(timeout_s))
    wait_frames: list[dict[str, Any]] = []
    first_index: int | None = None
    max_index: int | None = None
    saw_keys_present = False
    headroom_while_present = True
    anchors = dict(key_anchors_ms)
    wait_start_wall_ms = int(time.time() * 1000)
    last_error: str | None = None
    previous_present_ids: set[str] | None = None

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
            return _fail_wait(
                reason=f"movement or incomplete control during max-age wait: {control_reason}",
                frame=frame,
                memory=memory,
                wait_frames=wait_frames,
                present_keys=present_keys,
                max_age_ms=max_age_ms,
                control_ok=False,
                reset_used=False,
                identity_stable=True,
                frames_advanced=False,
                capacity_eviction_ambiguous=False,
                headroom_proven=False,
            )

        sim_epoch = frame_simulation_epoch(frame)
        if not sim_epoch:
            return _fail_wait(
                reason="simulation_epoch missing on wait frame",
                frame=frame,
                memory=memory,
                wait_frames=wait_frames,
                present_keys=present_keys,
                max_age_ms=max_age_ms,
                control_ok=True,
                reset_used=False,
                identity_stable=False,
                frames_advanced=False,
                capacity_eviction_ambiguous=False,
                headroom_proven=False,
            )
        if sim_epoch != identity.simulation_epoch:
            return _fail_wait(
                reason=(
                    f"simulation_epoch changed during max-age wait "
                    f"({identity.simulation_epoch!r} -> {sim_epoch!r})"
                ),
                frame=frame,
                memory=memory,
                wait_frames=wait_frames,
                present_keys=present_keys,
                max_age_ms=max_age_ms,
                control_ok=True,
                reset_used=False,
                identity_stable=False,
                frames_advanced=False,
                capacity_eviction_ambiguous=False,
                headroom_proven=False,
            )

        probe = probe_fn()
        if not isinstance(probe, dict) or probe.get("status") != "live":
            last_error = (
                f"worker not live during max-age wait "
                f"(status={None if not isinstance(probe, dict) else probe.get('status')})"
            )
            time.sleep(0.2)
            continue

        pid = probe.get("worker_pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return _fail_wait(
                reason="worker_pid missing on probe during max-age wait",
                frame=frame,
                memory=memory,
                wait_frames=wait_frames,
                present_keys=present_keys,
                max_age_ms=max_age_ms,
                control_ok=True,
                reset_used=False,
                identity_stable=False,
                frames_advanced=False,
                capacity_eviction_ambiguous=False,
                headroom_proven=False,
            )
        if pid != identity.worker_pid:
            return _fail_wait(
                reason=(
                    f"worker_pid changed during max-age wait "
                    f"({identity.worker_pid} -> {pid})"
                ),
                frame=frame,
                memory=memory,
                wait_frames=wait_frames,
                present_keys=present_keys,
                max_age_ms=max_age_ms,
                control_ok=True,
                reset_used=False,
                identity_stable=False,
                frames_advanced=False,
                capacity_eviction_ambiguous=False,
                headroom_proven=False,
            )

        reset_count = _optional_int(probe.get("reset_count"))
        if reset_count is None:
            return _fail_wait(
                reason="reset_count missing on probe during max-age wait",
                frame=frame,
                memory=memory,
                wait_frames=wait_frames,
                present_keys=present_keys,
                max_age_ms=max_age_ms,
                control_ok=True,
                reset_used=False,
                identity_stable=False,
                frames_advanced=False,
                capacity_eviction_ambiguous=False,
                headroom_proven=False,
            )
        if reset_count != identity.reset_count:
            return _fail_wait(
                reason=(
                    f"reset_count changed during max-age wait "
                    f"({identity.reset_count} -> {reset_count})"
                ),
                frame=frame,
                memory=memory,
                wait_frames=wait_frames,
                present_keys=present_keys,
                max_age_ms=max_age_ms,
                control_ok=True,
                reset_used=True,
                identity_stable=False,
                frames_advanced=False,
                capacity_eviction_ambiguous=False,
                headroom_proven=False,
            )

        epoch_id = str(probe.get("last_epoch_id") or "").strip()
        if not epoch_id:
            return _fail_wait(
                reason="memory epoch missing on probe during max-age wait",
                frame=frame,
                memory=memory,
                wait_frames=wait_frames,
                present_keys=present_keys,
                max_age_ms=max_age_ms,
                control_ok=True,
                reset_used=False,
                identity_stable=False,
                frames_advanced=False,
                capacity_eviction_ambiguous=False,
                headroom_proven=False,
            )
        if epoch_id != identity.memory_epoch_id:
            return _fail_wait(
                reason=(
                    f"memory epoch changed during max-age wait "
                    f"({identity.memory_epoch_id!r} -> {epoch_id!r})"
                ),
                frame=frame,
                memory=memory,
                wait_frames=wait_frames,
                present_keys=present_keys,
                max_age_ms=max_age_ms,
                control_ok=True,
                reset_used=False,
                identity_stable=False,
                frames_advanced=False,
                capacity_eviction_ambiguous=False,
                headroom_proven=False,
            )

        # Correlate this frame with the live probe generation so a restarted
        # worker reusing epoch-1 cannot launder keys-present evidence.
        frame_epoch = frame_memory_epoch_id(frame)
        if not frame_epoch:
            return _fail_wait(
                reason="frame.memory.epoch_id missing during max-age wait",
                frame=frame,
                memory=memory,
                wait_frames=wait_frames,
                present_keys=present_keys,
                max_age_ms=max_age_ms,
                control_ok=True,
                reset_used=False,
                identity_stable=False,
                frames_advanced=False,
                capacity_eviction_ambiguous=False,
                headroom_proven=False,
            )
        if frame_epoch != epoch_id or frame_epoch != identity.memory_epoch_id:
            return _fail_wait(
                reason=(
                    "frame.memory.epoch_id does not match live probe memory epoch "
                    f"(frame={frame_epoch!r}, probe={epoch_id!r}, "
                    f"baseline={identity.memory_epoch_id!r})"
                ),
                frame=frame,
                memory=memory,
                wait_frames=wait_frames,
                present_keys=present_keys,
                max_age_ms=max_age_ms,
                control_ok=True,
                reset_used=False,
                identity_stable=False,
                frames_advanced=False,
                capacity_eviction_ambiguous=False,
                headroom_proven=False,
            )

        probe_run_id = str(probe.get("run_id") or "").strip()
        if not probe_run_id:
            return _fail_wait(
                reason="run_id missing on probe during max-age wait",
                frame=frame,
                memory=memory,
                wait_frames=wait_frames,
                present_keys=present_keys,
                max_age_ms=max_age_ms,
                control_ok=True,
                reset_used=False,
                identity_stable=False,
                frames_advanced=False,
                capacity_eviction_ambiguous=False,
                headroom_proven=False,
            )
        if probe_run_id != identity.run_id:
            return _fail_wait(
                reason=(
                    f"run_id changed during max-age wait "
                    f"({identity.run_id!r} -> {probe_run_id!r})"
                ),
                frame=frame,
                memory=memory,
                wait_frames=wait_frames,
                present_keys=present_keys,
                max_age_ms=max_age_ms,
                control_ok=True,
                reset_used=False,
                identity_stable=False,
                frames_advanced=False,
                capacity_eviction_ambiguous=False,
                headroom_proven=False,
            )
        frame_rid = frame_run_id(frame)
        if not frame_rid or frame_rid != identity.run_id:
            return _fail_wait(
                reason=(
                    "frame.run_id does not match automation generation "
                    f"(frame={frame_rid!r}, expected={identity.run_id!r})"
                ),
                frame=frame,
                memory=memory,
                wait_frames=wait_frames,
                present_keys=present_keys,
                max_age_ms=max_age_ms,
                control_ok=True,
                reset_used=False,
                identity_stable=False,
                frames_advanced=False,
                capacity_eviction_ambiguous=False,
                headroom_proven=False,
            )
        frame_pid = frame_worker_pid(frame)
        if frame_pid is None or frame_pid != identity.worker_pid:
            return _fail_wait(
                reason=(
                    "frame.worker_pid does not match automation process "
                    f"(frame={frame_pid!r}, expected={identity.worker_pid})"
                ),
                frame=frame,
                memory=memory,
                wait_frames=wait_frames,
                present_keys=present_keys,
                max_age_ms=max_age_ms,
                control_ok=True,
                reset_used=False,
                identity_stable=False,
                frames_advanced=False,
                capacity_eviction_ambiguous=False,
                headroom_proven=False,
            )

        # Authoritative capacity-eviction counter survives unsampled intermediate
        # frames: any increase during the wait voids a pure max-age claim.
        probe_evictions = _optional_int(probe.get("capacity_eviction_count"))
        frame_evictions = frame_capacity_eviction_count(frame)
        if probe_evictions is None or frame_evictions is None:
            return _fail_wait(
                reason=(
                    "capacity_eviction_count missing on probe or frame during max-age wait"
                ),
                frame=frame,
                memory=memory,
                wait_frames=wait_frames,
                present_keys=present_keys,
                max_age_ms=max_age_ms,
                control_ok=True,
                reset_used=False,
                identity_stable=True,
                frames_advanced=False,
                capacity_eviction_ambiguous=True,
                headroom_proven=False,
            )
        if frame_evictions != probe_evictions:
            return _fail_wait(
                reason=(
                    "frame capacity_eviction_count does not match probe "
                    f"(frame={frame_evictions}, probe={probe_evictions})"
                ),
                frame=frame,
                memory=memory,
                wait_frames=wait_frames,
                present_keys=present_keys,
                max_age_ms=max_age_ms,
                control_ok=True,
                reset_used=False,
                identity_stable=True,
                frames_advanced=False,
                capacity_eviction_ambiguous=True,
                headroom_proven=False,
            )
        if probe_evictions > identity.capacity_eviction_count:
            return _fail_wait(
                reason=(
                    "capacity eviction occurred during max-age wait "
                    f"(baseline={identity.capacity_eviction_count}, "
                    f"now={probe_evictions}); not pure max-age expiry"
                ),
                frame=frame,
                memory=memory,
                wait_frames=wait_frames,
                present_keys=present_keys,
                max_age_ms=max_age_ms,
                control_ok=True,
                reset_used=False,
                identity_stable=True,
                frames_advanced=False,
                capacity_eviction_ambiguous=True,
                headroom_proven=False,
            )
        if probe_evictions < identity.capacity_eviction_count:
            return _fail_wait(
                reason=(
                    "capacity_eviction_count decreased during max-age wait "
                    f"({identity.capacity_eviction_count} -> {probe_evictions}); "
                    "worker generation discontinuity"
                ),
                frame=frame,
                memory=memory,
                wait_frames=wait_frames,
                present_keys=present_keys,
                max_age_ms=max_age_ms,
                control_ok=True,
                reset_used=False,
                identity_stable=False,
                frames_advanced=False,
                capacity_eviction_ambiguous=False,
                headroom_proven=False,
            )

        index = _chase_frame_index(frame)
        if index is not None:
            if first_index is None:
                first_index = index
            max_index = index if max_index is None else max(max_index, index)

        present_ids = record_ids_from_memory(memory)
        remaining = present_keys.intersection(present_ids)
        record_count = len(memory.get("records") or [])

        if remaining:
            saw_keys_present = True
            if record_count >= max_records:
                # Observed full ledger while the tracked key still exists: capacity
                # eviction cannot be excluded for later disappearance.
                return _fail_wait(
                    reason=(
                        "ledger at max_records while tracked keys still present; "
                        "capacity headroom not proven for max-age expiry"
                    ),
                    frame=frame,
                    memory=memory,
                    wait_frames=wait_frames,
                    present_keys=present_keys,
                    max_age_ms=max_age_ms,
                    control_ok=True,
                    reset_used=False,
                    identity_stable=True,
                    frames_advanced=(
                        first_index is not None
                        and max_index is not None
                        and max_index > first_index
                    ),
                    capacity_eviction_ambiguous=False,
                    headroom_proven=False,
                )
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

        if capacity_eviction_is_ambiguous(
            present_keys=present_keys,
            present_ids=present_ids,
            max_records=max_records,
            previous_present_ids=previous_present_ids,
        ):
            now_ms = int(time.time() * 1000)
            min_age = None
            for key in present_keys - present_ids:
                anchor = anchors.get(key, wait_start_wall_ms)
                age = now_ms - anchor
                min_age = age if min_age is None else min(min_age, age)
            return _fail_wait(
                reason=(
                    "lifecycle key loss while ledger at max_records "
                    "(capacity eviction ambiguous; not pure max-age expiry)"
                ),
                frame=frame,
                memory=memory,
                wait_frames=wait_frames,
                present_keys=present_keys,
                max_age_ms=max_age_ms,
                control_ok=True,
                reset_used=False,
                identity_stable=True,
                frames_advanced=(
                    first_index is not None
                    and max_index is not None
                    and max_index > first_index
                ),
                capacity_eviction_ambiguous=True,
                headroom_proven=False,
                age_elapsed_ms=min_age,
            )

        previous_present_ids = set(present_ids)
        wait_frames.append(frame)

        if remaining:
            time.sleep(0.25)
            continue

        # Keys gone.
        if not saw_keys_present:
            last_error = (
                "lifecycle keys never observed present during wait "
                "(stale or keyless frame)"
            )
            time.sleep(0.2)
            continue

        frames_advanced = (
            first_index is not None
            and max_index is not None
            and max_index > first_index
        )
        # Final ambiguity check after keys leave (covers full-ledger replacement).
        final_ambiguous = capacity_eviction_is_ambiguous(
            present_keys=present_keys,
            present_ids=present_ids,
            max_records=max_records,
            previous_present_ids=previous_present_ids,
        )
        # Keys were only accepted while headroom held; final frame must also
        # not be a full ledger after loss (replacement under capacity bounds).
        headroom_proven = headroom_while_present and record_count < max_records
        if not headroom_proven:
            return _fail_wait(
                reason=(
                    "capacity headroom was not proven for max-age expiry "
                    f"(max_records={max_records}, final_record_count={record_count})"
                ),
                frame=frame,
                memory=memory,
                wait_frames=wait_frames[:-1] if wait_frames else [],
                present_keys=present_keys,
                max_age_ms=max_age_ms,
                control_ok=True,
                reset_used=False,
                identity_stable=True,
                frames_advanced=frames_advanced,
                capacity_eviction_ambiguous=final_ambiguous,
                headroom_proven=False,
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
            identity_stable=True,
            frames_advanced=frames_advanced,
            capacity_eviction_ambiguous=final_ambiguous,
            headroom_proven=headroom_proven,
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
        f"live Chase memory did not complete max-age expiry within {timeout_s}s "
        f"(lifecycle keys did not drop under continuous worker identity).{detail}"
    )
