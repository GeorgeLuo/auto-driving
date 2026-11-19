"""Obstruction tracks for the Workbench obstruction pipeline.

Detects region candidates in the current frame, rejects boxes against a fixed
image cut-off, and associates the rest into tracks with optical-flow support.
The cut-off is an image-row threshold. This plugin does not read floor-plugin
output. Track history (the previous grayscale image, track records, and the
next track id) lives in the host map at ``perception.<plugin_id>.history``.
The tracked things are the plugin's evidence; the memory step retains them
for the ``avoid_recent_obstruction`` proposal plugin.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from autonomy.decision_cycle.confidence import confidence_rank
from autonomy.fields import FieldError, require_number, require_safe_int
from autonomy.decision_cycle.perception.evidence.values import (
    PerceivedThing,
    PerceptionEvidenceBatch,
    PerceptionSignal,
    ViewLocation,
)
from autonomy.decision_cycle.perception.plugin import (
    PerceptionPluginContract,
    PerceptionPluginInputs,
)
from implementations.decision_cycle.perception.feeds.camera import (
    CameraFrame,
    FRONT_CAMERA_RGB_INPUT,
)
from implementations.decision_cycle.perception.shared.image.contrast import normalize_gray
from implementations.decision_cycle.perception.shared.image.stored import StoredImage
from implementations.decision_cycle.perception.shared.obstructions.boxes import clamp, zone
from implementations.decision_cycle.perception.shared.obstructions.tracker import (
    ObstructionTrackState,
)


TRACKING_CONFIG_FIELDS = (
    "max_tracks",
    "association_distance",
    "minimum_association_score",
    "smoothing_alpha",
    "max_missed_frames",
    "reacquire_window_frames",
    "minimum_feature_points",
    "output_bbox_shrink_x",
    "output_bbox_shrink_y",
    "minimum_output_confidence",
    "floor_cutoff_y",
    "flow_window",
    "flow_levels",
    "flow_forward_backward_px",
    "feature_seed_cap",
)


class MultiObstructionTracksPlugin:
    """Detect image-cutoff candidates and associate them into tracks."""

    plugin_id = "multi_obstruction_tracks"
    contract = PerceptionPluginContract(
        inputs=(FRONT_CAMERA_RGB_INPUT,),
        state_mode="windowed",
        memory_required=True,
        description=(
            "Detect obstruction candidates, drop boxes below a fixed image "
            "cut-off, and associate the rest into tracks with optical-flow support."
        ),
        assumptions=(
            "obstruction regions have enough rectangular edge and shape support",
            "adjacent-frame image geometry changes within the association window",
            "boxes below the fixed image cut-off are background, not floor-plugin output",
        ),
        emits=(
            "signal multi_obstruction_tracks_available",
            "image-space obstruction tracks with bounded temporal ids",
        ),
        limitations=(
            "regions are generic image evidence, not semantic objects",
            "camera motion and lighting can split, merge, or misassociate regions",
            "lost tracks are dropped after a bounded grace period",
        ),
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
        contour_min_vertices: int = 4,
        contour_max_vertices: int = 15,
        contour_min_width: float = 0.04,
        contour_max_width: float = 0.85,
        contour_min_height: float = 0.08,
        contour_max_height: float = 0.60,
        reject_bottom_y: float = 0.95,
        reject_above_y: float = 0.28,
        reject_top_edge_y: float = 0.03,
        reject_top_edge_height: float = 0.25,
        flow_window: int = 21,
        flow_levels: int = 3,
        flow_forward_backward_px: float = 1.5,
        feature_seed_cap: int = 80,
        **_unused_config: Any,
    ) -> None:
        self.max_tracks = max(1, int(max_tracks))
        self.floor_cutoff_y = clamp(float(floor_cutoff_y), 0.35, 0.95)
        self.minimum_object_height = clamp(float(minimum_object_height), 0.02, 1.0)
        self.minimum_object_area_fraction = clamp(
            float(minimum_object_area_fraction), 0.0, 1.0
        )
        self.maximum_object_area_fraction = clamp(
            float(maximum_object_area_fraction),
            self.minimum_object_area_fraction,
            1.0,
        )
        self.association_distance = max(0.01, float(association_distance))
        self.minimum_association_score = clamp(
            float(minimum_association_score), 0.0, 1.0
        )
        self.smoothing_alpha = clamp(float(smoothing_alpha), 0.05, 1.0)
        self.max_missed_frames = max(0, int(max_missed_frames))
        self.reacquire_window_frames = max(0, int(reacquire_window_frames))
        self.minimum_feature_points = max(4, int(minimum_feature_points))
        self.canny_low = max(1, int(canny_low))
        self.canny_high = max(self.canny_low + 1, int(canny_high))
        self.minimum_contour_area_fraction = clamp(
            float(minimum_contour_area_fraction), 0.0001, 1.0
        )
        self.maximum_contour_area_fraction = clamp(
            float(maximum_contour_area_fraction),
            self.minimum_contour_area_fraction,
            1.0,
        )
        self.contour_merge_gap = clamp(float(contour_merge_gap), 0.0, 0.5)
        self.contrast_normalization = str(contrast_normalization or "none").lower()
        if self.contrast_normalization not in {"none", "clahe", "stretch", "gamma"}:
            self.contrast_normalization = "none"
        self.contrast_clip_limit = clamp(float(contrast_clip_limit), 0.1, 10.0)
        self.contrast_tile_size = max(2, min(32, int(contrast_tile_size)))
        self.contrast_gamma = clamp(float(contrast_gamma), 0.25, 4.0)
        self.shared_blur_kernel = _odd_kernel(shared_blur_kernel)
        self.shared_close_kernel = _odd_kernel(shared_close_kernel)
        self.shared_min_width_px = max(0, int(shared_min_width_px))
        self.shared_min_height_px = max(0, int(shared_min_height_px))
        self.shared_max_vertices = max(0, int(shared_max_vertices))
        self.preserve_separate_proposals = bool(preserve_separate_proposals)
        self.duplicate_suppression_iou = clamp(
            float(duplicate_suppression_iou), 0.0, 1.0
        )
        self.output_bbox_shrink_x = clamp(float(output_bbox_shrink_x), 0.25, 1.0)
        self.output_bbox_shrink_y = clamp(float(output_bbox_shrink_y), 0.25, 1.0)
        self.minimum_output_confidence = clamp(float(minimum_output_confidence), 0.0, 1.0)
        self._assign_shape_and_flow_limits(
            contour_min_vertices=contour_min_vertices,
            contour_max_vertices=contour_max_vertices,
            contour_min_width=contour_min_width,
            contour_max_width=contour_max_width,
            contour_min_height=contour_min_height,
            contour_max_height=contour_max_height,
            reject_bottom_y=reject_bottom_y,
            reject_above_y=reject_above_y,
            reject_top_edge_y=reject_top_edge_y,
            reject_top_edge_height=reject_top_edge_height,
            flow_window=flow_window,
            flow_levels=flow_levels,
            flow_forward_backward_px=flow_forward_backward_px,
            feature_seed_cap=feature_seed_cap,
        )

    def _assign_shape_and_flow_limits(
        self,
        *,
        contour_min_vertices: int,
        contour_max_vertices: int,
        contour_min_width: float,
        contour_max_width: float,
        contour_min_height: float,
        contour_max_height: float,
        reject_bottom_y: float,
        reject_above_y: float,
        reject_top_edge_y: float,
        reject_top_edge_height: float,
        flow_window: int,
        flow_levels: int,
        flow_forward_backward_px: float,
        feature_seed_cap: int,
    ) -> None:
        self.contour_min_vertices = require_safe_int(
            contour_min_vertices, field_name="contour_min_vertices"
        )
        self.contour_max_vertices = require_safe_int(
            contour_max_vertices, field_name="contour_max_vertices"
        )
        if self.contour_min_vertices > self.contour_max_vertices:
            raise FieldError("contour_min_vertices", "must be <= contour_max_vertices")
        self.contour_min_width = require_number(
            contour_min_width, field_name="contour_min_width", bounds=(0, 1)
        )
        self.contour_max_width = require_number(
            contour_max_width, field_name="contour_max_width", bounds=(0, 1)
        )
        if self.contour_min_width > self.contour_max_width:
            raise FieldError("contour_min_width", "must be <= contour_max_width")
        self.contour_min_height = require_number(
            contour_min_height, field_name="contour_min_height", bounds=(0, 1)
        )
        self.contour_max_height = require_number(
            contour_max_height, field_name="contour_max_height", bounds=(0, 1)
        )
        if self.contour_min_height > self.contour_max_height:
            raise FieldError("contour_min_height", "must be <= contour_max_height")
        self.reject_bottom_y = require_number(
            reject_bottom_y, field_name="reject_bottom_y", bounds=(0, 1)
        )
        self.reject_above_y = require_number(
            reject_above_y, field_name="reject_above_y", bounds=(0, 1)
        )
        self.reject_top_edge_y = require_number(
            reject_top_edge_y, field_name="reject_top_edge_y", bounds=(0, 1)
        )
        self.reject_top_edge_height = require_number(
            reject_top_edge_height, field_name="reject_top_edge_height", bounds=(0, 1)
        )
        self.flow_window = require_safe_int(flow_window, field_name="flow_window")
        if self.flow_window < 1:
            raise FieldError("flow_window", "must be >= 1")
        self.flow_levels = require_safe_int(flow_levels, field_name="flow_levels")
        self.flow_forward_backward_px = require_number(
            flow_forward_backward_px,
            field_name="flow_forward_backward_px",
            bounds=(0, float("inf")),
        )
        self.feature_seed_cap = require_safe_int(
            feature_seed_cap, field_name="feature_seed_cap"
        )
        if self.feature_seed_cap < 1:
            raise FieldError("feature_seed_cap", "must be >= 1")

    @property
    def _memory_key(self) -> str:
        return f"perception.{self.plugin_id}.history"

    def reset(self, shared_memory=None) -> None:
        if shared_memory is not None:
            shared_memory.pop(self._memory_key, None)

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        if inputs.shared_memory is None:
            raise ValueError(f"{self.plugin_id} requires host shared memory")
        frame = inputs.require("frame", CameraFrame)
        candidates, gray, detector_summary = self._detect_candidates(frame.rgb)

        # The associator is rebuilt each frame from the history in the map.
        tracker = ObstructionTrackState(
            **{name: getattr(self, name) for name in TRACKING_CONFIG_FIELDS}
        )
        tracks_read = _restore_history(tracker, inputs.shared_memory.get(self._memory_key))
        active, events, association = tracker._associate(candidates, gray=gray)

        things = tuple(
            tracker._materialize(track, events.get(track.track_id, "matched"))
            for track in active
            if tracker._should_emit_track(track, events.get(track.track_id, "matched"))
        )
        tracks = tracker._lookback_tracks()
        inputs.shared_memory[self._memory_key] = {
            "tracks": tracks,
            "previous_gray": None if gray is None else StoredImage(gray),
            "next_track_id": tracker._next_track_id,
        }

        track_events = {str(track_id): event for track_id, event in events.items()}
        return PerceptionEvidenceBatch(
            things=things,
            signals=(
                PerceptionSignal(
                    "multi_obstruction_tracks_available",
                    bool(things),
                    _mean_confidence(things),
                    {
                        "candidate_count": len(candidates),
                        "active_track_count": len(things),
                        "track_events": track_events,
                        "floor_cutoff_y": tracker.floor_cutoff_y,
                        "history_tracks_read": tracks_read,
                        "history_tracks_written": len(tracks),
                    },
                ),
            ),
            measurements={
                "candidate_count": len(candidates),
                "active_track_count": len(things),
                "track_ids": [thing.thing_id for thing in things],
                "track_events": track_events,
                "detector": detector_summary,
                "association": association,
                "floor_cutoff_y": tracker.floor_cutoff_y,
                "history_tracks_read": tracks_read,
                "history_tracks_written": len(tracks),
            },
        )

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
            if not (self.contour_min_vertices <= len(approximation) <= self.contour_max_vertices):
                continue
            if not (
                self.minimum_contour_area_fraction
                <= area_fraction
                <= self.maximum_contour_area_fraction
            ):
                continue
            if not (
                self.contour_min_height <= height_fraction <= self.contour_max_height
                and self.contour_min_width <= width_fraction <= self.contour_max_width
            ):
                continue
            if y1 >= self.reject_bottom_y or y2 <= self.reject_above_y:
                continue
            # Small top-edge rectangles are usually window/wall details. Keep
            # large rising regions because a near obstacle may fill the frame.
            if y1 < self.reject_top_edge_y and height_fraction < self.reject_top_edge_height:
                continue
            edge_density = float(edges[y : y + component_height, x : x + component_width].mean() / 255.0)
            rectangularity = clamp(
                contour_area / max(float(component_width * component_height), 1.0),
                0.0,
                1.0,
            )
            confidence = clamp(
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
                    zone=zone(proposal["bbox"]),
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
        return normalize_gray(rgb, contrast_normalization=self.contrast_normalization,
                              contrast_clip_limit=self.contrast_clip_limit,
                              contrast_tile_size=self.contrast_tile_size,
                              contrast_gamma=self.contrast_gamma)

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
                *confidence_rank(thing.confidence),
                -float(thing.properties.get("area_fraction", 0.0)),
            )
        )
        return selected[: max(self.max_tracks * 4, self.max_tracks)]


def _restore_history(tracker: ObstructionTrackState, history: Any) -> int:
    """Rebuild the tracker from the map; return how many tracks were read."""

    history = history if isinstance(history, dict) else {}
    previous_gray = history.get("previous_gray")
    tracker._previous_gray = (
        np.array(previous_gray.pixels, copy=True)
        if isinstance(previous_gray, StoredImage)
        else None
    )
    return tracker._restore_from_history(history.get("tracks"), history.get("next_track_id"))


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


def _mean_confidence(things: tuple[PerceivedThing, ...]) -> float | None:
    if not things:
        return 0.0
    scored = [thing.confidence for thing in things if thing.confidence is not None]
    if not scored:
        return None
    return float(sum(scored) / len(scored))


def _odd_kernel(value: int) -> int:
    """Return an odd OpenCV kernel size, with zero preserving cue defaults."""
    parsed = int(value)
    if parsed <= 0:
        return 0
    parsed = max(3, min(15, parsed))
    return parsed if parsed % 2 else parsed + 1
