from __future__ import annotations
from cli.automa_cli.chase_max_age import ChaseMaxAgeIdentity


def _chase_frame(
    index: int,
    records: list[dict],
    *,
    timestamp_ms: int | None = None,
    control: dict | None = None,
    simulation_epoch: str = "chase-run:test",
    memory_epoch_id: str = "memory-epoch-0",
    run_id: str = "automation-run-1",
    worker_pid: int = 4242,
    capacity_eviction_count: int = 0,
    omit_observe_only: bool = False,
) -> dict:
    frame = {
        "frame_id": f"chase_frame_{index:06d}",
        "frame_index": index,
        "simulator_frame_index": index,
        "timestamp_ms": timestamp_ms if timestamp_ms is not None else 1_000 + index,
        "simulation_epoch": simulation_epoch,
        "run_id": run_id,
        "worker_pid": worker_pid,
        "control_source": "simulator",
        "control": control
        if control is not None
        else {
            "applied": False,
            "reason": "idle",
            "steering": 0.0,
            "throttle": 0.0,
        },
        "shadow_reference": {
            "schema": "chase_shadow_reference_v1",
            "evaluator_only": True,
            "simulator_frame_index": index,
            "simulation_epoch": simulation_epoch,
            "game_id": "chase",
            "scenario": "chaser-depth-obstacles",
            "chaser_control_source": "programmatic",
        },
        "observation": {
            "observation_id": f"obs-{index}",
            "things": [{"thing_id": "front_camera_frame"}],
            "signals": [],
            "sensor_snapshot": {
                "metadata": {
                    "simulator_frame_index": index,
                    "simulation_epoch": simulation_epoch,
                }
            },
        },
        "memory": {
            "health": "healthy" if records else "empty",
            "record_count": len(records),
            "records": records,
            "epoch_id": memory_epoch_id,
            "metadata": {
                "capacity_eviction_count": capacity_eviction_count,
            },
        },
    }
    if not omit_observe_only:
        frame["control_application"] = "not_applied"
        frame["action_policy"] = "observe_only"
    return frame


def _live_probe(
    *,
    reset_count: int = 0,
    epoch: str = "memory-epoch-0",
    pid: int = 4242,
    run_id: str = "automation-run-1",
    max_age_ms: int = 1000,
    max_records: int = 32,
    include_bounds: bool = True,
    count: int = 1,
    capacity_eviction_count: int = 0,
) -> dict:
    payload = {
        "status": "live",
        "last_health": "healthy",
        "last_record_count": count,
        "last_epoch_id": epoch,
        "reset_count": reset_count,
        "worker_pid": pid,
        "run_id": run_id,
        "capacity_eviction_count": capacity_eviction_count,
        "implementation_id": "bounded_evidence",
        "activation": "runtime/memory/active.json",
    }
    if include_bounds:
        payload["bounds"] = {"max_age_ms": max_age_ms, "max_records": max_records}
    return payload


def _identity(**overrides: object) -> ChaseMaxAgeIdentity:
    base = dict(
        worker_pid=4242,
        run_id="automation-run-1",
        reset_count=1,
        memory_epoch_id="memory-epoch-0",
        simulation_epoch="chase-run:test",
        capacity_eviction_count=0,
    )
    base.update(overrides)
    return ChaseMaxAgeIdentity(**base)  # type: ignore[arg-type]
