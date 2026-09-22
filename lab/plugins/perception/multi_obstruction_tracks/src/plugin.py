from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from autonomy.perception import (
    PerceivedThing,
    PerceptionEvidenceBatch,
    PerceptionPluginContract,
    PerceptionPluginInputs,
    PerceptionSignal,
    ViewLocation,
)
from implementations.perception.components import CameraFrame, FRONT_CAMERA_RGB_INPUT


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


class MultiObstructionTracksPlugin:
    """Associate multiple generic obstruction regions after floor suppression."""

    plugin_id = "multi-obstruction-tracks-v0"
    contract = PerceptionPluginContract(
        inputs=(FRONT_CAMERA_RGB_INPUT,),
        state_mode="windowed",
        description=(
            "Suppress lower-frame floor-like regions and associate multiple "
            "generic image-space obstruction regions across adjacent frames."
        ),
        assumptions=(
            "obstruction regions have enough rectangular edge and shape support",
            "adjacent-frame image geometry changes within the association window",
            "lower-frame floor/background regions are not themselves obstructions",
        ),
        emits=(
            "signal multi_obstruction_tracks_available",
            "multiple image-space obstacle records with bounded temporal ids",
        ),
        limitations=(
            "regions are generic image evidence, not semantic objects",
            "shape_support and flow_support are separate evidence scores, not semantic identity",
            "camera motion and lighting can split, merge, or misassociate regions",
            "lost tracks are dropped after a bounded grace period",
        ),
        diagnostic_artifacts=("track_summary", "edge_summary"),
    )

    def __init__(
        self,
        *,
        max_tracks: int = 4,
        floor_cutoff_y: float = 0.72,
        minimum_object_height: float = 0.10,
        minimum_object_area_fraction: float = 0.006,
        maximum_object_area_fraction: float = 0.60,
        association_distance: float = 0.35,
        minimum_association_score: float = 0.12,
        smoothing_alpha: float = 0.65,
        max_missed_frames: int = 1,
        reacquire_window_frames: int = 4,
        minimum_feature_points: int = 6,
        canny_low: int = 20,
        canny_high: int = 40,
        minimum_contour_area_fraction: float = 0.0015,
        maximum_contour_area_fraction: float = 0.25,
        contour_merge_gap: float = 0.16,
        contrast_normalization: str = "none",
        contrast_clip_limit: float = 2.0,
        contrast_tile_size: int = 8,
        contrast_gamma: float = 1.0,
        shared_blur_kernel: int = 0,
        shared_close_kernel: int = 0,
        shared_min_width_px: int = 0,
        shared_min_height_px: int = 0,
        shared_max_vertices: int = 0,
        preserve_separate_proposals: bool = False,
        duplicate_suppression_iou: float = 0.08,
        output_bbox_shrink_x: float = 1.0,
        output_bbox_shrink_y: float = 1.0,
        minimum_output_confidence: float = 0.0,
        **_unused_config: Any,
    ) -> None:
        self.max_tracks = max(1, int(max_tracks))
        self.floor_cutoff_y = _clamp(float(floor_cutoff_y), 0.35, 0.95)
        self.minimum_object_height = _clamp(float(minimum_object_height), 0.02, 1.0)
        self.minimum_object_area_fraction = _clamp(
            float(minimum_object_area_fraction), 0.0, 1.0
        )
        self.maximum_object_area_fraction = _clamp(
            float(maximum_object_area_fraction),
            self.minimum_object_area_fraction,
            1.0,
        )
        self.association_distance = max(0.01, float(association_distance))
        self.minimum_association_score = _clamp(
            float(minimum_association_score), 0.0, 1.0
        )
        self.smoothing_alpha = _clamp(float(smoothing_alpha), 0.05, 1.0)
        self.max_missed_frames = max(0, int(max_missed_frames))
        self.reacquire_window_frames = max(0, int(reacquire_window_frames))
        self.minimum_feature_points = max(4, int(minimum_feature_points))
        self.canny_low = max(1, int(canny_low))
        self.canny_high = max(self.canny_low + 1, int(canny_high))
        self.minimum_contour_area_fraction = _clamp(
            float(minimum_contour_area_fraction), 0.0001, 1.0
        )
        self.maximum_contour_area_fraction = _clamp(
            float(maximum_contour_area_fraction),
            self.minimum_contour_area_fraction,
            1.0,
        )
        self.contour_merge_gap = _clamp(float(contour_merge_gap), 0.0, 0.5)
        self.contrast_normalization = str(contrast_normalization or "none").lower()
        if self.contrast_normalization not in {"none", "clahe", "stretch", "gamma"}:
            self.contrast_normalization = "none"
        self.contrast_clip_limit = _clamp(float(contrast_clip_limit), 0.1, 10.0)
        self.contrast_tile_size = max(2, min(32, int(contrast_tile_size)))
        self.contrast_gamma = _clamp(float(contrast_gamma), 0.25, 4.0)
        self.shared_blur_kernel = _odd_kernel(shared_blur_kernel)
        self.shared_close_kernel = _odd_kernel(shared_close_kernel)
        self.shared_min_width_px = max(0, int(shared_min_width_px))
        self.shared_min_height_px = max(0, int(shared_min_height_px))
        self.shared_max_vertices = max(0, int(shared_max_vertices))
        self.preserve_separate_proposals = bool(preserve_separate_proposals)
        self.duplicate_suppression_iou = _clamp(
            float(duplicate_suppression_iou), 0.0, 1.0
        )
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

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        frame = inputs.require("frame", CameraFrame)
        memory_tracks_read = self._restore_from_prior_memory(
            inputs.metadata.get("prior_memory")
        )
        candidates, gray, detector_summary = self._detect_candidates(frame.rgb)
        active, events, association = self._associate(candidates, gray=gray)

        emitted_tracks = [
            track
            for track in active
            if self._should_emit_track(track, events.get(track.track_id, "matched"))
        ]
        things = tuple(
            self._materialize(track, events.get(track.track_id, "matched"))
            for track in emitted_tracks
        )
        lookback_tracks = self._lookback_tracks()
        signals = (
            PerceptionSignal(
                "multi_obstruction_tracks_available",
                bool(things),
                _mean_confidence(things),
                {
                    "candidate_count": len(candidates),
                    "active_track_count": len(things),
                    "track_events": {
                        str(track_id): event for track_id, event in events.items()
                    },
                    "floor_cutoff_y": self.floor_cutoff_y,
                    "memory_tracks_read": memory_tracks_read,
                    "memory_tracks_written": len(lookback_tracks),
                },
            ),
            PerceptionSignal(
                "multi_obstruction_track_lookback",
                True,
                _mean_confidence(things) if things else 1.0,
                {
                    "tracks": lookback_tracks,
                    "memory_tracks_read": memory_tracks_read,
                    "memory_tracks_written": len(lookback_tracks),
                },
            ),
        )
        measurements = {
            "candidate_count": len(candidates),
            "active_track_count": len(things),
            "track_ids": [thing.thing_id for thing in things],
            "track_events": events,
            "detector": detector_summary,
            "association": association,
            "floor_cutoff_y": self.floor_cutoff_y,
            "minimum_object_height": self.minimum_object_height,
            "memory_tracks_read": memory_tracks_read,
            "memory_tracks_written": len(lookback_tracks),
        }
        if inputs.diagnostics.enabled:
            inputs.diagnostics.emit_json(
                "track_summary",
                "track_summary.json",
                {
                    "frame_id": inputs.frame_id,
                    "frame_index": self._frame_index,
                    "candidate_count": len(candidates),
                    "active": [
                        {
                            "track_id": track.track_id,
                            "bbox_xyxy_norm": track.bbox,
                            "confidence": track.confidence,
                            "shape_support": track.shape_support,
                            "flow_support": track.flow_support,
                            "event": events.get(track.track_id, "matched"),
                            "age_frames": track.age_frames,
                            "missed_frames": track.missed_frames,
                            "association_score": track.last_association_score,
                        }
                        for track in emitted_tracks
                    ],
                    "events": events,
                    "lost_track_ids": sorted(self._lost),
                    "association": association,
                    "detector": detector_summary,
                },
            )
            inputs.diagnostics.emit_json(
                "edge_summary",
                "edge_summary.json",
                detector_summary,
            )
        self._frame_index += 1
        self._previous_gray = gray
        return PerceptionEvidenceBatch(
            signals=signals,
            things=things,
            measurements=measurements,
        )

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

    def _detect_candidates(
        self, rgb: np.ndarray
    ) -> tuple[list[PerceivedThing], np.ndarray, dict[str, Any]]:
        height, width = rgb.shape[:2]
        gray = self._normalize_gray(rgb)
        edge_gray = gray
        if self.shared_blur_kernel:
            edge_gray = cv2.GaussianBlur(
                edge_gray,
                (self.shared_blur_kernel, self.shared_blur_kernel),
                0,
            )
        edges = cv2.Canny(edge_gray, self.canny_low, self.canny_high)
        edges = cv2.morphologyEx(
            edges,
            cv2.MORPH_CLOSE,
            np.ones(
                (self.shared_close_kernel or 3, self.shared_close_kernel or 3),
                dtype=np.uint8,
            ),
        )
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        proposals: list[dict[str, Any]] = []
        image_area = max(1, width * height)
        for contour in contours:
            x, y, component_width, component_height = cv2.boundingRect(contour)
            contour_area = float(cv2.contourArea(contour))
            area_fraction = contour_area / image_area
            perimeter = cv2.arcLength(contour, True)
            approximation = cv2.approxPolyDP(contour, 0.03 * perimeter, True)
            x1, y1 = x / width, y / height
            x2, y2 = (x + component_width) / width, (y + component_height) / height
            width_fraction = x2 - x1
            height_fraction = y2 - y1
            if not (4 <= len(approximation) <= 15):
                continue
            if not (
                self.minimum_contour_area_fraction
                <= area_fraction
                <= self.maximum_contour_area_fraction
            ):
                continue
            if not (0.08 <= height_fraction <= 0.60 and 0.04 <= width_fraction <= 0.85):
                continue
            if y1 >= 0.95 or y2 <= 0.28:
                continue
            # Small top-edge rectangles are usually window/wall details. Keep
            # large rising regions because a near obstacle may fill the frame.
            if y1 < 0.03 and height_fraction < 0.25:
                continue
            edge_density = float(edges[y : y + component_height, x : x + component_width].mean() / 255.0)
            rectangularity = _clamp(
                contour_area / max(float(component_width * component_height), 1.0),
                0.0,
                1.0,
            )
            confidence = _clamp(
                0.45 * edge_density
                + 0.35 * rectangularity
                + 0.20 * min(1.0, area_fraction / 0.03),
                0.0,
                1.0,
            )
            proposals.append(
                {
                    "bbox": (x1, y1, x2, y2),
                    "confidence": confidence,
                    "edge_density": edge_density,
                    "rectangularity": rectangularity,
                    "area_fraction": area_fraction,
                }
            )
        if self.preserve_separate_proposals:
            proposals = _suppress_duplicate_proposals(
                proposals, self.duplicate_suppression_iou
            )
        else:
            proposals = _merge_proposals(
                proposals,
                self.contour_merge_gap,
                duplicate_suppression_iou=self.duplicate_suppression_iou,
            )
        things = [
            PerceivedThing(
                thing_id=f"edge_region_{index:03d}",
                kind="region_proposal",
                label="rectangular foreground edge region",
                location=ViewLocation(
                    frame="image",
                    zone=_zone(proposal["bbox"]),
                    bbox_xyxy_norm=proposal["bbox"],
                ),
                confidence=proposal["confidence"],
                properties={
                    "evidence": "floor_suppressed_edge_contour",
                    "area_fraction": proposal["area_fraction"],
                    "edge_density": proposal["edge_density"],
                    "rectangularity": proposal["rectangularity"],
                    "touches_lower_image": proposal["bbox"][3] >= 0.95,
                },
            )
            for index, proposal in enumerate(proposals)
        ]
        things = self._filter_candidates(tuple(things))
        return things, gray, {
            "edge_pixels_fraction": round(float(np.mean(edges > 0)), 6),
            "raw_contour_count": len(contours),
            "proposal_count": len(things),
            "canny_low": self.canny_low,
            "canny_high": self.canny_high,
            "shared_blur_kernel": self.shared_blur_kernel,
            "shared_close_kernel": self.shared_close_kernel,
            "shared_min_width_px": self.shared_min_width_px,
            "shared_min_height_px": self.shared_min_height_px,
            "shared_max_vertices": self.shared_max_vertices,
            "contrast_normalization": self.contrast_normalization,
            "preserve_separate_proposals": self.preserve_separate_proposals,
            "duplicate_suppression_iou": self.duplicate_suppression_iou,
            "minimum_output_confidence": self.minimum_output_confidence,
            "minimum_output_confidence_events": ["predicted", "held"],
        }

    def _normalize_gray(self, rgb: np.ndarray) -> np.ndarray:
        """Apply a parameterized, capture-agnostic luminance normalization."""
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        if self.contrast_normalization == "clahe":
            return cv2.createCLAHE(
                clipLimit=self.contrast_clip_limit,
                tileGridSize=(self.contrast_tile_size, self.contrast_tile_size),
            ).apply(gray)
        if self.contrast_normalization == "stretch":
            return cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
        if self.contrast_normalization == "gamma":
            lut = np.array(
                [((index / 255.0) ** self.contrast_gamma) * 255.0 for index in range(256)],
                dtype=np.float32,
            ).clip(0.0, 255.0).astype(np.uint8)
            return cv2.LUT(gray, lut)
        return gray

    def _filter_candidates(self, things: tuple[PerceivedThing, ...]) -> list[PerceivedThing]:
        selected: list[PerceivedThing] = []
        for thing in things:
            if thing.kind != "region_proposal":
                continue
            bbox = thing.location.bbox_xyxy_norm
            if bbox is None or len(bbox) != 4:
                continue
            x1, y1, x2, y2 = (float(value) for value in bbox)
            width = max(0.0, x2 - x1)
            height = max(0.0, y2 - y1)
            area = width * height
            if height < self.minimum_object_height:
                continue
            if area < self.minimum_object_area_fraction or area > self.maximum_object_area_fraction:
                continue
            # Floor/background suppression: carpet-like regions begin deep in
            # the image and touch the lower edge without rising into the scene.
            if y1 >= self.floor_cutoff_y - 0.12 and bool(thing.properties.get("touches_lower_image")):
                continue
            if y1 >= self.floor_cutoff_y and height < 0.25:
                continue
            # Reject full-width wall/floor bands while retaining box-like spans.
            if width > 0.80 and height < 0.35:
                continue
            if x1 >= 0.88 and width < 0.15:
                continue
            if y1 < 0.05 and x1 < 0.65 and width < 0.12:
                continue
            selected.append(thing)
        selected.sort(
            key=lambda thing: (
                float(thing.confidence),
                float(thing.properties.get("area_fraction", 0.0)),
            ),
            reverse=True,
        )
        return selected[: max(self.max_tracks * 4, self.max_tracks)]

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


def _merge_proposals(
    proposals: list[dict[str, Any]],
    gap: float,
    *,
    duplicate_suppression_iou: float = 0.08,
) -> list[dict[str, Any]]:
    """Merge overlapping or vertically split contours into object-like boxes."""
    pending = list(proposals)
    merged: list[dict[str, Any]] = []
    while pending:
        current = pending.pop(0)
        changed = True
        while changed:
            changed = False
            for index, other in enumerate(pending):
                if not _proposals_related(
                    current,
                    other,
                    gap,
                    duplicate_suppression_iou=duplicate_suppression_iou,
                ):
                    continue
                current = {
                    "bbox": (
                        min(current["bbox"][0], other["bbox"][0]),
                        min(current["bbox"][1], other["bbox"][1]),
                        max(current["bbox"][2], other["bbox"][2]),
                        max(current["bbox"][3], other["bbox"][3]),
                    ),
                    "confidence": max(current["confidence"], other["confidence"]),
                    "edge_density": max(current["edge_density"], other["edge_density"]),
                    "rectangularity": max(current["rectangularity"], other["rectangularity"]),
                    "area_fraction": max(current["area_fraction"], other["area_fraction"]),
                }
                pending.pop(index)
                changed = True
                break
        merged.append(current)
    merged.sort(key=lambda proposal: proposal["confidence"], reverse=True)
    return merged


def _suppress_duplicate_proposals(
    proposals: list[dict[str, Any]], iou_threshold: float
) -> list[dict[str, Any]]:
    """Drop near-identical contours while retaining overlapping objects."""
    kept: list[dict[str, Any]] = []
    for proposal in sorted(proposals, key=lambda item: item["confidence"], reverse=True):
        bbox = proposal["bbox"]
        duplicate = any(
            _bbox_iou(bbox, other["bbox"]) >= iou_threshold
            for other in kept
        )
        if not duplicate:
            kept.append(proposal)
    return kept


def _bbox_iou(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    ix = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    iy = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = ix * iy
    left_area = max(1e-9, (left[2] - left[0]) * (left[3] - left[1]))
    right_area = max(1e-9, (right[2] - right[0]) * (right[3] - right[1]))
    return intersection / max(1e-9, left_area + right_area - intersection)


def _proposals_related(
    left: dict[str, Any],
    right: dict[str, Any],
    gap: float,
    *,
    duplicate_suppression_iou: float = 0.08,
) -> bool:
    a = left["bbox"]
    b = right["bbox"]
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area_a = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))
    iou = (ix * iy) / max(1e-6, area_a + area_b - ix * iy)
    if iou >= duplicate_suppression_iou:
        return True
    horizontal_overlap = ix / max(1e-6, min(a[2] - a[0], b[2] - b[0]))
    vertical_overlap = iy / max(1e-6, min(a[3] - a[1], b[3] - b[1]))
    horizontal_gap = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    if vertical_overlap >= 0.50 and horizontal_gap <= 0.02:
        return True
    vertical_gap = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return horizontal_overlap >= 0.35 and vertical_gap <= gap


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


def _bbox(thing: PerceivedThing) -> tuple[float, float, float, float]:
    bbox = thing.location.bbox_xyxy_norm
    if bbox is None:
        return (0.0, 0.0, 0.0, 0.0)
    return tuple(float(value) for value in bbox)  # type: ignore[return-value]


def _shape_support(thing: PerceivedThing) -> float:
    edge_density = float(thing.properties.get("edge_density", 0.0))
    rectangularity = float(thing.properties.get("rectangularity", 0.0))
    return round(_clamp(0.55 * edge_density + 0.45 * rectangularity, 0.0, 1.0), 5)


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


def _zone(bbox: tuple[float, float, float, float]) -> str:
    cx = (bbox[0] + bbox[2]) / 2.0
    cy = (bbox[1] + bbox[3]) / 2.0
    # Keep the lateral bands aligned with the proposal plugin's image-space
    # fallback thresholds.  This lets a centered-looking box still contribute
    # when its bbox supplies an unambiguous side cue.
    horizontal = "left" if cx < 0.45 else "right" if cx > 0.55 else "center"
    vertical = "near" if cy > 0.66 else "far" if cy < 0.33 else "mid"
    return f"{vertical}_{horizontal}"


def _mean_confidence(things: tuple[PerceivedThing, ...]) -> float:
    if not things:
        return 0.0
    return float(sum(thing.confidence for thing in things) / len(things))


def _blend(old: float, new: float, alpha: float) -> float:
    return (1.0 - alpha) * float(old) + alpha * float(new)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _odd_kernel(value: int) -> int:
    """Return an odd OpenCV kernel size, with zero preserving cue defaults."""
    parsed = int(value)
    if parsed <= 0:
        return 0
    parsed = max(3, min(15, parsed))
    return parsed if parsed % 2 else parsed + 1
