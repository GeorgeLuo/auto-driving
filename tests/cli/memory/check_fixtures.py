from __future__ import annotations


def _always_on_things_signals(*, with_boundary: bool) -> tuple[list[dict], list[dict]]:
    """Camera/floor evidence that remains present even after object removal."""
    things = [
        {
            "thing_id": "front_camera_frame",
            "kind": "camera_frame",
            "label": "camera",
            "confidence": 1.0,
            "location": {"frame": "image", "zone": "full"},
            "source_plugin_id": "lightweight_observer",
        },
        {
            "thing_id": "traversable_floor",
            "kind": "floor",
            "label": "floor",
            "confidence": 0.95,
            "location": {"frame": "image", "zone": "center"},
            "source_plugin_id": "lightweight_observer",
        },
    ]
    if with_boundary:
        things.append(
            {
                "thing_id": "floor_boundary_000",
                "kind": "floor_boundary",
                "label": "boundary",
                "confidence": 0.9,
                "location": {
                    "frame": "image",
                    "zone": "center",
                    "bbox_xyxy_norm": [0.3, 0.4, 0.7, 0.95],
                },
                "source_plugin_id": "floor-plane-v0",
            }
        )
    signals = [
        {"signal_id": "floor_visible", "value": True, "confidence": 0.95},
        {"signal_id": "front_camera_available", "value": True, "confidence": 1.0},
    ]
    return things, signals


def _memory_record(
    record_id: str, *, frame_id: str, kind: str = "floor_boundary"
) -> dict:
    return {
        "record_id": record_id,
        "kind": kind,
        "label": kind,
        "confidence": 0.9,
        "provenance": {
            "frame_id": frame_id,
            "observation_id": f"obs_{frame_id}",
            "evidence_id": record_id.split(":", 1)[-1],
        },
        "location": {"frame": "image", "zone": "center"},
    }


def _live_publication(
    *,
    frame_id: str,
    frame_index: int,
    with_boundary: bool,
    steering: float = 0.0,
    throttle: float = 0.0,
    memory_records: list[dict] | None = None,
    memory_health: str | None = None,
    epoch_id: str = "epoch-1",
    max_age_ms: int = 1_000,
) -> dict:
    things, signals = _always_on_things_signals(with_boundary=with_boundary)
    if memory_records is None:
        # Always-on keys stay in memory; boundary is the lifecycle target.
        memory_records = [
            _memory_record(
                "thing:front_camera_frame", frame_id=frame_id, kind="camera_frame"
            ),
            _memory_record("thing:traversable_floor", frame_id=frame_id, kind="floor"),
            _memory_record("signal:floor_visible", frame_id=frame_id, kind="signal"),
            _memory_record(
                "signal:front_camera_available", frame_id=frame_id, kind="signal"
            ),
        ]
        if with_boundary:
            memory_records.append(
                _memory_record("thing:floor_boundary_000", frame_id=frame_id)
            )
        else:
            # Dropout survival: boundary gone from observation but still retained in memory.
            memory_records.append(
                _memory_record("thing:floor_boundary_000", frame_id="present_frame")
            )
    if memory_health is None:
        memory_health = "healthy" if memory_records else "empty"
    return {
        "health": "healthy",
        "drive_mode": "user",
        "control": {"steering": steering, "throttle": throttle},
        "frame": {
            "frame_id": frame_id,
            "frame_index": frame_index,
            "captured_at_ms": 1_000 + frame_index * 100,
            "completed_at_ms": 1_010 + frame_index * 100,
            "has_image": True,
        },
        "perception": {
            "plugin_id": "lightweight_observer",
            "status": "ok",
            "things": things,
            "signals": signals,
            "lines": ["live test"],
        },
        "observation": {
            "observation_id": f"obs_{frame_id}",
            "created_at_ms": 1_000 + frame_index * 100,
            "sensor_snapshot": {},
            "perception_plugin_id": "lightweight_observer",
            "things": things,
            "signals": signals,
        },
        "memory": {
            "health": memory_health,
            "record_count": len(memory_records),
            "records": memory_records,
            "epoch_id": epoch_id,
            "implementation_id": "bounded_evidence",
            "bounds": {
                "max_records": 32,
                "max_age_ms": max_age_ms,
                "eviction_policy": "oldest_first",
            },
        },
    }
