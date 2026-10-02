from __future__ import annotations

import math
import statistics
from typing import Any


IGNORED_EVIDENCE_KINDS = {"sensor_frame", "prepared_sensor_frame"}


def evaluate_perception_frames(frames: list[dict[str, Any]]) -> dict[str, Any]:
    """Score representation health without claiming semantic correctness."""

    evidence_by_frame = [_spatial_evidence(frame) for frame in frames]
    all_evidence = [thing for evidence in evidence_by_frame for thing in evidence]
    valid_geometry = sum(1 for thing in all_evidence if _valid_thing_geometry(thing))
    frame_counts = [len(evidence) for evidence in evidence_by_frame]
    available_frames = sum(
        1 for frame in frames if str(frame.get("status")) not in {"error", "partial", "unavailable"}
    )
    nonempty_frames = sum(1 for evidence in evidence_by_frame if evidence)

    pair_metrics = [
        _pair_continuity(previous, current)
        for previous, current in zip(evidence_by_frame, evidence_by_frame[1:])
    ]
    match_fraction = _mean([item["match_fraction"] for item in pair_metrics])
    mean_iou = _mean([item["mean_iou"] for item in pair_metrics if item["matches"]])
    mean_count = _mean(frame_counts)
    count_cv = (
        statistics.pstdev(frame_counts) / mean_count
        if len(frame_counts) > 1 and mean_count > 0
        else 0.0
    )

    frame_total = max(len(frames), 1)
    evidence_total = max(len(all_evidence), 1)
    availability_score = available_frames / frame_total
    geometry_score = valid_geometry / evidence_total if all_evidence else 0.0
    nonempty_score = nonempty_frames / frame_total
    continuity_score = match_fraction if pair_metrics else 0.0
    count_stability_score = 1.0 / (1.0 + count_cv) if all_evidence else 0.0
    overall = (
        0.25 * availability_score
        + 0.20 * geometry_score
        + 0.20 * nonempty_score
        + 0.20 * continuity_score
        + 0.15 * count_stability_score
    )

    return {
        "schema": "perception_representation_health_v0",
        "score": round(overall, 5),
        "interpretation": (
            "Contract, availability, count stability, and adjacent-frame image-space continuity; "
            "this is not semantic accuracy or obstacle-detection recall."
        ),
        "evidence_kinds": sorted({str(thing.get("kind") or "unknown") for thing in all_evidence}),
        "availability": {
            "usable_frames": available_frames,
            "total_frames": len(frames),
            "score": round(availability_score, 5),
        },
        "geometry": {
            "valid_records": valid_geometry,
            "total_records": len(all_evidence),
            "score": round(geometry_score, 5),
        },
        "nonempty": {
            "frames": nonempty_frames,
            "score": round(nonempty_score, 5),
        },
        "count_stability": {
            "mean": round(mean_count, 5),
            "coefficient_of_variation": round(count_cv, 5),
            "score": round(count_stability_score, 5),
        },
        "continuity": {
            "frame_pairs": len(pair_metrics),
            "mean_match_fraction": round(match_fraction, 5),
            "mean_matched_iou": round(mean_iou, 5),
            "score": round(continuity_score, 5),
            "pairs": pair_metrics,
        },
    }


def _spatial_evidence(frame: dict[str, Any]) -> list[dict[str, Any]]:
    perception = frame.get("perception")
    things = perception.get("things") if isinstance(perception, dict) else None
    if not isinstance(things, (list, tuple)):
        return []
    spatial = [
        thing
        for thing in things
        if isinstance(thing, dict)
        and str(thing.get("kind") or "unknown") not in IGNORED_EVIDENCE_KINDS
        and isinstance((thing.get("location") or {}).get("bbox_xyxy_norm"), (list, tuple))
    ]
    region_proposals = [thing for thing in spatial if thing.get("kind") == "region_proposal"]
    return region_proposals or spatial


def _valid_thing_geometry(thing: dict[str, Any]) -> bool:
    location = thing.get("location")
    bbox = location.get("bbox_xyxy_norm") if isinstance(location, dict) else None
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return False
    confidence_value = thing.get("confidence")
    if confidence_value is None:
        return False
    try:
        x1, y1, x2, y2 = (float(value) for value in bbox)
        confidence = float(confidence_value)
    except (TypeError, ValueError):
        return False
    return (
        all(math.isfinite(value) for value in (x1, y1, x2, y2, confidence))
        and 0.0 <= x1 <= x2 <= 1.0
        and 0.0 <= y1 <= y2 <= 1.0
        and 0.0 <= confidence <= 1.0
    )


def _pair_continuity(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> dict[str, Any]:
    previous = [thing for thing in previous if _valid_thing_geometry(thing)]
    current = [thing for thing in current if _valid_thing_geometry(thing)]
    candidates: list[tuple[float, int, int]] = []
    for previous_index, left in enumerate(previous):
        for current_index, right in enumerate(current):
            if left.get("kind") != right.get("kind"):
                continue
            overlap = _bbox_iou(_bbox(left), _bbox(right))
            if overlap >= 0.05:
                candidates.append((overlap, previous_index, current_index))
    candidates.sort(reverse=True)
    used_previous: set[int] = set()
    used_current: set[int] = set()
    overlaps: list[float] = []
    for overlap, previous_index, current_index in candidates:
        if previous_index in used_previous or current_index in used_current:
            continue
        used_previous.add(previous_index)
        used_current.add(current_index)
        overlaps.append(overlap)
    denominator = max(len(previous), len(current), 1)
    return {
        "previous_count": len(previous),
        "current_count": len(current),
        "matches": len(overlaps),
        "match_fraction": round(len(overlaps) / denominator, 5),
        "mean_iou": round(_mean(overlaps), 5),
    }


def _bbox(thing: dict[str, Any]) -> tuple[float, float, float, float]:
    values = [float(value) for value in thing["location"]["bbox_xyxy_norm"]]
    return values[0], values[1], values[2], values[3]


def _bbox_iou(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def _mean(values: list[float] | list[int]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


