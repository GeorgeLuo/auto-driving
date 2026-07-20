"""Simulator frame identity and evaluator-only shadow reference helpers.

Chase Play exposes ``frameIndex`` on play_debug. The vehicle adapter must preserve
that identity on sensor captures so candidate cycle results can be aligned with
built-in reference state. Full debug/map payloads stay evaluator-only and must
not become memory or controller inputs.
"""

from __future__ import annotations

from typing import Any


def coerce_simulator_frame_index(value: Any) -> int | None:
    """Return a non-negative int frame index, or None when absent/invalid."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = int(value.strip(), 10)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


def format_chase_frame_id(frame_index: int) -> str:
    """Stable camera-derived frame id anchored to the simulator frame index."""

    return f"chase_frame_{int(frame_index):06d}"


def simulator_frame_index_from_debug(debug: dict[str, Any] | None) -> int | None:
    if not isinstance(debug, dict):
        return None
    return coerce_simulator_frame_index(debug.get("frameIndex"))


def simulator_frame_index_from_snapshot(snapshot: Any) -> int | None:
    """Extract simulator frame index from a SensorSnapshot or its dict form."""

    if snapshot is None:
        return None
    metadata: dict[str, Any] = {}
    readings: dict[str, Any] = {}
    if isinstance(snapshot, dict):
        meta = snapshot.get("metadata")
        if isinstance(meta, dict):
            metadata = meta
        raw_readings = snapshot.get("readings")
        if isinstance(raw_readings, dict):
            readings = raw_readings
    else:
        meta = getattr(snapshot, "metadata", None)
        if isinstance(meta, dict):
            metadata = meta
        raw_readings = getattr(snapshot, "readings", None)
        if isinstance(raw_readings, dict):
            readings = raw_readings

    for key in ("simulator_frame_index", "frame_index", "frameIndex"):
        index = coerce_simulator_frame_index(metadata.get(key))
        if index is not None:
            return index

    for reading in readings.values():
        reading_meta: dict[str, Any] = {}
        if isinstance(reading, dict):
            maybe = reading.get("metadata")
            if isinstance(maybe, dict):
                reading_meta = maybe
        else:
            maybe = getattr(reading, "metadata", None)
            if isinstance(maybe, dict):
                reading_meta = maybe
        for key in ("simulator_frame_index", "frame_index", "frameIndex"):
            index = coerce_simulator_frame_index(reading_meta.get(key))
            if index is not None:
                return index
    return None


def sanitize_chase_shadow_reference(
    debug: dict[str, Any] | None,
    *,
    require_frame_index: int | None = None,
) -> dict[str, Any] | None:
    """Build an evaluator-only shadow reference from play_debug.

    Always uses the debug payload's own ``frameIndex``. When
    ``require_frame_index`` is provided it must equal that debug index —
    callers must not relabel an older debug blob onto a newer camera frame.

    Intentionally omits map geometry and privileged scene structure. The result
    is for post-cycle alignment scoring only — never fed into observation or
    memory inputs.
    """

    if not isinstance(debug, dict):
        return None
    frame_index = simulator_frame_index_from_debug(debug)
    if frame_index is None:
        return None
    if require_frame_index is not None and int(require_frame_index) != int(frame_index):
        # Fail closed: never rewrite debug identity to match a different frame.
        return None

    chaser_action = debug.get("actions") if isinstance(debug.get("actions"), dict) else {}
    actors = debug.get("actors") if isinstance(debug.get("actors"), dict) else {}
    chaser = actors.get("chaser") if isinstance(actors.get("chaser"), dict) else {}
    chaser_action_block = chaser.get("action") if isinstance(chaser.get("action"), dict) else {}

    reference: dict[str, Any] = {
        "schema": "chase_shadow_reference_v0",
        "evaluator_only": True,
        "simulator_frame_index": frame_index,
        "frame_id": format_chase_frame_id(frame_index),
        "game_id": debug.get("gameId"),
        "scenario": debug.get("scenario") or debug.get("scenarioId"),
        "chaser_control_source": (
            _nested(debug, ("actions", "chaserInput", "source"))
            or _nested(debug, ("actions", "chaserAction", "source"))
            or chaser_action_block.get("source")
        ),
        "chaser_input": _shallow_copy_dict(chaser_action.get("chaserInput")),
        "chaser_action": _shallow_copy_dict(chaser_action.get("chaserAction")),
        "actor_action": _shallow_copy_dict(chaser_action_block),
    }
    # Drop empty optional blocks for stable, compact artifacts.
    for key in ("chaser_input", "chaser_action", "actor_action", "scenario", "chaser_control_source"):
        if reference.get(key) in (None, {}):
            reference.pop(key, None)
    return reference


def frame_indices_strictly_increasing(indices: list[int]) -> bool:
    """True when each index is strictly greater than the previous."""

    if len(indices) < 2:
        return False
    return all(indices[i] < indices[i + 1] for i in range(len(indices) - 1))


def consistent_game_identity(frames: list[dict[str, Any]]) -> bool:
    """All frames that declare game_id/scenario must share one value each."""

    game_ids: set[str] = set()
    scenarios: set[str] = set()
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        shadow = frame.get("shadow_reference") if isinstance(frame.get("shadow_reference"), dict) else {}
        game = shadow.get("game_id") or frame.get("game_id")
        scenario = shadow.get("scenario") or frame.get("scenario")
        if game is not None and str(game).strip():
            game_ids.add(str(game))
        if scenario is not None and str(scenario).strip():
            scenarios.add(str(scenario))
    if len(game_ids) > 1:
        return False
    if len(scenarios) > 1:
        return False
    return True


def align_candidate_with_shadow(
    *,
    candidate_frame_index: int | None,
    shadow_reference: dict[str, Any] | None,
) -> dict[str, Any]:
    """Score whether candidate and shadow reference share simulator frame identity."""

    shadow_index = None
    if isinstance(shadow_reference, dict):
        shadow_index = coerce_simulator_frame_index(
            shadow_reference.get("simulator_frame_index")
            if shadow_reference.get("simulator_frame_index") is not None
            else shadow_reference.get("frame_index")
        )
    matched = (
        candidate_frame_index is not None
        and shadow_index is not None
        and int(candidate_frame_index) == int(shadow_index)
    )
    return {
        "aligned": matched,
        "candidate_frame_index": candidate_frame_index,
        "shadow_frame_index": shadow_index,
        "reason": (
            "candidate and shadow share simulator frame identity"
            if matched
            else "candidate/shadow simulator frame identity mismatch or missing"
        ),
    }


def score_shadow_alignment_batch(
    frames: list[dict[str, Any]],
    *,
    min_frames: int = 2,
) -> dict[str, Any]:
    """Score a sequence of candidate frame records for identity + shadow alignment."""

    alignments: list[dict[str, Any]] = []
    indices: list[int] = []
    missing_identity = 0
    missing_shadow = 0
    mismatched = 0
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        index = coerce_simulator_frame_index(
            frame.get("simulator_frame_index")
            if frame.get("simulator_frame_index") is not None
            else frame.get("frame_index")
        )
        shadow = frame.get("shadow_reference")
        if not isinstance(shadow, dict):
            shadow = None
            missing_shadow += 1
        if index is None:
            missing_identity += 1
        else:
            indices.append(int(index))
        alignment = align_candidate_with_shadow(
            candidate_frame_index=index,
            shadow_reference=shadow,
        )
        alignments.append(
            {
                "frame_id": frame.get("frame_id"),
                **alignment,
            }
        )
        if index is not None and shadow is not None and not alignment["aligned"]:
            mismatched += 1

    advancing = frame_indices_strictly_increasing(indices)
    identity_ok = consistent_game_identity(frames)
    aligned_count = sum(1 for item in alignments if item.get("aligned"))
    passed = (
        len(alignments) >= min_frames
        and missing_identity == 0
        and missing_shadow == 0
        and mismatched == 0
        and advancing
        and identity_ok
        and aligned_count == len(alignments)
    )
    return {
        "passed": passed,
        "frame_count": len(alignments),
        "aligned_count": aligned_count,
        "missing_identity": missing_identity,
        "missing_shadow": missing_shadow,
        "mismatched": mismatched,
        "advancing_simulator_frames": advancing,
        "consistent_game_identity": identity_ok,
        "simulator_frame_indices": indices,
        "alignments": alignments,
        "reason": (
            "live frames preserve simulator identity and align with evaluator-only shadow references"
            if passed
            else (
                "expected ≥"
                f"{min_frames} frames with strictly increasing simulator_frame_index, "
                "matching shadow_reference, and consistent game/scenario identity"
            ),
        ),
    }


def _nested(record: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = record
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _shallow_copy_dict(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    # Copy only JSON-scalar leaves one level deep; drop nested map-like blobs.
    out: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, (str, int, float, bool)) or item is None:
            out[str(key)] = item
        elif isinstance(item, (list, tuple)) and all(
            isinstance(entry, (str, int, float, bool)) or entry is None for entry in item
        ):
            out[str(key)] = list(item)
    return out
