"""Chase simulator frame identity and evaluator-only reference helpers.

The atomic evaluation capture owns the camera image, simulation-run identity,
and a bounded control reference for one immutable simulator state. The control
reference is for post-cycle scoring only; it must never become perception,
observation, memory, or controller input.
"""

from __future__ import annotations

import math
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
    """Stable frame label anchored to the simulator frame index."""

    return f"chase_frame_{int(frame_index):06d}"


def simulator_frame_index_from_snapshot(snapshot: Any) -> int | None:
    """Extract simulator frame index from a SensorSnapshot or its dict form."""

    for metadata in _snapshot_metadata_records(snapshot):
        for key in ("simulator_frame_index", "frame_index", "frameIndex"):
            index = coerce_simulator_frame_index(metadata.get(key))
            if index is not None:
                return index
    return None


def simulator_epoch_from_snapshot(snapshot: Any) -> str | None:
    """Extract the simulation-run epoch from a SensorSnapshot or dict form."""

    for metadata in _snapshot_metadata_records(snapshot):
        for key in ("simulation_epoch", "simulationEpoch"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def build_chase_shadow_reference(
    capture: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Validate an atomic capture and copy only its bounded control reference."""

    if not isinstance(capture, dict):
        return None
    version = capture.get("contractVersion")
    if isinstance(version, bool) or version != 1:
        return None
    capture_id = _nonempty_string(capture.get("captureId"))
    actor_id = _nonempty_string(capture.get("actorId"))
    if capture_id is None or actor_id != "chaser":
        return None

    playback = capture.get("playback")
    if not isinstance(playback, dict) or playback.get("advanced") is not False:
        return None

    identity = capture.get("frameIdentity")
    if not isinstance(identity, dict):
        return None
    game_id = _nonempty_string(identity.get("gameId"))
    simulation_epoch = _nonempty_string(identity.get("simulationEpoch"))
    frame_index = coerce_simulator_frame_index(identity.get("frameIndex"))
    if game_id != "chase" or simulation_epoch is None or frame_index is None:
        return None

    evaluator = capture.get("evaluator")
    if (
        not isinstance(evaluator, dict)
        or evaluator.get("classification") != "non-sensor"
    ):
        return None
    reference = evaluator.get("reference")
    if not isinstance(reference, dict) or reference.get("kind") != "actor-control-reference":
        return None

    scenario = _nonempty_string(reference.get("scenarioId"))
    control_source = _nonempty_string(reference.get("controlSource"))
    phase = _nonempty_string(reference.get("phase"))
    action_frame_index = coerce_simulator_frame_index(reference.get("actionFrameIndex"))
    control_input = _bounded_control(reference.get("input"), include_proposal=False)
    control_action = _bounded_control(reference.get("action"), include_proposal=True)
    if (
        scenario is None
        or control_source is None
        or phase is None
        or action_frame_index is None
        or action_frame_index > frame_index
        or control_input is None
        or control_action is None
    ):
        return None

    return {
        "schema": "chase_shadow_reference_v1",
        "evaluator_only": True,
        "capture_id": capture_id,
        "actor_id": actor_id,
        "simulator_frame_index": frame_index,
        "simulation_epoch": simulation_epoch,
        "frame_id": format_chase_frame_id(frame_index),
        "game_id": game_id,
        "scenario": scenario,
        "chaser_control_source": control_source,
        "phase": phase,
        "action_frame_index": action_frame_index,
        "chaser_input": control_input,
        "chaser_action": control_action,
    }


def frame_indices_strictly_increasing(indices: list[int]) -> bool:
    """True when each index is strictly greater than the previous."""

    if len(indices) < 2:
        return False
    return all(indices[index] < indices[index + 1] for index in range(len(indices) - 1))


def align_candidate_with_shadow(
    *,
    candidate_frame_index: int | None,
    candidate_simulation_epoch: str | None,
    shadow_reference: dict[str, Any] | None,
) -> dict[str, Any]:
    """Score candidate/reference alignment using full simulation-run identity."""

    shadow_index = None
    shadow_epoch = None
    if isinstance(shadow_reference, dict):
        shadow_index = coerce_simulator_frame_index(
            shadow_reference.get("simulator_frame_index")
            if shadow_reference.get("simulator_frame_index") is not None
            else shadow_reference.get("frame_index")
        )
        shadow_epoch = _nonempty_string(shadow_reference.get("simulation_epoch"))
    candidate_epoch = _nonempty_string(candidate_simulation_epoch)
    matched = (
        candidate_frame_index is not None
        and shadow_index is not None
        and int(candidate_frame_index) == int(shadow_index)
        and candidate_epoch is not None
        and candidate_epoch == shadow_epoch
    )
    return {
        "aligned": matched,
        "candidate_frame_index": candidate_frame_index,
        "shadow_frame_index": shadow_index,
        "candidate_simulation_epoch": candidate_epoch,
        "shadow_simulation_epoch": shadow_epoch,
        "reason": (
            "candidate and shadow share simulation epoch and frame index"
            if matched
            else "candidate/shadow simulation-run identity mismatch or missing"
        ),
    }


def score_shadow_alignment_batch(
    frames: list[dict[str, Any]],
    *,
    min_frames: int = 2,
) -> dict[str, Any]:
    """Score candidate records for advancing, same-run atomic alignment."""

    alignments: list[dict[str, Any]] = []
    indices: list[int] = []
    game_ids: set[str] = set()
    scenarios: set[str] = set()
    epochs: set[str] = set()
    missing_identity = 0
    missing_shadow = 0
    missing_run_identity = 0
    mismatched = 0
    for frame in frames:
        if not isinstance(frame, dict):
            missing_identity += 1
            continue
        index = coerce_simulator_frame_index(
            frame.get("simulator_frame_index")
            if frame.get("simulator_frame_index") is not None
            else frame.get("frame_index")
        )
        candidate_epoch = _nonempty_string(frame.get("simulation_epoch"))
        shadow = frame.get("shadow_reference")
        if not isinstance(shadow, dict):
            shadow = None
            missing_shadow += 1
        if index is None:
            missing_identity += 1
        else:
            indices.append(int(index))
        if candidate_epoch is None:
            missing_run_identity += 1

        if shadow is not None:
            game = _nonempty_string(shadow.get("game_id"))
            scenario = _nonempty_string(shadow.get("scenario"))
            epoch = _nonempty_string(shadow.get("simulation_epoch"))
            if game is None or scenario is None or epoch is None:
                missing_run_identity += 1
            else:
                game_ids.add(game)
                scenarios.add(scenario)
                epochs.add(epoch)

        alignment = align_candidate_with_shadow(
            candidate_frame_index=index,
            candidate_simulation_epoch=candidate_epoch,
            shadow_reference=shadow,
        )
        alignments.append({"frame_id": frame.get("frame_id"), **alignment})
        if index is not None and shadow is not None and not alignment["aligned"]:
            mismatched += 1

    advancing = frame_indices_strictly_increasing(indices)
    consistent_run_identity = (
        missing_run_identity == 0
        and game_ids == {"chase"}
        and len(scenarios) == 1
        and len(epochs) == 1
    )
    aligned_count = sum(1 for item in alignments if item.get("aligned"))
    passed = (
        len(alignments) >= min_frames
        and missing_identity == 0
        and missing_shadow == 0
        and mismatched == 0
        and advancing
        and consistent_run_identity
        and aligned_count == len(alignments)
    )
    return {
        "passed": passed,
        "frame_count": len(alignments),
        "aligned_count": aligned_count,
        "missing_identity": missing_identity,
        "missing_shadow": missing_shadow,
        "missing_run_identity": missing_run_identity,
        "mismatched": mismatched,
        "advancing_simulator_frames": advancing,
        "consistent_run_identity": consistent_run_identity,
        "game_ids": sorted(game_ids),
        "scenarios": sorted(scenarios),
        "simulation_epochs": sorted(epochs),
        "simulator_frame_indices": indices,
        "alignments": alignments,
        "reason": (
            "live frames advance within one simulation run and align with atomic references"
            if passed
            else (
                "expected ≥"
                f"{min_frames} advancing frames with matching epoch/index references "
                "and one non-empty game/scenario/simulation epoch"
            )
        ),
    }


def _snapshot_metadata_records(snapshot: Any) -> list[dict[str, Any]]:
    if snapshot is None:
        return []
    metadata: dict[str, Any] = {}
    readings: dict[str, Any] = {}
    if isinstance(snapshot, dict):
        maybe_metadata = snapshot.get("metadata")
        maybe_readings = snapshot.get("readings")
    else:
        maybe_metadata = getattr(snapshot, "metadata", None)
        maybe_readings = getattr(snapshot, "readings", None)
    if isinstance(maybe_metadata, dict):
        metadata = maybe_metadata
    if isinstance(maybe_readings, dict):
        readings = maybe_readings

    records = [metadata]
    for reading in readings.values():
        maybe = reading.get("metadata") if isinstance(reading, dict) else getattr(reading, "metadata", None)
        if isinstance(maybe, dict):
            records.append(maybe)
    return records


def _nonempty_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _bounded_control(value: Any, *, include_proposal: bool) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    source = _nonempty_string(value.get("source"))
    forward = value.get("forward")
    reverse = value.get("reverse")
    steering = value.get("steering")
    if (
        source is None
        or not isinstance(forward, bool)
        or not isinstance(reverse, bool)
        or isinstance(steering, bool)
        or not isinstance(steering, (int, float))
        or not math.isfinite(float(steering))
        or float(steering) < -1.0
        or float(steering) > 1.0
    ):
        return None
    result: dict[str, Any] = {
        "source": source,
        "forward": forward,
        "reverse": reverse,
        "steering": float(steering),
    }
    if include_proposal:
        proposal = value.get("selectedActionProposalId")
        if proposal is not None:
            proposal = _nonempty_string(proposal)
            if proposal is None:
                return None
        result["selectedActionProposalId"] = proposal
    return result
