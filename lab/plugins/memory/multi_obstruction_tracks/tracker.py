from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from autonomy.perception import PerceivedThing, ViewLocation
from lab.plugins.perception.multi_obstruction_tracks.src.plugin import (
    _bbox, _shape_support, _clamp, _zone,
)

@dataclass
class _Track:
    track_id: int
    bbox: tuple[float, float, float, float]
    confidence: float
    shape_support: float
    flow_support: float
    age_frames: int
    missed_frames: int = 0
    last_association_score: float = 0.0
    lost_age: int = 0
    points: np.ndarray | None = None


def _track_from_lookback(item: Any) -> _Track | None:
    if not isinstance(item, dict):
        return None
    bbox = item.get("bbox")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        return _Track(
            track_id=int(item["track_id"]),
            bbox=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
            confidence=float(item.get("confidence") or 0.0),
            shape_support=float(item.get("shape_support") or 0.0),
            flow_support=float(item.get("flow_support") or 0.0),
            age_frames=int(item.get("age_frames") or 0),
            missed_frames=int(item.get("missed_frames") or 0),
            last_association_score=float(item.get("last_association_score") or 0.0),
            lost_age=int(item.get("lost_age") or 0),
        )
    except (TypeError, ValueError, KeyError):
        return None


class ObstructionTrackState:
    """Association and temporal state owned by the memory implementation."""

    def __init__(
        self,
        *,
        max_tracks=4,
        association_distance=0.35,
        minimum_association_score=0.12,
        smoothing_alpha=0.65,
        max_missed_frames=1,
        reacquire_window_frames=4,
        minimum_feature_points=6,
        output_bbox_shrink_x=1.0,
        output_bbox_shrink_y=1.0,
        minimum_output_confidence=0.0,
        floor_cutoff_y=0.72,
    ):
        self.max_tracks = max(1, int(max_tracks))
        self.floor_cutoff_y = _clamp(float(floor_cutoff_y), 0.35, 0.95)
        self.association_distance = max(0.01, float(association_distance))
        self.minimum_association_score = _clamp(
            float(minimum_association_score), 0.0, 1.0
        )
        self.smoothing_alpha = _clamp(float(smoothing_alpha), 0.05, 1.0)
        self.max_missed_frames = max(0, int(max_missed_frames))
        self.reacquire_window_frames = max(0, int(reacquire_window_frames))
        self.minimum_feature_points = max(4, int(minimum_feature_points))
        self.output_bbox_shrink_x = _clamp(float(output_bbox_shrink_x), 0.25, 1.0)
        self.output_bbox_shrink_y = _clamp(float(output_bbox_shrink_y), 0.25, 1.0)
        self.minimum_output_confidence = _clamp(float(minimum_output_confidence), 0.0, 1.0)
        self.reset()

    def reset(self) -> None:
        self._tracks: dict[int, _Track] = {}
        self._lost: dict[int, _Track] = {}
        self._next_track_id = 0
        self._frame_index = 0
        self._previous_gray: np.ndarray | None = None


    def _restore_from_prior_memory(self, prior_memory: Any) -> int:
        """Replace track continuity with the lookback record stored last frame.

        Absent memory leaves the in-process tracks alone. A present lookback
        record, including an empty track list, is the continuity for this frame.
        """

        if not isinstance(prior_memory, dict):
            return 0
        records = prior_memory.get("records")
        if not isinstance(records, list):
            return 0
        payload = None
        for record in records:
            if not isinstance(record, dict):
                continue
            provenance = record.get("provenance")
            evidence_id = (
                provenance.get("evidence_id") if isinstance(provenance, dict) else None
            )
            if evidence_id != "multi_obstruction_track_lookback":
                continue
            properties = record.get("properties")
            if isinstance(properties, dict) and isinstance(properties.get("tracks"), list):
                payload = properties["tracks"]
                break
        if payload is None:
            return 0
        active: dict[int, _Track] = {}
        lost: dict[int, _Track] = {}
        for item in payload:
            track = _track_from_lookback(item)
            if track is None:
                continue
            if item.get("slot") == "lost":
                lost[track.track_id] = track
            else:
                active[track.track_id] = track
        self._tracks = active
        self._lost = lost
        used = set(active) | set(lost)
        self._next_track_id = (max(used) + 1) if used else 0
        return len(active) + len(lost)


    def _lookback_tracks(self) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for slot, tracks in (("active", self._tracks), ("lost", self._lost)):
            for track in tracks.values():
                payload.append(
                    {
                        "slot": slot,
                        "track_id": int(track.track_id),
                        "bbox": [float(value) for value in track.bbox],
                        "confidence": float(track.confidence),
                        "shape_support": float(track.shape_support),
                        "flow_support": float(track.flow_support),
                        "age_frames": int(track.age_frames),
                        "missed_frames": int(track.missed_frames),
                        "last_association_score": float(track.last_association_score),
                        "lost_age": int(track.lost_age),
                    }
                )
        payload.sort(key=lambda item: (item["slot"], item["track_id"]))
        return payload


    def _should_emit_track(self, track: _Track, event: str) -> bool:
        """Suppress stale predicted/held boxes while retaining observed matches.

        A confidence floor is useful for removing a stale box after its
        detector evidence disappears.  Applying that floor to matched boxes
        would also remove a real obstacle as its tracked confidence decays,
        so the floor is intentionally limited to non-observed output events.
        """
        if event in {"predicted", "held"}:
            return track.confidence >= self.minimum_output_confidence
        return True


    def _associate(
        self,
        candidates: list[PerceivedThing],
        *,
        gray: np.ndarray | None = None,
    ) -> tuple[list[_Track], dict[int, str], dict[str, Any]]:
        previous = list(self._tracks.values())
        predicted: dict[int, tuple[float, float, float, float]] = {}
        feature_points: dict[int, np.ndarray] = {}
        feature_counts: dict[int, int] = {}
        if gray is not None and self._previous_gray is not None:
            for track in previous:
                result = _predict_track(
                    track,
                    self._previous_gray,
                    gray,
                    self.minimum_feature_points,
                )
                if result is None:
                    continue
                bbox, points, count = result
                predicted[track.track_id] = bbox
                feature_points[track.track_id] = points
                feature_counts[track.track_id] = count
        # Age retained identities on every frame, including frames with no
        # detector candidates.  Without this, reacquire_window_frames would
        # retain a lost identity indefinitely.
        for track_id, lost_track in list(self._lost.items()):
            lost_track.lost_age += 1
            if lost_track.lost_age > self.reacquire_window_frames:
                self._lost.pop(track_id, None)

        matches: list[tuple[float, int, int]] = []
        for track_index, track in enumerate(previous):
            for candidate_index, candidate in enumerate(candidates):
                prior_bbox = predicted.get(track.track_id, track.bbox)
                score = _association_score(
                    prior_bbox,
                    candidate.location.bbox_xyxy_norm,
                    float(candidate.confidence),
                    self.association_distance,
                )
                matches.append((score, track_index, candidate_index))
        matches.sort(reverse=True)
        used_tracks: set[int] = set()
        used_candidates: set[int] = set()
        assignments: dict[int, tuple[PerceivedThing, float]] = {}
        for score, track_index, candidate_index in matches:
            if score < self.minimum_association_score:
                break
            if track_index in used_tracks or candidate_index in used_candidates:
                continue
            used_tracks.add(track_index)
            used_candidates.add(candidate_index)
            assignments[previous[track_index].track_id] = (candidates[candidate_index], score)

        events: dict[int, str] = {}
        updated: dict[int, _Track] = {}
        association_scores: dict[str, float] = {}
        prediction_residuals: dict[str, dict[str, float]] = {}
        for track in previous:
            assigned = assignments.get(track.track_id)
            if assigned is not None:
                candidate, score = assigned
                bbox = _bbox(candidate)
                alpha = self.smoothing_alpha
                prior_bbox = predicted.get(track.track_id, track.bbox)
                if track.track_id in predicted:
                    prediction_residuals[str(track.track_id)] = {
                        "center_distance": round(
                            _center_distance(prior_bbox, bbox), 6
                        ),
                        "bbox_l1": round(_bbox_l1_distance(prior_bbox, bbox), 6),
                    }
                smoothed = tuple(
                    (1.0 - alpha) * old + alpha * new
                    for old, new in zip(prior_bbox, bbox)
                )
                event = "reacquired" if track.missed_frames > self.max_missed_frames else "matched"
                points = feature_points.get(track.track_id)
                if points is None and gray is not None:
                    points = _seed_points(gray, smoothed)
                updated[track.track_id] = _Track(
                    track_id=track.track_id,
                    bbox=smoothed,
                    confidence=_blend(track.confidence, candidate.confidence, alpha),
                    shape_support=_shape_support(candidate),
                    flow_support=min(1.0, feature_counts.get(track.track_id, 0) / 40.0),
                    age_frames=track.age_frames + 1,
                    missed_frames=0,
                    last_association_score=score,
                    points=points,
                )
                events[track.track_id] = event
                association_scores[str(track.track_id)] = round(score, 5)
                self._lost.pop(track.track_id, None)
                continue
            track.missed_frames += 1
            track.lost_age += 1
            if (
                track.track_id in predicted
                and feature_counts[track.track_id] >= self.minimum_feature_points
                and track.missed_frames <= self.max_missed_frames
            ):
                updated[track.track_id] = _Track(
                    track_id=track.track_id,
                    bbox=predicted[track.track_id],
                    confidence=_clamp(track.confidence * 0.94, 0.0, 1.0),
                    shape_support=track.shape_support,
                    flow_support=min(1.0, feature_counts[track.track_id] / 40.0),
                    age_frames=track.age_frames + 1,
                    missed_frames=track.missed_frames,
                    last_association_score=0.0,
                    lost_age=track.lost_age,
                    points=feature_points[track.track_id],
                )
                events[track.track_id] = "predicted"
            elif track.missed_frames <= self.max_missed_frames:
                updated[track.track_id] = track
                events[track.track_id] = "held"
            else:
                self._lost[track.track_id] = track
                events[track.track_id] = "lost"

        for candidate_index, candidate in enumerate(candidates):
            if candidate_index in used_candidates:
                continue
            if len(updated) >= self.max_tracks:
                break
            track_id = self._next_track_id
            self._next_track_id += 1
            bbox = _bbox(candidate)
            lost_match = self._find_lost_match(bbox)
            if lost_match is not None:
                track_id = lost_match.track_id
                self._lost.pop(track_id, None)
                event = "reacquired"
                # Reacquisition should re-enter through the same geometry
                # smoother as an ordinary match; emitting the raw contour
                # would turn a temporary detector gap into a box jump.
                bbox = tuple(
                    (1.0 - self.smoothing_alpha) * old
                    + self.smoothing_alpha * new
                    for old, new in zip(lost_match.bbox, bbox)
                )
            else:
                event = "new"
            updated[track_id] = _Track(
                track_id=track_id,
                bbox=bbox,
                confidence=float(candidate.confidence),
                shape_support=_shape_support(candidate),
                flow_support=0.0,
                age_frames=1,
                last_association_score=0.0,
                points=_seed_points(gray, bbox) if gray is not None else None,
            )
            events[track_id] = event

        self._tracks = updated
        self._lost = {
            track_id: track
            for track_id, track in self._lost.items()
            if track.lost_age <= self.reacquire_window_frames
        }
        return sorted(updated.values(), key=lambda track: track.track_id), events, {
            "matched_track_count": len(assignments),
            "candidate_count": len(candidates),
            "association_scores": association_scores,
            "prediction_residuals": prediction_residuals,
            "lost_track_ids": sorted(self._lost),
        }


    def _find_lost_match(self, bbox: tuple[float, float, float, float]) -> _Track | None:
        scored = [
            (_association_score(track.bbox, bbox, track.confidence, self.association_distance), track)
            for track in self._lost.values()
        ]
        if not scored:
            return None
        score, track = max(scored, key=lambda item: item[0])
        return track if score >= self.minimum_association_score else None


    def _materialize(self, track: _Track, event: str) -> PerceivedThing:
        bbox = _shrink_bbox(
            track.bbox,
            self.output_bbox_shrink_x,
            self.output_bbox_shrink_y,
        )
        return PerceivedThing(
            thing_id=f"obstruction_track_{track.track_id:03d}",
            kind="obstacle",
            label="temporally associated generic obstruction",
            location=ViewLocation(
                frame="image",
                zone=_zone(bbox),
                bbox_xyxy_norm=bbox,
            ),
            confidence=_clamp(track.confidence, 0.0, 1.0),
            properties={
                "evidence": "floor_suppressed_edge_region",
                "track_id": track.track_id,
                "track_event": event,
                "track_age_frames": track.age_frames,
                "track_missed_frames": track.missed_frames,
                "shape_support": track.shape_support,
                "flow_support": track.flow_support,
                "association_score": track.last_association_score,
                "floor_suppressed": True,
                "source_bbox_xyxy_norm": track.bbox,
                "output_bbox_shrink_x": self.output_bbox_shrink_x,
                "output_bbox_shrink_y": self.output_bbox_shrink_y,
                "minimum_output_confidence": self.minimum_output_confidence,
                "minimum_output_confidence_events": ["predicted", "held"],
            },
        )


def _shrink_bbox(
    bbox: tuple[float, float, float, float],
    shrink_x: float,
    shrink_y: float,
) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    width = max(0.0, (x2 - x1) * shrink_x)
    height = max(0.0, (y2 - y1) * shrink_y)
    return (
        max(0.0, cx - width / 2.0),
        max(0.0, cy - height / 2.0),
        min(1.0, cx + width / 2.0),
        min(1.0, cy + height / 2.0),
    )


def _seed_points(gray: np.ndarray | None, bbox: tuple[float, float, float, float]) -> np.ndarray | None:
    if gray is None:
        return None
    height, width = gray.shape[:2]
    x1 = max(0, min(width - 1, int(round(bbox[0] * width))))
    y1 = max(0, min(height - 1, int(round(bbox[1] * height))))
    x2 = max(x1 + 1, min(width, int(round(bbox[2] * width))))
    y2 = max(y1 + 1, min(height, int(round(bbox[3] * height))))
    mask = np.zeros_like(gray)
    mask[y1:y2, x1:x2] = 255
    points = cv2.goodFeaturesToTrack(
        gray,
        maxCorners=80,
        qualityLevel=0.01,
        minDistance=5,
        blockSize=7,
        mask=mask,
    )
    return points.reshape(-1, 2).astype(np.float32) if points is not None else None


def _predict_track(
    track: _Track,
    previous_gray: np.ndarray,
    gray: np.ndarray,
    minimum_points: int,
) -> tuple[tuple[float, float, float, float], np.ndarray, int] | None:
    if track.points is None or len(track.points) < minimum_points:
        return None
    p0 = track.points.reshape(-1, 1, 2)
    p1, status, _ = cv2.calcOpticalFlowPyrLK(
        previous_gray,
        gray,
        p0,
        None,
        winSize=(21, 21),
        maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
    )
    if p1 is None:
        return None
    p_back, status_back, _ = cv2.calcOpticalFlowPyrLK(
        gray,
        previous_gray,
        p1,
        None,
        winSize=(21, 21),
        maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
    )
    if p_back is None:
        return None
    p1_flat = p1.reshape(-1, 2)
    good = status.ravel().astype(bool) & status_back.ravel().astype(bool)
    good &= np.linalg.norm(track.points - p_back.reshape(-1, 2), axis=1) < 1.5
    height, width = gray.shape[:2]
    good &= (p1_flat[:, 0] >= 0) & (p1_flat[:, 0] < width)
    good &= (p1_flat[:, 1] >= 0) & (p1_flat[:, 1] < height)
    if int(good.sum()) < minimum_points:
        return None
    source = track.points[good]
    destination = p1_flat[good]
    transform, inlier_mask = cv2.estimateAffinePartial2D(
        source,
        destination,
        method=cv2.RANSAC,
        ransacReprojThreshold=3.0,
        maxIters=300,
        confidence=0.99,
    )
    if transform is None or inlier_mask is None:
        return None
    inliers = inlier_mask.ravel().astype(bool)
    if int(inliers.sum()) < minimum_points:
        return None
    previous_bbox = np.float32(
        [
            [track.bbox[0] * width, track.bbox[1] * height],
            [track.bbox[2] * width, track.bbox[1] * height],
            [track.bbox[2] * width, track.bbox[3] * height],
            [track.bbox[0] * width, track.bbox[3] * height],
        ]
    )
    transformed = cv2.transform(previous_bbox[None, :, :], transform)[0]
    bbox = (
        float(np.clip(transformed[:, 0].min() / width, 0.0, 1.0)),
        float(np.clip(transformed[:, 1].min() / height, 0.0, 1.0)),
        float(np.clip(transformed[:, 0].max() / width, 0.0, 1.0)),
        float(np.clip(transformed[:, 1].max() / height, 0.0, 1.0)),
    )
    return bbox, destination[inliers], int(inliers.sum())


def _center_distance(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    return math.hypot(
        ((left[0] + left[2]) / 2.0) - ((right[0] + right[2]) / 2.0),
        ((left[1] + left[3]) / 2.0) - ((right[1] + right[3]) / 2.0),
    )


def _bbox_l1_distance(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    return sum(abs(a - b) for a, b in zip(left, right))


def _candidate_center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def _association_score(
    previous: tuple[float, float, float, float],
    current: tuple[float, float, float, float],
    confidence: float,
    max_distance: float,
) -> float:
    iou = _iou(previous, current)
    old_center = _candidate_center(previous)
    new_center = _candidate_center(current)
    distance = math.hypot(old_center[0] - new_center[0], old_center[1] - new_center[1])
    # A similarly sized region far outside the association window is not a
    # plausible continuation merely because its area happens to match.
    if distance > max_distance and iou < 0.05:
        return 0.0
    distance_score = max(0.0, 1.0 - distance / max(max_distance, 1e-6))
    old_area = max(0.0, previous[2] - previous[0]) * max(0.0, previous[3] - previous[1])
    new_area = max(0.0, current[2] - current[0]) * max(0.0, current[3] - current[1])
    area_score = 1.0 - min(1.0, abs(old_area - new_area) / max(old_area, new_area, 1e-6))
    return float(0.45 * iou + 0.30 * distance_score + 0.15 * area_score + 0.10 * _clamp(confidence, 0.0, 1.0))


def _iou(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    x1, y1 = max(left[0], right[0]), max(left[1], right[1])
    x2, y2 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0.0 else 0.0


def _blend(old: float, new: float, alpha: float) -> float:
    return (1.0 - alpha) * float(old) + alpha * float(new)
