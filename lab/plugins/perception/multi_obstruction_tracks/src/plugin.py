from __future__ import annotations

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
)


class MultiObstructionTracksPlugin:
    """Detect candidates; cross-frame tracking is owned by the memory plugin."""

    plugin_id = "multi-obstruction-tracks-v0"
    contract = PerceptionPluginContract(
        inputs=(FRONT_CAMERA_RGB_INPUT,),
        state_mode="stateless",
        description=(
            "Detect floor-suppressed candidates for the tracking memory plugin."
        ),
        assumptions=(
            "obstruction regions have enough rectangular edge and shape support",
            "lower-frame floor/background regions are not themselves obstructions",
        ),
        emits=(
            "signal multi_obstruction_candidates",
            "image-space region proposals for memory-stage association",
        ),
        limitations=(
            "regions are generic image evidence, not semantic objects",
            "camera motion and lighting can split or merge regions",
            "tracking requires the Workbench memory implementation",
        ),
        diagnostic_artifacts=("edge_summary",),
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


    def reset(self) -> None:
        pass  # Detection has no cross-frame state.

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        frame = inputs.require("frame", CameraFrame)
        candidates, _gray, detector_summary = self._detect_candidates(frame.rgb)
        history = inputs.memory.get("multi_obstruction_tracks.history", ()) if inputs.memory is not None else ()
        config = {name: getattr(self, name) for name in TRACKING_CONFIG_FIELDS}
        normalization = {name: getattr(self, name) for name in (
            "contrast_normalization", "contrast_clip_limit", "contrast_tile_size", "contrast_gamma",
        )}
        if inputs.diagnostics.enabled:
            inputs.diagnostics.emit_json("edge_summary", "edge_summary.json", detector_summary)
        return PerceptionEvidenceBatch(
            things=tuple(candidates),
            signals=(PerceptionSignal("multi_obstruction_candidates", True, 1.0, {
                "tracking_config": config, "normalization": normalization,
            }),),
            measurements={"candidate_count": len(candidates), "detector": detector_summary,
                          "memory_tracks_read": len(history)},
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
                float(thing.confidence),
                float(thing.properties.get("area_fraction", 0.0)),
            ),
            reverse=True,
        )
        return selected[: max(self.max_tracks * 4, self.max_tracks)]


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


def _bbox(thing: PerceivedThing) -> tuple[float, float, float, float]:
    bbox = thing.location.bbox_xyxy_norm
    if bbox is None:
        return (0.0, 0.0, 0.0, 0.0)
    return tuple(float(value) for value in bbox)  # type: ignore[return-value]


def _shape_support(thing: PerceivedThing) -> float:
    edge_density = float(thing.properties.get("edge_density", 0.0))
    rectangularity = float(thing.properties.get("rectangularity", 0.0))
    return round(_clamp(0.55 * edge_density + 0.45 * rectangularity, 0.0, 1.0), 5)


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


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _odd_kernel(value: int) -> int:
    """Return an odd OpenCV kernel size, with zero preserving cue defaults."""
    parsed = int(value)
    if parsed <= 0:
        return 0
    parsed = max(3, min(15, parsed))
    return parsed if parsed % 2 else parsed + 1


def normalize_gray(rgb: np.ndarray, *, contrast_normalization="none", contrast_clip_limit=2.0, contrast_tile_size=8, contrast_gamma=1.0) -> np.ndarray:
    """Apply a parameterized, capture-agnostic luminance normalization."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if contrast_normalization == "clahe":
        return cv2.createCLAHE(
            clipLimit=contrast_clip_limit,
            tileGridSize=(contrast_tile_size, contrast_tile_size),
        ).apply(gray)
    if contrast_normalization == "stretch":
        return cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    if contrast_normalization == "gamma":
        lut = np.array(
            [((index / 255.0) ** contrast_gamma) * 255.0 for index in range(256)],
            dtype=np.float32,
        ).clip(0.0, 255.0).astype(np.uint8)
        return cv2.LUT(gray, lut)
    return gray
