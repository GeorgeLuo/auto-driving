from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import requests
from PIL import Image, ImageDraw

from autonomy.perception import (
    PerceivedThing,
    PerceptionDiagnosticSink,
    PerceptionEvidenceBatch,
    PerceptionPluginContract,
    PerceptionPluginInputs,
    PerceptionPluginWarmingUp,
    PerceptionSignal,
    ViewLocation,
)
from implementations.perception.components import CameraFrame, FRONT_CAMERA_RGB_INPUT
from implementations.perception.motion.tracks import MotionTracksPlugin
from implementations.perception.traversability.plugin import FloorPlanePlugin
from lab.plugins.perception.classical_regions.src.plugin import ClassicalRegionPlugin
from lab.plugins.perception.floor_continuity.src.plugin import FloorContinuityPlugin


@dataclass(frozen=True)
class RawBox:
    candidate_id: str
    source: str
    kind: str
    label: str
    bbox: tuple[float, float, float, float]
    confidence: float
    properties: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source": self.source,
            "kind": self.kind,
            "label": self.label,
            "bbox_xyxy_norm": list(self.bbox),
            "confidence": self.confidence,
            "properties": _json_safe(self.properties),
        }


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    kind: str
    bbox: tuple[float, float, float, float]
    source_ids: tuple[str, ...]
    source_names: tuple[str, ...]
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "kind": self.kind,
            "bbox_xyxy_norm": list(self.bbox),
            "source_ids": list(self.source_ids),
            "source_names": list(self.source_names),
            "confidence": self.confidence,
        }


class CompositeBoxFusionPlugin:
    """Run several current CV cues and ask Jev to choose bounded box hypotheses."""

    plugin_id = "composite-box-fusion-v0"
    contract = PerceptionPluginContract(
        inputs=(FRONT_CAMERA_RGB_INPUT,),
        state_mode="windowed",
        description=(
            "Preserve noisy current CV spatial cues, build bounded geometric box "
            "hypotheses, and optionally select them with Jev."
        ),
        assumptions=(
            "candidate boxes are image-space evidence rather than object truth",
            "motion tracks are local sequence evidence and may warm up, merge, or split",
            "Jev receives structured detector findings and never receives the image",
            "the selected box is useful only when the candidate set contains useful geometry",
        ),
        emits=(
            "signals cv_candidates_available and jev_boundaries_available",
            "raw detector candidate boxes",
            "Jev-selected bounded boundary boxes",
        ),
        limitations=(
            "no candidate establishes semantic object identity",
            "Jev confidence is decision confidence, not box IoU",
            "simulation-only color targets are intentionally excluded",
            "image-space boxes are not calibrated metric geometry",
        ),
        diagnostic_artifacts=(
            "cv_only",
            "jev_boundaries",
            "side_by_side",
            "findings",
            "fusion_summary",
        ),
    )

    _SUPPORTED_DETECTORS = {
        "floor_plane",
        "motion_tracks",
        "classical_regions",
        "floor_continuity",
        "edge_rectangles",
        "hsv_components",
        "canny_morph",
        "adaptive_contours",
        "quad_rectangularity",
        "corner_junctions",
        "partial_contour",
        "photometric_windows",
        "hough_fragments",
    }

    def __init__(
        self,
        *,
        detectors: list[str] | tuple[str, ...] | None = None,
        fusion_exclude_detectors: list[str] | tuple[str, ...] | None = None,
        fusion_mode: str = "both",
        working_width: int = 320,
        max_raw_boxes: int = 36,
        max_clusters: int = 8,
        max_hypotheses_per_cluster: int = 10,
        cluster_iou_threshold: float = 0.12,
        cluster_center_distance: float = 0.08,
        min_box_area_fraction: float = 0.02,
        max_box_area_fraction: float = 0.20,
        max_fusion_area_fraction: float = 0.20,
        edge_max_boxes: int = 12,
        edge_min_height_fraction: float = 0.04,
        edge_blur_kernel: int = 3,
        edge_close_kernel: int = 3,
        edge_close_iterations: int = 0,
        contour_working_width: int = 1008,
        contour_max_boxes: int = 36,
        adaptive_max_boxes: int = 28,
        corner_working_width: int = 1008,
        corner_max_boxes: int = 32,
        photometric_working_width: int = 1008,
        photometric_max_boxes: int = 24,
        hough_working_width: int = 1008,
        hough_max_boxes: int = 48,
        hsv_hue_min: int = 5,
        hsv_hue_max: int = 35,
        hsv_saturation_min: int = 50,
        hsv_value_max: int = 220,
        hsv_open_iterations: int = 1,
        hsv_close_iterations: int = 0,
        hsv_max_boxes: int = 16,
        jev_model: str = "jev-latest",
        jev_api_url: str = "https://api.typesafe.ai/v1/systemone",
        jev_timeout_s: float = 30.0,
        jev_min_confidence: float = 0.0,
        jev_min_usable: float = 0.45,
        cache_responses: bool = True,
    ) -> None:
        active_detectors = tuple(
            str(item)
            for item in (
                detectors
                or (
                    "classical_regions",
                    "edge_rectangles",
                )
            )
        )
        unknown = sorted(set(active_detectors) - self._SUPPORTED_DETECTORS)
        if unknown:
            raise ValueError(
                f"unsupported composite detector(s): {', '.join(unknown)}; "
                f"supported: {', '.join(sorted(self._SUPPORTED_DETECTORS))}"
            )
        if fusion_mode not in {"heuristic", "jev", "both"}:
            raise ValueError("fusion_mode must be heuristic, jev, or both")

        self.detectors = active_detectors
        self.fusion_exclude_detectors = tuple(
            str(item) for item in (fusion_exclude_detectors or ())
        )
        unknown_exclusions = sorted(
            set(self.fusion_exclude_detectors) - self._SUPPORTED_DETECTORS
        )
        if unknown_exclusions:
            raise ValueError(
                "unsupported fusion exclusion(s): "
                + ", ".join(unknown_exclusions)
            )
        self.fusion_mode = fusion_mode
        self.working_width = max(160, int(working_width))
        self.max_raw_boxes = max(1, int(max_raw_boxes))
        self.max_clusters = max(1, int(max_clusters))
        self.max_hypotheses_per_cluster = max(3, int(max_hypotheses_per_cluster))
        self.cluster_iou_threshold = max(0.0, min(1.0, float(cluster_iou_threshold)))
        self.cluster_center_distance = max(0.0, float(cluster_center_distance))
        self.min_box_area_fraction = max(0.0, min(1.0, float(min_box_area_fraction)))
        self.max_box_area_fraction = max(
            self.min_box_area_fraction,
            min(1.0, float(max_box_area_fraction)),
        )
        self.max_fusion_area_fraction = max(
            self.min_box_area_fraction,
            min(1.0, float(max_fusion_area_fraction)),
        )
        self.edge_max_boxes = max(1, int(edge_max_boxes))
        self.edge_min_height_fraction = max(
            0.0,
            min(1.0, float(edge_min_height_fraction)),
        )
        self.edge_blur_kernel = max(3, int(edge_blur_kernel) | 1)
        self.edge_close_kernel = max(3, int(edge_close_kernel))
        self.edge_close_iterations = max(0, int(edge_close_iterations))
        self.contour_working_width = max(320, int(contour_working_width))
        self.contour_max_boxes = max(1, int(contour_max_boxes))
        self.adaptive_max_boxes = max(1, int(adaptive_max_boxes))
        self.corner_working_width = max(320, int(corner_working_width))
        self.corner_max_boxes = max(1, int(corner_max_boxes))
        self.photometric_working_width = max(320, int(photometric_working_width))
        self.photometric_max_boxes = max(1, int(photometric_max_boxes))
        self.hough_working_width = max(320, int(hough_working_width))
        self.hough_max_boxes = max(1, int(hough_max_boxes))
        self.hsv_hue_min = max(0, min(179, int(hsv_hue_min)))
        self.hsv_hue_max = max(self.hsv_hue_min, min(179, int(hsv_hue_max)))
        self.hsv_saturation_min = max(0, min(255, int(hsv_saturation_min)))
        self.hsv_value_max = max(0, min(255, int(hsv_value_max)))
        self.hsv_open_iterations = max(0, int(hsv_open_iterations))
        self.hsv_close_iterations = max(0, int(hsv_close_iterations))
        self.hsv_max_boxes = max(1, int(hsv_max_boxes))
        self.jev_model = str(jev_model)
        self.jev_api_url = str(jev_api_url)
        self.jev_timeout_s = max(1.0, float(jev_timeout_s))
        self.jev_min_confidence = max(0.0, min(1.0, float(jev_min_confidence)))
        self.jev_min_usable = max(0.0, min(1.0, float(jev_min_usable)))
        self.cache_responses = bool(cache_responses)
        self._children = {
            "floor_plane": FloorPlanePlugin(),
            "motion_tracks": MotionTracksPlugin(),
            "classical_regions": ClassicalRegionPlugin(working_width=self.working_width),
            "floor_continuity": FloorContinuityPlugin(),
        }

    def reset(self) -> None:
        for child in self._children.values():
            reset = getattr(child, "reset", None)
            if callable(reset):
                reset()

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        frame = inputs.require("frame", CameraFrame)
        child_inputs = _child_inputs(inputs, frame)
        raw_boxes: list[RawBox] = []
        detector_runs: list[dict[str, Any]] = []

        for detector_name in self.detectors:
            started = time.perf_counter()
            try:
                if detector_name == "edge_rectangles":
                    things = _edge_rectangles(
                        frame.rgb,
                        working_width=self.working_width,
                        max_boxes=self.edge_max_boxes,
                        min_area_fraction=self.min_box_area_fraction,
                        max_area_fraction=self.max_box_area_fraction,
                        blur_kernel=self.edge_blur_kernel,
                        close_kernel=self.edge_close_kernel,
                        close_iterations=self.edge_close_iterations,
                    )
                    status = "ok" if things else "empty"
                elif detector_name == "canny_morph":
                    things = _canny_morph(
                        frame.rgb,
                        working_width=self.contour_working_width,
                        max_boxes=self.contour_max_boxes,
                    )
                    status = "ok" if things else "empty"
                elif detector_name == "adaptive_contours":
                    things = _adaptive_contours(
                        frame.rgb,
                        working_width=self.contour_working_width,
                        max_boxes=self.adaptive_max_boxes,
                    )
                    status = "ok" if things else "empty"
                elif detector_name == "quad_rectangularity":
                    things = _quad_rectangularity(
                        frame.rgb,
                        working_width=self.contour_working_width,
                        max_boxes=self.contour_max_boxes,
                    )
                    status = "ok" if things else "empty"
                elif detector_name == "corner_junctions":
                    things = _corner_junctions(
                        frame.rgb,
                        working_width=self.corner_working_width,
                        max_boxes=self.corner_max_boxes,
                    )
                    status = "ok" if things else "empty"
                elif detector_name == "partial_contour":
                    things = _partial_contour(
                        frame.rgb,
                        working_width=self.contour_working_width,
                        max_boxes=self.contour_max_boxes,
                    )
                    status = "ok" if things else "empty"
                elif detector_name == "photometric_windows":
                    things = _photometric_windows(
                        frame.rgb,
                        working_width=self.photometric_working_width,
                        max_boxes=self.photometric_max_boxes,
                    )
                    status = "ok" if things else "empty"
                elif detector_name == "hough_fragments":
                    things = _hough_fragments(
                        frame.rgb,
                        working_width=self.hough_working_width,
                        max_boxes=self.hough_max_boxes,
                    )
                    status = "ok" if things else "empty"
                elif detector_name == "hsv_components":
                    things = _hsv_components(
                        frame.rgb,
                        working_width=self.working_width,
                        max_boxes=self.hsv_max_boxes,
                        min_area_fraction=self.min_box_area_fraction,
                        max_area_fraction=self.max_box_area_fraction,
                        hue_min=self.hsv_hue_min,
                        hue_max=self.hsv_hue_max,
                        saturation_min=self.hsv_saturation_min,
                        value_max=self.hsv_value_max,
                        open_iterations=self.hsv_open_iterations,
                        close_iterations=self.hsv_close_iterations,
                    )
                    status = "ok" if things else "empty"
                else:
                    batch = self._children[detector_name].perceive(child_inputs)
                    things = list(batch.things)
                    status = "ok" if things else "empty"
            except PerceptionPluginWarmingUp as exc:
                things = []
                status = "warming_up"
                detector_runs.append(
                    {
                        "detector": detector_name,
                        "status": status,
                        "duration_ms": _elapsed_ms(started),
                        "count": 0,
                        "detail": exc.reason,
                        "measurements": _json_safe(exc.measurements),
                    }
                )
                continue
            except Exception as exc:  # A weak detector must not hide other cues.
                things = []
                status = "error"
                detector_runs.append(
                    {
                        "detector": detector_name,
                        "status": status,
                        "duration_ms": _elapsed_ms(started),
                        "count": 0,
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue

            extracted = _extract_raw_boxes(
                detector_name,
                things,
                min_area_fraction=self.min_box_area_fraction,
                max_area_fraction=self.max_box_area_fraction,
            )
            raw_boxes.extend(extracted)
            detector_runs.append(
                {
                    "detector": detector_name,
                    "status": status,
                    "duration_ms": _elapsed_ms(started),
                    "count": len(extracted),
                }
            )

        raw_boxes = _limit_raw_boxes(raw_boxes, self.max_raw_boxes)
        fusion_boxes = [
            box
            for box in raw_boxes
            if box.source not in self.fusion_exclude_detectors
        ]
        clusters = _cluster_boxes(
            fusion_boxes,
            iou_threshold=self.cluster_iou_threshold,
            center_distance=self.cluster_center_distance,
            max_area_fraction=self.max_fusion_area_fraction,
            max_clusters=self.max_clusters,
            min_edge_height_fraction=self.edge_min_height_fraction,
        )
        hypotheses_by_cluster = {
            cluster_id: _make_hypotheses(
                cluster_id,
                boxes,
                max_hypotheses=self.max_hypotheses_per_cluster,
            )
            for cluster_id, boxes in clusters.items()
        }
        heuristic_boxes = [
            _heuristic_box(cluster_id, hypotheses)
            for cluster_id, hypotheses in hypotheses_by_cluster.items()
            if hypotheses
        ]

        jev_request: dict[str, Any] | None = None
        jev_result: dict[str, Any] = {
            "status": "disabled",
            "model": self.jev_model,
            "api_url": self.jev_api_url,
            "selected": [],
        }
        if self.fusion_mode in {"jev", "both"} and hypotheses_by_cluster:
            jev_request = _build_jev_request(
                inputs.frame_id,
                frame,
                detector_runs,
                fusion_boxes,
                hypotheses_by_cluster,
                self.jev_model,
            )
            jev_result = self._run_jev(
                jev_request,
                allow_cache_write=inputs.diagnostics.enabled,
            )
            jev_result["selected"] = _select_jev_hypotheses(
                jev_result.get("response"),
                hypotheses_by_cluster,
                min_confidence=self.jev_min_confidence,
                min_usable=self.jev_min_usable,
            )

        selected_boxes = [
            item["hypothesis"]
            for item in jev_result.get("selected", [])
            if item.get("hypothesis") is not None
        ]
        raw_things = tuple(_raw_thing(box, _cluster_id_for_box(box, clusters)) for box in raw_boxes)
        fused_things = tuple(
            _jev_thing(item, index=index)
            for index, item in enumerate(jev_result.get("selected", []))
            if item.get("hypothesis") is not None
        )
        things = raw_things + fused_things

        findings = {
            "schema": "issue_219_composite_box_fusion_v0",
            "frame_id": inputs.frame_id,
            "captured_at_ms": inputs.captured_at_ms,
            "image": {
                "width_px": frame.width_px,
                "height_px": frame.height_px,
                "source_path": str(frame.source_path) if frame.source_path is not None else None,
            },
            "detectors": self.detectors,
            "fusion_exclude_detectors": self.fusion_exclude_detectors,
            "detector_runs": detector_runs,
            "raw_boxes": [box.to_dict() for box in raw_boxes],
            "fusion_box_count": len(fusion_boxes),
            "clusters": {
                cluster_id: [box.to_dict() for box in boxes]
                for cluster_id, boxes in clusters.items()
            },
            "hypotheses": {
                cluster_id: [hypothesis.to_dict() for hypothesis in hypotheses]
                for cluster_id, hypotheses in hypotheses_by_cluster.items()
            },
            "heuristic_boxes": [box.to_dict() for box in heuristic_boxes],
            "jev_request": _json_safe(jev_request),
            "jev": _json_safe(jev_result),
        }

        if inputs.diagnostics.enabled:
            inputs.diagnostics.emit(
                "cv_only",
                "cv_only.png",
                lambda path: _render_cv_overlay(frame.rgb, raw_boxes, path),
            )
            inputs.diagnostics.emit(
                "jev_boundaries",
                "jev_boundaries.png",
                lambda path: _render_jev_overlay(frame.rgb, jev_result, path),
            )
            inputs.diagnostics.emit(
                "side_by_side",
                "side_by_side.png",
                lambda path: _render_side_by_side(frame.rgb, raw_boxes, jev_result, path),
            )
            inputs.diagnostics.emit_json("findings", "findings.json", findings)
            inputs.diagnostics.emit_json(
                "fusion_summary",
                "fusion_summary.json",
                {
                    "frame_id": inputs.frame_id,
                    "raw_box_count": len(raw_boxes),
                    "cluster_count": len(clusters),
                    "heuristic_box_count": len(heuristic_boxes),
                    "jev_box_count": len(selected_boxes),
                    "jev_status": jev_result.get("status"),
                    "jev_latency_ms": jev_result.get("latency_ms"),
                },
            )

        measurements = {
            "detectors": list(self.detectors),
            "fusion_exclude_detectors": list(self.fusion_exclude_detectors),
            "detector_runs": detector_runs,
            "raw_boxes": [box.to_dict() for box in raw_boxes],
            "fusion_box_count": len(fusion_boxes),
            "clusters": {
                cluster_id: [box.to_dict() for box in boxes]
                for cluster_id, boxes in clusters.items()
            },
            "hypotheses": {
                cluster_id: [hypothesis.to_dict() for hypothesis in hypotheses]
                for cluster_id, hypotheses in hypotheses_by_cluster.items()
            },
            "heuristic_boxes": [box.to_dict() for box in heuristic_boxes],
            "jev": _json_safe(jev_result),
        }
        return PerceptionEvidenceBatch(
            signals=(
                PerceptionSignal(
                    "cv_candidates_available",
                    bool(raw_boxes),
                    _mean_confidence(raw_boxes),
                    {
                        "raw_box_count": len(raw_boxes),
                        "cluster_count": len(clusters),
                        "detectors": list(self.detectors),
                    },
                ),
                PerceptionSignal(
                    "jev_boundaries_available",
                    bool(selected_boxes),
                    _mean_confidence(selected_boxes),
                    {
                        "status": jev_result.get("status"),
                        "selected_count": len(selected_boxes),
                    },
                ),
            ),
            things=things,
            measurements=measurements,
        )

    def _run_jev(
        self,
        request_body: dict[str, Any],
        *,
        allow_cache_write: bool,
    ) -> dict[str, Any]:
        request_digest = hashlib.sha256(
            _canonical_json(request_body).encode("utf-8")
        ).hexdigest()
        cache_path = self._cache_path(request_digest)
        if cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                return {
                    "status": "cached",
                    "model": self.jev_model,
                    "request_sha256": request_digest,
                    "cache_path": str(cache_path),
                    "response": cached.get("response"),
                    "latency_ms": cached.get("latency_ms"),
                    "attempts": cached.get("attempts", 0),
                }
            except (OSError, ValueError, TypeError):
                pass

        api_key = os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
        if not api_key:
            return {
                "status": "missing_api_key",
                "model": self.jev_model,
                "request_sha256": request_digest,
                "cache_path": str(cache_path),
                "response": None,
                "latency_ms": 0.0,
                "attempts": 0,
            }

        started = time.perf_counter()
        last_error: str | None = None
        attempts = 0
        for attempt in range(1, 4):
            attempts = attempt
            try:
                response = requests.post(
                    self.jev_api_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=request_body,
                    timeout=self.jev_timeout_s,
                )
                if response.status_code in {429, 529} and attempt < 3:
                    time.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                response.raise_for_status()
                payload = response.json()
                latency_ms = _elapsed_ms(started)
                result = {
                    "status": "live",
                    "model": self.jev_model,
                    "request_sha256": request_digest,
                    "cache_path": str(cache_path),
                    "response": payload,
                    "latency_ms": latency_ms,
                    "attempts": attempts,
                }
                if self.cache_responses and allow_cache_write:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(
                        json.dumps(
                            {
                                "schema": "issue_219_jev_cache_v0",
                                "request": request_body,
                                "response": payload,
                                "latency_ms": latency_ms,
                                "attempts": attempts,
                                "created_at_ms": int(time.time() * 1000),
                            },
                            indent=2,
                            sort_keys=True,
                        ),
                        encoding="utf-8",
                    )
                return result
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < 3:
                    time.sleep(0.5 * (2 ** (attempt - 1)))
            except (ValueError, TypeError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                break

        return {
            "status": "error",
            "model": self.jev_model,
            "request_sha256": request_digest,
            "cache_path": str(cache_path),
            "response": None,
            "latency_ms": _elapsed_ms(started),
            "attempts": attempts,
            "error": last_error or "unknown Jev failure",
        }

    def _cache_path(self, request_digest: str) -> Path:
        root = Path(__file__).resolve().parents[1]
        return root / "cache" / f"{request_digest}.json"


def _child_inputs(inputs: PerceptionPluginInputs, frame: CameraFrame) -> PerceptionPluginInputs:
    return PerceptionPluginInputs(
        frame_id=inputs.frame_id,
        captured_at_ms=inputs.captured_at_ms,
        components={"frame": frame},
        diagnostics=PerceptionDiagnosticSink(
            output_dir=None,
            plugin_id="composite-child",
            allowed_artifacts=(),
        ),
        metadata=inputs.metadata,
    )


def _extract_raw_boxes(
    source: str,
    things: list[PerceivedThing],
    *,
    min_area_fraction: float,
    max_area_fraction: float,
) -> list[RawBox]:
    raw: list[RawBox] = []
    for index, thing in enumerate(things):
        bbox = thing.location.bbox_xyxy_norm
        if bbox is None or thing.kind in {"surface", "sensor_frame"}:
            continue
        normalized = _normalize_bbox(bbox)
        if normalized is None:
            continue
        area = _bbox_area(normalized)
        if area < min_area_fraction or area > max_area_fraction:
            continue
        raw.append(
            RawBox(
                candidate_id=f"{source}_{index:03d}",
                source=source,
                kind=thing.kind,
                label=thing.label,
                bbox=normalized,
                confidence=float(thing.confidence),
                properties={
                    "thing_id": thing.thing_id,
                    **_json_safe(thing.properties),
                },
            )
        )
    return raw


def _limit_raw_boxes(boxes: list[RawBox], limit: int) -> list[RawBox]:
    ordered = sorted(
        boxes,
        key=lambda item: (item.confidence, _bbox_area(item.bbox)),
        reverse=True,
    )
    if len(ordered) <= limit:
        return ordered
    sources = sorted({item.source for item in ordered})
    if len(sources) <= 1:
        return ordered[:limit]
    quota = max(1, limit // len(sources))
    kept: list[RawBox] = []
    kept_ids: set[str] = set()
    for source in sources:
        for item in (candidate for candidate in ordered if candidate.source == source):
            if sum(1 for existing in kept if existing.source == source) >= quota:
                break
            kept.append(item)
            kept_ids.add(item.candidate_id)
    if len(kept) < limit:
        kept.extend(item for item in ordered if item.candidate_id not in kept_ids)
    return kept[:limit]


def _cluster_boxes(
    boxes: list[RawBox],
    *,
    iou_threshold: float,
    center_distance: float,
    max_area_fraction: float,
    max_clusters: int,
    min_edge_height_fraction: float,
) -> dict[str, list[RawBox]]:
    # Keep broad proposals in the raw CV overlay, but do not let them bridge
    # otherwise separate object hypotheses.
    eligible = [
        box for box in boxes
        if _fusion_eligible(
            box,
            max_area_fraction=max_area_fraction,
            min_edge_height_fraction=min_edge_height_fraction,
        )
    ]
    if not eligible:
        return {}
    parent = list(range(len(eligible)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left in range(len(eligible)):
        for right in range(left + 1, len(eligible)):
            overlap = _bbox_iou(eligible[left].bbox, eligible[right].bbox)
            distance = _center_distance(eligible[left].bbox, eligible[right].bbox)
            if overlap >= iou_threshold or distance <= center_distance:
                union(left, right)

    grouped: dict[int, list[RawBox]] = {}
    for index, box in enumerate(eligible):
        grouped.setdefault(find(index), []).append(box)
    ordered = sorted(
        grouped.values(),
        key=lambda group: (
            max(item.confidence for item in group),
            len(group),
        ),
        reverse=True,
    )
    return {
        f"cluster_{index:02d}": sorted(
            group,
            key=lambda item: (item.confidence, _bbox_area(item.bbox)),
            reverse=True,
        )
        for index, group in enumerate(ordered[:max_clusters])
    }


def _fusion_eligible(
    box: RawBox,
    *,
    max_area_fraction: float,
    min_edge_height_fraction: float = 0.08,
) -> bool:
    """Keep broad cues for audit, but stop them bridging object hypotheses."""
    area = _bbox_area(box.bbox)
    if area > max_area_fraction:
        return False
    x1, y1, x2, y2 = box.bbox
    width = x2 - x1
    height = y2 - y1
    # Floor detectors describe interruptions, not object extents. Preserve
    # them in raw evidence, but do not offer them as Jev boundary choices.
    if box.kind == "floor_boundary":
        return False
    # Tiny regions pinned to the upper border are usually wall/ceiling noise.
    if y2 <= 0.18 or (y1 <= 0.01 and height < 0.50):
        return False
    touches_border = sum(
        value
        for value in (
            x1 <= 0.01,
            y1 <= 0.01,
            x2 >= 0.99,
            y2 >= 0.99,
        )
        if value
    )
    if touches_border >= 2 and area > 0.08:
        return False
    # Small side-border fragments are commonly scene edges. Keep a substantial
    # cropped obstacle when it is large enough to be meaningful.
    if box.source == "classical_regions" and touches_border and area > 0.10:
        return False
    if box.source == "classical_regions" and touches_border and width < 0.25:
        return False
    # Generic scene-edge guards for weak fragment cues. These do not encode an
    # object location; they keep small border, ceiling/door-top, and carpet
    # interruptions from becoming multi-source boundary clusters.
    if x1 <= 0.01 and width <= 0.22 and height <= 0.40:
        return False
    if y1 >= 0.72 and height <= 0.22:
        return False
    if y2 <= 0.34 and height <= 0.32 and x2 <= 0.55:
        return False
    if box.source == "edge_rectangles" and (
        height < min_edge_height_fraction or width / max(height, 1e-6) > 5.0
    ):
        return False
    if box.source == "edge_rectangles" and touches_border and area > 0.20:
        return False
    return True


def _make_hypotheses(
    cluster_id: str,
    boxes: list[RawBox],
    *,
    max_hypotheses: int,
) -> list[Hypothesis]:
    hypotheses: list[Hypothesis] = []
    source_ids = tuple(box.candidate_id for box in boxes)
    source_names = tuple(sorted({box.source for box in boxes}))
    multi_source = len(source_names) >= 2
    # For a multi-source cluster Jev should decide among aggregate shapes;
    # raw alternatives stay in the audit overlay and the compact request state
    # but should not distract the boundary decision with one detector's patch.
    if not multi_source:
        for box in boxes[: max(1, max_hypotheses - 4)]:
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=f"{cluster_id}_{box.candidate_id}",
                    kind="raw",
                    bbox=box.bbox,
                    source_ids=(box.candidate_id,),
                    source_names=(box.source,),
                    confidence=box.confidence,
                )
            )
    # Same-detector fragments are correlated. Only synthesize an aggregate
    # when independent cues agree; raw evidence remains available for audit.
    if len(boxes) >= 2 and multi_source:
        hypotheses.extend(
            [
                Hypothesis(
                    hypothesis_id=f"{cluster_id}_robust_extent",
                    kind="robust_extent",
                    bbox=_robust_extent_bbox(boxes),
                    source_ids=source_ids,
                    source_names=source_names,
                    confidence=_support_confidence(boxes),
                ),
                Hypothesis(
                    hypothesis_id=f"{cluster_id}_covering_union",
                    kind="covering_union",
                    bbox=_union_bbox(boxes),
                    source_ids=source_ids,
                    source_names=source_names,
                    confidence=_support_confidence(boxes),
                ),
                Hypothesis(
                    hypothesis_id=f"{cluster_id}_weighted_median",
                    kind="weighted_median",
                    bbox=_weighted_median_bbox(boxes),
                    source_ids=source_ids,
                    source_names=source_names,
                    confidence=_support_confidence(boxes),
                ),
                Hypothesis(
                    hypothesis_id=f"{cluster_id}_union",
                    kind="union",
                    bbox=_union_bbox(boxes),
                    source_ids=source_ids,
                    source_names=source_names,
                    confidence=_support_confidence(boxes),
                ),
            ]
        )
        intersection = _intersection_bbox(boxes)
        if intersection is not None and _bbox_area(intersection) >= 0.01:
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=f"{cluster_id}_intersection",
                    kind="intersection",
                    bbox=intersection,
                    source_ids=source_ids,
                    source_names=source_names,
                    confidence=_support_confidence(boxes),
                )
            )
    hypotheses.append(
        Hypothesis(
            hypothesis_id=f"{cluster_id}_none",
            kind="none",
            bbox=(0.0, 0.0, 0.0, 0.0),
            source_ids=(),
            source_names=(),
            confidence=0.0,
        )
    )
    return _dedupe_hypotheses(hypotheses)[:max_hypotheses]


def _dedupe_hypotheses(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
    seen: set[tuple[float, ...]] = set()
    result: list[Hypothesis] = []
    positions: dict[tuple[float, ...], int] = {}
    for hypothesis in hypotheses:
        key = tuple(round(value, 4) for value in hypothesis.bbox)
        if key in seen and hypothesis.kind != "none":
            existing_index = positions.get(key)
            if existing_index is not None and _hypothesis_priority(hypothesis) > _hypothesis_priority(
                result[existing_index]
            ):
                result[existing_index] = hypothesis
            continue
        seen.add(key)
        positions[key] = len(result)
        result.append(hypothesis)
    return result


def _hypothesis_priority(hypothesis: Hypothesis) -> int:
    return {
        "raw": 1,
        "intersection": 2,
        "robust_extent": 4,
        "covering_union": 3,
        "union": 3,
        "weighted_median": 4,
        "none": 0,
    }.get(hypothesis.kind, 0)


def _heuristic_box(cluster_id: str, hypotheses: list[Hypothesis]) -> Hypothesis:
    preferred_extent = [item for item in hypotheses if item.kind == "robust_extent"]
    if preferred_extent:
        return preferred_extent[0]
    preferred = [item for item in hypotheses if item.kind == "weighted_median"]
    if preferred:
        return preferred[0]
    return next(item for item in hypotheses if item.kind != "none")


def _jev_hypothesis_description(hypothesis: Hypothesis) -> str:
    descriptions = {
        "raw": "single raw detector box",
        "robust_extent": "10th-to-90th-percentile outer extent with small padding",
        "weighted_median": "coordinate-median box that may shrink complementary fragments",
        "union": "outer extent of all cluster fragments",
        "covering_union": "outer boundary extent covered by the cluster fragments",
        "intersection": "common overlap of cluster fragments",
        "none": "no boundary",
    }
    return f"{descriptions.get(hypothesis.kind, hypothesis.kind)} ({hypothesis.kind})"


def _build_jev_request(
    frame_id: str,
    frame: CameraFrame,
    detector_runs: list[dict[str, Any]],
    raw_boxes: list[RawBox],
    hypotheses_by_cluster: dict[str, list[Hypothesis]],
    model: str,
) -> dict[str, Any]:
    clusters = []
    for cluster_id, hypotheses in hypotheses_by_cluster.items():
        criteria = {
            hypothesis.hypothesis_id: (
                f"{_jev_hypothesis_description(hypothesis)} from "
                f"{', '.join(hypothesis.source_names) or 'no detector'}; "
                f"normalized bounds={list(hypothesis.bbox)}; "
                f"support={list(hypothesis.source_ids) or 'none'}"
            )
            for hypothesis in hypotheses
        }
        clusters.append(
            {
                "cluster_id": cluster_id,
                "hypotheses": [hypothesis.to_dict() for hypothesis in hypotheses],
                "question_criteria": criteria,
            }
        )
    questions: dict[str, Any] = {}
    for cluster in clusters:
        cluster_id = cluster["cluster_id"]
        questions[f"{cluster_id}_selected"] = {
            "type": "choice",
            "instructions": (
                "Which bounded hypothesis best encloses one distinct visible object or obstacle "
                "supported by the structured CV evidence? Choose none when the cluster is clutter, "
                "a floor-only interruption, or insufficiently object-like. Select a fused box only "
                "when independent detector cues agree on one compact spatial mode. Boxes from one "
                "detector are correlated and count as one cue. Do not use a large floor, wall, "
                "image-border, or edge region as object support. Do not combine genuinely separated "
                "object-sized modes with a union. However, partial detector boxes can be complementary: "
                "when a compact multi-source cluster places fragments on different sides or faces of "
                "one object, its covering_union is the intended outer boundary even when no individual fragment "
                "covers the whole object. Use weighted_median only when the members are alternative "
                "noisy proposals for the same already-complete boundary; a median can incorrectly "
                "shrink a tiled set of partial fragments. When a fragment cluster has a few spatial "
                "outliers, robust_extent (the 10th-to-90th "
                "coordinate envelope with small padding) is preferred over both an untrimmed union "
                "and a shrinking median, provided it remains compact and has multi-detector support. "
                "A single compact, high-confidence "
                "raw proposal may be usable when it is isolated and not contradicted by broad cues. "
                "A compact closed-edge rectangle with plausible image geometry may remain usable "
                "even when its edge confidence is lower than a color-region confidence. Prefer a "
                "multi-detector robust_extent or covering_union when its extent is compact and its member "
                "fragments are spatially coherent; do not let one high-confidence fragment beat that "
                "object-sized outer extent."
            ),
            "criteria": cluster["question_criteria"],
        }
        questions[f"{cluster_id}_usable"] = {
            "type": "noul",
            "instructions": (
                "Does this structured cluster support a usable boundary for one distinct object "
                "rather than unrelated image regions?"
            ),
            "criteria": {
                "true": "Several independent cues agree on one compact distinct object or obstacle, or one isolated compact raw proposal with plausible geometry is usable.",
                "false": "The cues are correlated, spatially heterogeneous, cluttered, or describe only a surface interruption.",
            },
        }
    return {
        "model": model,
        "state": {
            "task": "Fuse noisy image-space CV cues into bounded object-boundary hypotheses.",
            "image_is_not_available_to_jev": True,
            "frame": {
                "id": frame_id,
                "sensor_id": frame.sensor_id,
                "width_px": frame.width_px,
                "height_px": frame.height_px,
            },
            "detector_runs": _json_safe(detector_runs),
            # The full detector properties remain in findings.json for audit,
            # but they are redundant in Jev's request: the cluster hypotheses
            # already carry the spatial evidence and source identities. Keep
            # the request compact enough for a larger, source-balanced packet.
            "raw_boxes": [_jev_raw_box(box) for box in raw_boxes],
            "clusters": clusters,
        },
        "questions": questions,
    }


def _jev_raw_box(box: RawBox) -> dict[str, Any]:
    return {
        "candidate_id": box.candidate_id,
        "source": box.source,
        "kind": box.kind,
        "bbox_xyxy_norm": list(box.bbox),
        "confidence": box.confidence,
    }


def _select_jev_hypotheses(
    response: Any,
    hypotheses_by_cluster: dict[str, list[Hypothesis]],
    *,
    min_confidence: float,
    min_usable: float,
) -> list[dict[str, Any]]:
    if not isinstance(response, dict):
        return []
    answers = response.get("answers")
    if not isinstance(answers, dict):
        return []
    selected: list[dict[str, Any]] = []
    for cluster_id, hypotheses in hypotheses_by_cluster.items():
        answer = answers.get(f"{cluster_id}_selected")
        usable_answer = answers.get(f"{cluster_id}_usable")
        if not isinstance(answer, dict):
            continue
        choice = answer.get("choice")
        probabilities = answer.get("probabilities")
        if not isinstance(choice, str):
            continue
        hypothesis = next(
            (item for item in hypotheses if item.hypothesis_id == choice),
            None,
        )
        confidence = _answer_confidence(answer)
        usable = _noul_value(usable_answer)
        selected.append(
            {
                "cluster_id": cluster_id,
                "choice": choice,
                "confidence": confidence,
                "probability": (
                    float(probabilities.get(choice))
                    if isinstance(probabilities, dict) and choice in probabilities
                    else None
                ),
                "usable": usable,
                "hypothesis": (
                    hypothesis.to_dict()
                    if hypothesis is not None and hypothesis.kind != "none"
                    and confidence >= min_confidence
                    and (usable is None or usable >= min_usable)
                    else None
                ),
            }
        )
    return selected


def _answer_confidence(answer: dict[str, Any]) -> float:
    try:
        if answer.get("confidence") is not None:
            return max(0.0, min(1.0, float(answer["confidence"])))
        probabilities = answer.get("probabilities")
        if isinstance(probabilities, dict) and probabilities:
            return max(float(value) for value in probabilities.values())
    except (TypeError, ValueError):
        pass
    return 0.0


def _noul_value(answer: Any) -> float | None:
    if not isinstance(answer, dict):
        return None
    try:
        value = answer.get("noul")
        return None if value is None else max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _raw_thing(box: RawBox, cluster_id: str | None) -> PerceivedThing:
    return PerceivedThing(
        thing_id=f"cv_{box.candidate_id}",
        kind="cv_candidate_box",
        label=f"{box.source}: {box.label}",
        location=ViewLocation(
            frame="image",
            zone=_zone_from_bbox(box.bbox),
            bbox_xyxy_norm=box.bbox,
        ),
        confidence=box.confidence,
        properties={
            "role": "raw_cv",
            "candidate_id": box.candidate_id,
            "source": box.source,
            "detector_kind": box.kind,
            "cluster_id": cluster_id,
            **box.properties,
        },
    )


def _jev_thing(item: dict[str, Any], *, index: int) -> PerceivedThing:
    hypothesis = item["hypothesis"]
    bbox = tuple(float(value) for value in hypothesis["bbox_xyxy_norm"])
    confidence = float(item.get("confidence") or 0.0)
    return PerceivedThing(
        thing_id=f"jev_boundary_{index:03d}",
        kind="jev_boundary_box",
        label="Jev selected boundary",
        location=ViewLocation(
            frame="image",
            zone=_zone_from_bbox(bbox),
            bbox_xyxy_norm=bbox,
        ),
        confidence=confidence,
        properties={
            "role": "jev_fused",
            "cluster_id": item.get("cluster_id"),
            "choice": item.get("choice"),
            "probability": item.get("probability"),
            "usable": item.get("usable"),
            "hypothesis": hypothesis,
        },
    )


def _cluster_id_for_box(box: RawBox, clusters: dict[str, list[RawBox]]) -> str | None:
    for cluster_id, members in clusters.items():
        if any(member.candidate_id == box.candidate_id for member in members):
            return cluster_id
    return None


def _edge_rectangles(
    rgb: np.ndarray,
    *,
    working_width: int,
    max_boxes: int,
    min_area_fraction: float,
    max_area_fraction: float,
    blur_kernel: int = 3,
    close_kernel: int = 3,
    close_iterations: int = 0,
) -> list[PerceivedThing]:
    source_height, source_width = rgb.shape[:2]
    scale = min(1.0, working_width / max(source_width, 1))
    width = max(1, int(round(source_width * scale)))
    height = max(1, int(round(source_height * scale)))
    working = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA) if scale < 1 else rgb
    gray = cv2.cvtColor(working, cv2.COLOR_RGB2GRAY)
    blur_kernel = max(3, int(blur_kernel) | 1)
    close_kernel = max(3, int(close_kernel))
    gray = cv2.GaussianBlur(gray, (blur_kernel, blur_kernel), 0)
    median = float(np.median(gray))
    low = int(max(12.0, 0.66 * median))
    high = int(min(255.0, max(low + 12.0, 1.33 * median)))
    edges = cv2.Canny(gray, low, high)
    kernel = np.ones((close_kernel, close_kernel), dtype=np.uint8)
    closed = cv2.morphologyEx(
        edges,
        cv2.MORPH_CLOSE,
        kernel,
        iterations=max(0, int(close_iterations)),
    )
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[float, PerceivedThing]] = []
    image_area = max(1, width * height)
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        area = box_width * box_height / image_area
        if area < min_area_fraction or area > max_area_fraction:
            continue
        if box_width < 5 or box_height < 5:
            continue
        contour_area = max(float(cv2.contourArea(contour)), 1.0)
        fill = min(1.0, contour_area / max(float(box_width * box_height), 1.0))
        edge_pixels = float(np.count_nonzero(closed[y : y + box_height, x : x + box_width]))
        edge_density = min(1.0, edge_pixels / max(float(box_width * box_height) * 0.25, 1.0))
        # Sparse closed contours have low fill by construction. Give their
        # compact, interior geometry a modest floor so Jev can weigh the cue
        # without treating it as equivalent to a color-region confidence.
        confidence = round(float(0.35 + 0.55 * edge_density + 0.10 * fill), 5)
        bbox = (
            round(x / max(width - 1, 1), 5),
            round(y / max(height - 1, 1), 5),
            round((x + box_width - 1) / max(width - 1, 1), 5),
            round((y + box_height - 1) / max(height - 1, 1), 5),
        )
        thing = PerceivedThing(
            thing_id=f"edge_rectangle_{len(candidates):03d}",
            kind="edge_region",
            label="closed-edge rectangle",
            location=ViewLocation(
                frame="image",
                zone=_zone_from_bbox(bbox),
                bbox_xyxy_norm=bbox,
            ),
            confidence=confidence,
            properties={
                "evidence": "canny_closed_contour",
                "area_fraction": round(float(area), 6),
                "fill": round(float(fill), 5),
                "edge_density": round(float(edge_density), 5),
                "blur_kernel": blur_kernel,
                "close_kernel": close_kernel,
                "close_iterations": max(0, int(close_iterations)),
                "working_width": width,
                "working_height": height,
            },
        )
        candidates.append((confidence, thing))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [thing for _, thing in candidates[:max_boxes]]


def _working_rgb(rgb: np.ndarray, working_width: int) -> np.ndarray:
    source_height, source_width = rgb.shape[:2]
    scale = min(1.0, working_width / max(source_width, 1))
    if scale >= 1.0:
        return rgb
    return cv2.resize(
        rgb,
        (max(1, int(round(source_width * scale))), max(1, int(round(source_height * scale)))),
        interpolation=cv2.INTER_AREA,
    )


def _gray_variants(rgb: np.ndarray) -> dict[str, np.ndarray]:
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    return {
        "gray": gray,
        "lab_l": lab[..., 0],
        "hsv_v": hsv[..., 2],
        "clahe": clahe,
    }


def _cv_box_thing(
    source: str,
    index: int,
    bbox: tuple[float, float, float, float],
    confidence: float,
    *,
    kind: str,
    label: str,
    properties: dict[str, Any],
) -> PerceivedThing:
    return PerceivedThing(
        thing_id=f"{source}_candidate_{index:03d}",
        kind=kind,
        label=label,
        location=ViewLocation(
            frame="image",
            zone=_zone_from_bbox(bbox),
            bbox_xyxy_norm=bbox,
        ),
        confidence=max(0.0, min(1.0, float(confidence))),
        properties={
            "evidence": source,
            "area_fraction": round(_bbox_area(bbox), 6),
            "source": source,
            **properties,
        },
    )


def _normalized_xywh(
    x: int,
    y: int,
    box_width: int,
    box_height: int,
    width: int,
    height: int,
) -> tuple[float, float, float, float]:
    values = (
        x / max(width - 1, 1),
        y / max(height - 1, 1),
        (x + box_width - 1) / max(width - 1, 1),
        (y + box_height - 1) / max(height - 1, 1),
    )
    return tuple(round(max(0.0, min(1.0, value)), 5) for value in values)


def _dedupe_cv_things(
    things: list[PerceivedThing],
    *,
    threshold: float,
    max_boxes: int,
) -> list[PerceivedThing]:
    ordered = sorted(things, key=lambda item: item.confidence, reverse=True)
    result: list[PerceivedThing] = []
    for thing in ordered:
        bbox = thing.location.bbox_xyxy_norm
        if bbox is None:
            continue
        if any(
            _bbox_iou(bbox, existing.location.bbox_xyxy_norm or (0.0, 0.0, 0.0, 0.0)) >= threshold
            for existing in result
        ):
            continue
        result.append(thing)
        if len(result) >= max_boxes:
            break
    return result


def _contour_candidate(
    source: str,
    index: int,
    contour: np.ndarray,
    mask: np.ndarray,
    *,
    params: dict[str, Any],
    min_area_fraction: float,
    max_area_fraction: float,
    min_width: int,
    min_height: int,
) -> PerceivedThing | None:
    height, width = mask.shape[:2]
    x, y, box_width, box_height = cv2.boundingRect(contour)
    area_fraction = box_width * box_height / max(float(width * height), 1.0)
    if (
        area_fraction < min_area_fraction
        or area_fraction > max_area_fraction
        or box_width < min_width
        or box_height < min_height
    ):
        return None
    contour_area = max(float(cv2.contourArea(contour)), 1.0)
    fill = min(1.0, contour_area / max(float(box_width * box_height), 1.0))
    perimeter = max(float(cv2.arcLength(contour, True)), 1.0)
    approx = cv2.approxPolyDP(contour, 0.025 * perimeter, True)
    edge_density = float(
        np.count_nonzero(mask[y : y + box_height, x : x + box_width])
    ) / max(float(box_width * box_height), 1.0)
    compact = 1.0 - min(
        1.0,
        abs(math.log(max(box_width / max(box_height, 1), 1e-6))) / 2.5,
    )
    confidence = (
        0.25
        + 0.30 * min(1.0, fill * 2.0)
        + 0.25 * min(1.0, edge_density * 4.0)
        + 0.20 * compact
    )
    bbox = _normalized_xywh(x, y, box_width, box_height, width, height)
    return _cv_box_thing(
        source,
        index,
        bbox,
        confidence,
        kind="contour_fragment",
        label=f"{source}: contour fragment",
        properties={
            **params,
            "vertices": len(approx),
            "fill": round(fill, 4),
            "edge_density": round(edge_density, 4),
            "working_width": width,
            "working_height": height,
        },
    )


def _canny_morph(
    rgb: np.ndarray,
    *,
    working_width: int,
    max_boxes: int,
) -> list[PerceivedThing]:
    working = _working_rgb(rgb, working_width)
    outputs: list[PerceivedThing] = []
    settings = (
        (35, 105, 5, 7, 1, 0),
        (55, 145, 3, 5, 2, 0),
        (75, 190, 5, 9, 1, 1),
    )
    for channel_name, channel in _gray_variants(working).items():
        for low, high, blur, close_kernel, close_iter, open_iter in settings:
            blurred = cv2.GaussianBlur(channel, (blur, blur), 0)
            edges = cv2.Canny(blurred, low, high, L2gradient=True)
            kernel = cv2.getStructuringElement(
                cv2.MORPH_RECT,
                (close_kernel, close_kernel),
            )
            closed = cv2.morphologyEx(
                edges,
                cv2.MORPH_CLOSE,
                kernel,
                iterations=close_iter,
            )
            if open_iter:
                closed = cv2.morphologyEx(
                    closed,
                    cv2.MORPH_OPEN,
                    np.ones((3, 3), dtype=np.uint8),
                    iterations=open_iter,
                )
            contours, _ = cv2.findContours(
                closed,
                cv2.RETR_LIST,
                cv2.CHAIN_APPROX_SIMPLE,
            )
            for contour in contours:
                thing = _contour_candidate(
                    "canny_morph",
                    len(outputs),
                    contour,
                    closed,
                    params={
                        "channel": channel_name,
                        "canny": [low, high],
                        "blur": blur,
                        "close_kernel": close_kernel,
                        "close_iter": close_iter,
                        "open_iter": open_iter,
                        "retrieval": "list",
                    },
                    min_area_fraction=0.0025,
                    max_area_fraction=0.28,
                    min_width=18,
                    min_height=18,
                )
                if thing is not None:
                    outputs.append(thing)
    return _dedupe_cv_things(outputs, threshold=0.92, max_boxes=max_boxes)


def _adaptive_contours(
    rgb: np.ndarray,
    *,
    working_width: int,
    max_boxes: int,
) -> list[PerceivedThing]:
    """Keep loose adaptive-threshold fragments as a separate geometry cue."""
    working = _working_rgb(rgb, working_width)
    lab = cv2.cvtColor(working, cv2.COLOR_RGB2LAB)
    gray = cv2.cvtColor(working, cv2.COLOR_RGB2GRAY)
    outputs: list[PerceivedThing] = []
    for channel_name, channel in (("lab_l", lab[..., 0]), ("gray", gray)):
        binary = cv2.adaptiveThreshold(
            channel,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            51,
            5,
        )
        binary = cv2.morphologyEx(
            binary,
            cv2.MORPH_CLOSE,
            np.ones((3, 3), dtype=np.uint8),
            iterations=1,
        )
        contours, _ = cv2.findContours(
            binary,
            cv2.RETR_LIST,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        for contour in contours:
            thing = _contour_candidate(
                "adaptive_contours",
                len(outputs),
                contour,
                binary,
                params={
                    "channel": channel_name,
                    "mode": "adaptive_threshold",
                    "adaptive_block": 51,
                    "adaptive_constant": 5,
                    "close_kernel": 3,
                    "close_iter": 1,
                    "retrieval": "list",
                },
                min_area_fraction=0.00035,
                max_area_fraction=0.19,
                min_width=15,
                min_height=15,
            )
            if thing is not None:
                outputs.append(thing)
    return _dedupe_cv_things(outputs, threshold=0.62, max_boxes=max_boxes)


def _corner_junctions(
    rgb: np.ndarray,
    *,
    working_width: int,
    max_boxes: int,
) -> list[PerceivedThing]:
    """Emit local corner clusters and aligned corner-pair ROIs, not rectangles."""
    working = _working_rgb(rgb, working_width)
    gray = cv2.cvtColor(working, cv2.COLOR_RGB2GRAY)
    points = cv2.goodFeaturesToTrack(
        gray,
        maxCorners=600,
        qualityLevel=0.006,
        minDistance=9,
        blockSize=5,
    )
    if points is None:
        return []
    points_xy = [tuple(float(value) for value in point[0]) for point in points]
    response = cv2.cornerMinEigenVal(gray, blockSize=5, ksize=3)
    response_max = max(float(np.max(response)), 1e-6)
    height, width = gray.shape[:2]
    outputs: list[PerceivedThing] = []

    for index, (px, py) in enumerate(points_xy):
        members = [
            member_index
            for member_index, (qx, qy) in enumerate(points_xy)
            if math.hypot(px - qx, py - qy) <= 96.0
        ]
        if len(members) < 3:
            continue
        xs = [points_xy[member_index][0] for member_index in members]
        ys = [points_xy[member_index][1] for member_index in members]
        x1 = int(math.floor(min(xs) - 20.0))
        y1 = int(math.floor(min(ys) - 20.0))
        x2 = int(math.ceil(max(xs) + 20.0))
        y2 = int(math.ceil(max(ys) + 20.0))
        box_width = x2 - x1
        box_height = y2 - y1
        density = len(members) / max(
            1.0,
            (box_width * box_height) / 10000.0,
        )
        corner_response = float(response[int(round(py)), int(round(px))]) / response_max
        score = (
            0.27
            + 0.42 * min(1.0, len(members) / 12.0)
            + 0.18 * min(1.0, density)
            + 0.13 * min(1.0, corner_response)
        )
        bbox = _normalized_xywh(x1, y1, box_width, box_height, width, height)
        if _bbox_area(bbox) <= 0.00025 or _bbox_area(bbox) > 0.28:
            continue
        outputs.append(
            _cv_box_thing(
                "corner_junctions",
                len(outputs),
                bbox,
                score,
                kind="corner_support",
                label="corner_junctions: local support",
                properties={
                    "anchor_xy_px": [round(px, 2), round(py, 2)],
                    "member_count": len(members),
                    "cluster_radius_px": 96,
                    "cluster_padding_px": 20,
                    "corner_response": round(corner_response, 4),
                    "working_width": width,
                    "working_height": height,
                },
            )
        )

    for left_index, (x1, y1) in enumerate(points_xy):
        for right_index in range(left_index + 1, len(points_xy)):
            x2, y2 = points_xy[right_index]
            dx = abs(x2 - x1)
            dy = abs(y2 - y1)
            distance = math.hypot(dx, dy)
            if distance < 24.0 or distance > 360.0:
                continue
            angle = abs(math.degrees(math.atan2(y2 - y1, x2 - x1))) % 180.0
            axis_bonus = max(
                0.0,
                1.0 - min(abs(angle), abs(angle - 90.0), abs(angle - 180.0)) / 45.0,
            )
            if axis_bonus < 0.06:
                continue
            pad = 16
            box = (
                int(math.floor(min(x1, x2) - pad)),
                int(math.floor(min(y1, y2) - pad)),
                int(math.ceil(max(x1, x2) + pad)),
                int(math.ceil(max(y1, y2) + pad)),
            )
            box_width = box[2] - box[0]
            box_height = box[3] - box[1]
            bbox = _normalized_xywh(
                box[0], box[1], box_width, box_height, width, height
            )
            if _bbox_area(bbox) <= 0.00025 or _bbox_area(bbox) > 0.28:
                continue
            support = (
                float(response[int(round(y1)), int(round(x1))])
                + float(response[int(round(y2)), int(round(x2))])
            ) / (2.0 * response_max)
            score = (
                0.23
                + 0.28 * axis_bonus
                + 0.25 * min(1.0, distance / 260.0)
                + 0.24 * min(1.0, support)
            )
            outputs.append(
                _cv_box_thing(
                    "corner_junctions",
                    len(outputs),
                    bbox,
                    score,
                    kind="corner_pair_support",
                    label="corner_junctions: aligned pair",
                    properties={
                        "pair_xy_px": [
                            [round(x1, 2), round(y1, 2)],
                            [round(x2, 2), round(y2, 2)],
                        ],
                        "distance_px": round(distance, 3),
                        "angle_deg": round(angle, 3),
                        "axis_bonus": round(axis_bonus, 4),
                        "pair_padding_px": pad,
                        "working_width": width,
                        "working_height": height,
                    },
                )
            )
    return _dedupe_cv_things(outputs, threshold=0.56, max_boxes=max_boxes)


def _quad_score(
    contour: np.ndarray,
    epsilon_fraction: float,
) -> tuple[float, int, float, float]:
    perimeter = max(float(cv2.arcLength(contour, True)), 1.0)
    approx = cv2.approxPolyDP(contour, epsilon_fraction * perimeter, True)
    vertices = len(approx)
    contour_area = max(float(cv2.contourArea(contour)), 1.0)
    hull_area = max(float(cv2.contourArea(cv2.convexHull(contour))), 1.0)
    x, y, box_width, box_height = cv2.boundingRect(contour)
    fill = contour_area / max(float(box_width * box_height), 1.0)
    solidity = contour_area / hull_area
    rectangularity = min(1.0, max(0.0, fill * 1.6))
    score = rectangularity * (1.0 if 4 <= vertices <= 8 else 0.72) * solidity
    return score, vertices, fill, solidity


def _quad_rectangularity(
    rgb: np.ndarray,
    *,
    working_width: int,
    max_boxes: int,
) -> list[PerceivedThing]:
    working = _working_rgb(rgb, working_width)
    outputs: list[PerceivedThing] = []
    for channel_name, channel in _gray_variants(working).items():
        for block, constant, morph_size, morph_iter in (
            (31, 5, 3, 1),
            (51, 9, 5, 1),
            (71, 12, 7, 2),
        ):
            binary = cv2.adaptiveThreshold(
                channel,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                block,
                constant,
            )
            kernel = cv2.getStructuringElement(
                cv2.MORPH_RECT,
                (morph_size, morph_size),
            )
            cleaned = cv2.morphologyEx(
                binary,
                cv2.MORPH_CLOSE,
                kernel,
                iterations=morph_iter,
            )
            cleaned = cv2.morphologyEx(
                cleaned,
                cv2.MORPH_OPEN,
                np.ones((3, 3), dtype=np.uint8),
                iterations=1,
            )
            contours, _ = cv2.findContours(
                cleaned,
                cv2.RETR_LIST,
                cv2.CHAIN_APPROX_SIMPLE,
            )
            for contour in contours:
                x, y, box_width, box_height = cv2.boundingRect(contour)
                area_fraction = box_width * box_height / max(float(cleaned.shape[0] * cleaned.shape[1]), 1.0)
                if (
                    area_fraction < 0.0025
                    or area_fraction > 0.30
                    or box_width < 20
                    or box_height < 20
                ):
                    continue
                score, vertices, fill, solidity = _quad_score(contour, 0.018)
                if score < 0.20 or vertices > 12:
                    continue
                bbox = _normalized_xywh(
                    x,
                    y,
                    box_width,
                    box_height,
                    cleaned.shape[1],
                    cleaned.shape[0],
                )
                outputs.append(
                    _cv_box_thing(
                        "quad_rectangularity",
                        len(outputs),
                        bbox,
                        0.35 + 0.50 * score,
                        kind="quadrilateral_fragment",
                        label="quad_rectangularity: loose quadrilateral",
                        properties={
                            "channel": channel_name,
                            "adaptive_block": block,
                            "adaptive_constant": constant,
                            "morph_kernel": morph_size,
                            "morph_iter": morph_iter,
                            "epsilon_fraction": 0.018,
                            "vertices": vertices,
                            "fill": round(fill, 4),
                            "solidity": round(solidity, 4),
                            "working_width": cleaned.shape[1],
                            "working_height": cleaned.shape[0],
                        },
                    )
                )
    return _dedupe_cv_things(outputs, threshold=0.90, max_boxes=max_boxes)


def _partial_contour(
    rgb: np.ndarray,
    *,
    working_width: int,
    max_boxes: int,
) -> list[PerceivedThing]:
    working = _working_rgb(rgb, working_width)
    height, width = working.shape[:2]
    outputs: list[PerceivedThing] = []
    for channel_name, channel in _gray_variants(working).items():
        for low, high, blur in ((25, 75, 3), (45, 120, 5), (70, 170, 7)):
            edges = cv2.Canny(
                cv2.GaussianBlur(channel, (blur, blur), 0),
                low,
                high,
                L2gradient=True,
            )
            edges = cv2.morphologyEx(
                edges,
                cv2.MORPH_CLOSE,
                np.ones((3, 3), dtype=np.uint8),
                iterations=1,
            )
            contours, _ = cv2.findContours(
                edges,
                cv2.RETR_LIST,
                cv2.CHAIN_APPROX_NONE,
            )
            for contour in contours:
                x, y, box_width, box_height = cv2.boundingRect(contour)
                area_fraction = box_width * box_height / max(float(width * height), 1.0)
                if (
                    area_fraction < 0.0015
                    or area_fraction > 0.18
                    or box_width < 14
                    or box_height < 14
                ):
                    continue
                perimeter = max(float(cv2.arcLength(contour, False)), 1.0)
                contour_area = float(cv2.contourArea(contour))
                long_edge = max(box_width, box_height) / max(min(box_width, box_height), 1)
                compact = 1.0 - min(
                    1.0,
                    abs(math.log(max(long_edge, 1e-6))) / 3.5,
                )
                density = min(1.0, len(contour) / max(perimeter * 0.12, 1.0))
                confidence = (
                    0.25
                    + 0.35 * compact
                    + 0.25 * density
                    + 0.15 * min(1.0, contour_area / max(box_width * box_height, 1))
                )
                bbox = _normalized_xywh(x, y, box_width, box_height, width, height)
                outputs.append(
                    _cv_box_thing(
                        "partial_contour",
                        len(outputs),
                        bbox,
                        confidence,
                        kind="partial_edge_fragment",
                        label="partial_contour: edge fragment",
                        properties={
                            "channel": channel_name,
                            "canny": [low, high],
                            "blur": blur,
                            "close_kernel": 3,
                            "close_iter": 1,
                            "partial": True,
                            "points": len(contour),
                            "long_edge_ratio": round(long_edge, 4),
                            "working_width": width,
                            "working_height": height,
                        },
                    )
                )
    return _dedupe_cv_things(outputs, threshold=0.88, max_boxes=max_boxes)


def _photometric_windows(
    rgb: np.ndarray,
    *,
    working_width: int,
    max_boxes: int,
) -> list[PerceivedThing]:
    working = _working_rgb(rgb, working_width)
    height, width = working.shape[:2]
    hsv = cv2.cvtColor(working, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(working, cv2.COLOR_RGB2GRAY)
    hue = hsv[..., 0]
    saturation = hsv[..., 1]
    value = hsv[..., 2]
    chromatic = (
        (hue >= 8)
        & (hue <= 35)
        & (saturation >= 45)
        & (saturation <= 205)
        & (value <= 190)
    )
    dark = (
        (hue >= 8)
        & (hue <= 35)
        & (saturation >= 55)
        & (value <= 125)
    )
    outputs: list[PerceivedThing] = []
    for width_fraction in (0.15, 0.22, 0.30, 0.38, 0.42):
        box_width = max(24, int(round(width * width_fraction)))
        for aspect in (0.75, 1.0, 1.25):
            box_height = max(24, int(round(box_width * aspect)))
            if box_width >= width or box_height >= height:
                continue
            step_x = max(12, int(round(box_width * 0.42)))
            step_y = max(12, int(round(box_height * 0.42)))
            for y in range(0, height - box_height + 1, step_y):
                for x in range(0, width - box_width + 1, step_x):
                    roi_chromatic = chromatic[y : y + box_height, x : x + box_width]
                    roi_dark = dark[y : y + box_height, x : x + box_width]
                    chromatic_fraction = float(np.mean(roi_chromatic))
                    dark_fraction = float(np.mean(roi_dark))
                    inside_mean = float(np.mean(gray[y : y + box_height, x : x + box_width]))
                    ring_x1 = max(0, x - box_width // 4)
                    ring_y1 = max(0, y - box_height // 4)
                    ring_x2 = min(width, x + box_width + box_width // 4)
                    ring_y2 = min(height, y + box_height + box_height // 4)
                    ring = gray[ring_y1:ring_y2, ring_x1:ring_x2]
                    if ring.size > roi_chromatic.size:
                        ring_mean = float(np.mean(ring))
                    else:
                        ring_mean = inside_mean
                    contrast = max(0.0, min(1.0, (ring_mean - inside_mean) / 90.0))
                    score = 0.45 * chromatic_fraction + 0.35 * dark_fraction + 0.20 * contrast
                    if score < 0.28:
                        continue
                    bbox = _normalized_xywh(x, y, box_width, box_height, width, height)
                    outputs.append(
                        _cv_box_thing(
                            "photometric_windows",
                            len(outputs),
                            bbox,
                            score,
                            kind="photometric_region",
                            label="photometric_windows: cardboard-like region",
                            properties={
                                "chromatic_fraction": round(chromatic_fraction, 4),
                                "dark_fraction": round(dark_fraction, 4),
                                "inside_ring_contrast": round(contrast, 4),
                                "window_width_fraction": width_fraction,
                                "aspect": aspect,
                                "working_width": width,
                                "working_height": height,
                            },
                        )
                    )
    return _dedupe_cv_things(outputs, threshold=0.60, max_boxes=max_boxes)


def _hough_fragments(
    rgb: np.ndarray,
    *,
    working_width: int,
    max_boxes: int,
) -> list[PerceivedThing]:
    working = _working_rgb(rgb, working_width)
    height, width = working.shape[:2]
    gray = cv2.cvtColor(working, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 35, 105, L2gradient=True)
    lines = cv2.HoughLinesP(
        edges,
        1.0,
        np.pi / 180.0,
        threshold=30,
        minLineLength=34,
        maxLineGap=14,
    )
    if lines is None:
        return []
    outputs: list[PerceivedThing] = []
    padding = 12
    for line in lines[:, 0, :].tolist():
        x1, y1, x2, y2 = [int(value) for value in line]
        line_width = max(abs(x2 - x1), 1)
        line_height = max(abs(y2 - y1), 1)
        length = float(math.hypot(x2 - x1, y2 - y1))
        left = max(0, min(x1, x2) - padding)
        top = max(0, min(y1, y2) - padding)
        right = min(width - 1, max(x1, x2) + padding)
        bottom = min(height - 1, max(y1, y2) + padding)
        box_width = max(1, right - left + 1)
        box_height = max(1, bottom - top + 1)
        area_fraction = box_width * box_height / max(float(width * height), 1.0)
        if area_fraction < 0.001 or area_fraction > 0.20:
            continue
        support = float(np.count_nonzero(edges[top : bottom + 1, left : right + 1]))
        support = min(1.0, support / max(length * 0.75, 1.0))
        confidence = min(
            0.88,
            0.28
            + 0.30 * support
            + 0.30 * min(1.0, length / 220.0)
            + 0.12 * min(1.0, (line_width + line_height) / 220.0),
        )
        bbox = _normalized_xywh(left, top, box_width, box_height, width, height)
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
        outputs.append(
            _cv_box_thing(
                "hough_fragments",
                len(outputs),
                bbox,
                confidence,
                kind="line_fragment",
                label="hough_fragments: partial line ROI",
                properties={
                    "line_xyxy_norm": [
                        round(x1 / max(width - 1, 1), 5),
                        round(y1 / max(height - 1, 1), 5),
                        round(x2 / max(width - 1, 1), 5),
                        round(y2 / max(height - 1, 1), 5),
                    ],
                    "length_px": round(length, 3),
                    "angle_deg": round(angle, 3),
                    "edge_support": round(support, 4),
                    "padding_px": padding,
                    "working_width": width,
                    "working_height": height,
                },
            )
        )
    return _dedupe_cv_things(outputs, threshold=0.72, max_boxes=max_boxes)


def _hsv_components(
    rgb: np.ndarray,
    *,
    working_width: int,
    max_boxes: int,
    min_area_fraction: float,
    max_area_fraction: float,
    hue_min: int,
    hue_max: int,
    saturation_min: int,
    value_max: int,
    open_iterations: int,
    close_iterations: int,
) -> list[PerceivedThing]:
    """Return compact warm/dark color components as deliberately noisy cues."""
    source_height, source_width = rgb.shape[:2]
    scale = min(1.0, working_width / max(source_width, 1))
    width = max(1, int(round(source_width * scale)))
    height = max(1, int(round(source_height * scale)))
    working = (
        cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA)
        if scale < 1
        else rgb
    )
    hsv = cv2.cvtColor(working, cv2.COLOR_RGB2HSV)
    mask = (
        (hsv[..., 0] >= hue_min)
        & (hsv[..., 0] <= hue_max)
        & (hsv[..., 1] >= saturation_min)
        & (hsv[..., 2] < value_max)
    ).astype(np.uint8)
    kernel = np.ones((3, 3), dtype=np.uint8)
    if open_iterations:
        mask = cv2.morphologyEx(
            mask,
            cv2.MORPH_OPEN,
            kernel,
            iterations=open_iterations,
        )
    if close_iterations:
        mask = cv2.morphologyEx(
            mask,
            cv2.MORPH_CLOSE,
            kernel,
            iterations=close_iterations,
        )

    image_area = max(1, width * height)
    min_area = max(4, int(round(image_area * min_area_fraction)))
    max_area = max(min_area, int(round(image_area * max_area_fraction)))
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        mask,
        connectivity=8,
    )
    candidates: list[tuple[float, PerceivedThing]] = []
    for label in range(1, count):
        x = int(stats[label, cv2.CC_STAT_LEFT])
        y = int(stats[label, cv2.CC_STAT_TOP])
        box_width = int(stats[label, cv2.CC_STAT_WIDTH])
        box_height = int(stats[label, cv2.CC_STAT_HEIGHT])
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < min_area or area > max_area or box_width < 4 or box_height < 4:
            continue
        component = labels == label
        pixels = hsv[component].astype(np.float32)
        saturation_score = float(np.mean(pixels[:, 1]) / 255.0)
        darkness_score = float(1.0 - np.mean(pixels[:, 2]) / 255.0)
        support_score = min(1.0, area / max(min_area * 8.0, 1.0))
        confidence = round(
            float(0.50 * saturation_score + 0.30 * darkness_score + 0.20 * support_score),
            5,
        )
        bbox = (
            round(x / max(width - 1, 1), 5),
            round(y / max(height - 1, 1), 5),
            round((x + box_width - 1) / max(width - 1, 1), 5),
            round((y + box_height - 1) / max(height - 1, 1), 5),
        )
        thing = PerceivedThing(
            thing_id=f"hsv_component_{len(candidates):03d}",
            kind="color_component",
            label="warm/dark HSV component",
            location=ViewLocation(
                frame="image",
                zone=_zone_from_bbox(bbox),
                bbox_xyxy_norm=bbox,
            ),
            confidence=confidence,
            properties={
                "evidence": "hsv_warm_dark_component",
                "area_fraction": round(float(area / image_area), 6),
                "hue_range": [hue_min, hue_max],
                "saturation_min": saturation_min,
                "value_max": value_max,
                "open_iterations": open_iterations,
                "close_iterations": close_iterations,
                "working_width": width,
                "working_height": height,
            },
        )
        candidates.append((confidence, thing))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [thing for _, thing in candidates[:max_boxes]]


def _render_cv_overlay(rgb: np.ndarray, boxes: list[RawBox], path: Path) -> None:
    image = Image.fromarray(rgb, mode="RGB")
    draw = ImageDraw.Draw(image)
    colors = {
        "floor_plane": (255, 212, 45),
        "motion_tracks": (50, 170, 255),
        "classical_regions": (55, 215, 110),
        "floor_continuity": (255, 120, 45),
        "edge_rectangles": (205, 90, 235),
    }
    for index, box in enumerate(boxes):
        color = colors.get(box.source, (240, 240, 240))
        pixel_box = _pixel_bbox(box.bbox, image.size)
        draw.rectangle(pixel_box, outline=color, width=3)
        label = f"{box.source} {box.confidence:.2f}"
        _draw_label(draw, pixel_box, label, color, image.size)
    if not boxes:
        draw.text((8, 8), "no raw CV boxes", fill=(255, 255, 255))
    image.save(path)


def _render_jev_overlay(rgb: np.ndarray, jev_result: dict[str, Any], path: Path) -> None:
    image = Image.fromarray(rgb, mode="RGB")
    draw = ImageDraw.Draw(image)
    selected = [
        item for item in jev_result.get("selected", [])
        if item.get("hypothesis") is not None
    ]
    for index, item in enumerate(selected):
        hypothesis = item["hypothesis"]
        bbox = tuple(float(value) for value in hypothesis["bbox_xyxy_norm"])
        color = (255, 55, 190) if index % 2 == 0 else (70, 235, 255)
        pixel_box = _pixel_bbox(bbox, image.size)
        draw.rectangle(pixel_box, outline=color, width=5)
        label = f"Jev {item.get('confidence', 0.0):.2f} {hypothesis.get('kind', 'box')}"
        _draw_label(draw, pixel_box, label, color, image.size)
    if not selected:
        status = str(jev_result.get("status") or "none")
        draw.text((8, 8), f"Jev: {status}", fill=(255, 255, 255))
    image.save(path)


def _render_side_by_side(
    rgb: np.ndarray,
    boxes: list[RawBox],
    jev_result: dict[str, Any],
    path: Path,
) -> None:
    left_path = path.with_name(".cv_only_tmp.png")
    right_path = path.with_name(".jev_boundaries_tmp.png")
    _render_cv_overlay(rgb, boxes, left_path)
    _render_jev_overlay(rgb, jev_result, right_path)
    left = Image.open(left_path).convert("RGB")
    right = Image.open(right_path).convert("RGB")
    header_height = 30
    canvas = Image.new("RGB", (left.width * 2, left.height + header_height), (25, 30, 35))
    canvas.paste(left, (0, header_height))
    canvas.paste(right, (left.width, header_height))
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 8), "CV only — raw cues", fill=(255, 255, 255))
    draw.text((left.width + 8, 8), "Jev — selected boundaries", fill=(255, 255, 255))
    draw.line([(left.width, 0), (left.width, canvas.height)], fill=(255, 255, 255), width=2)
    canvas.save(path)
    left_path.unlink(missing_ok=True)
    right_path.unlink(missing_ok=True)


def _draw_label(
    draw: ImageDraw.ImageDraw,
    pixel_box: tuple[int, int, int, int],
    label: str,
    color: tuple[int, int, int],
    image_size: tuple[int, int],
) -> None:
    x1, y1, _, _ = pixel_box
    text_y = max(0, y1 - 15)
    text_width = min(image_size[0] - 1, x1 + max(30, len(label) * 6))
    draw.rectangle([x1, text_y, text_width, text_y + 14], fill=(0, 0, 0))
    draw.text((x1 + 2, text_y), label, fill=color)


def _pixel_bbox(
    bbox: tuple[float, float, float, float],
    size: tuple[int, int],
) -> tuple[int, int, int, int]:
    width, height = size
    return (
        int(round(bbox[0] * max(width - 1, 1))),
        int(round(bbox[1] * max(height - 1, 1))),
        int(round(bbox[2] * max(width - 1, 1))),
        int(round(bbox[3] * max(height - 1, 1))),
    )


def _normalize_bbox(value: Any) -> tuple[float, float, float, float] | None:
    try:
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            return None
        coordinates = tuple(max(0.0, min(1.0, float(item))) for item in value)
        x1, y1, x2, y2 = coordinates
        if x2 < x1 or y2 < y1:
            return None
        return coordinates
    except (TypeError, ValueError):
        return None


def _bbox_area(bbox: tuple[float, float, float, float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _bbox_iou(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = _bbox_area(left) + _bbox_area(right) - intersection
    return intersection / union if union > 0.0 else 0.0


def _center_distance(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    left_center = ((left[0] + left[2]) / 2.0, (left[1] + left[3]) / 2.0)
    right_center = ((right[0] + right[2]) / 2.0, (right[1] + right[3]) / 2.0)
    return float(np.hypot(left_center[0] - right_center[0], left_center[1] - right_center[1]))


def _weighted_median_bbox(boxes: list[RawBox]) -> tuple[float, float, float, float]:
    # A detector may emit several correlated fragments for one object. Normalize
    # each detector's total influence before taking coordinate medians so a noisy
    # source cannot outvote an independent source by count alone.
    source_totals: dict[str, float] = {}
    for box in boxes:
        source_totals[box.source] = source_totals.get(box.source, 0.0) + max(
            0.01,
            box.confidence,
        )
    weights = [
        max(0.01, box.confidence) / max(source_totals.get(box.source, 1.0), 0.01)
        for box in boxes
    ]
    coordinates = []
    for coordinate in range(4):
        ordered = sorted(zip((box.bbox[coordinate] for box in boxes), weights))
        total = sum(weight for _, weight in ordered)
        running = 0.0
        selected = ordered[-1][0]
        for value, weight in ordered:
            running += weight
            if running >= total / 2.0:
                selected = value
                break
        coordinates.append(round(float(selected), 5))
    return _normalize_bbox(coordinates) or (0.0, 0.0, 0.0, 0.0)


def _union_bbox(boxes: list[RawBox]) -> tuple[float, float, float, float]:
    return (
        round(min(box.bbox[0] for box in boxes), 5),
        round(min(box.bbox[1] for box in boxes), 5),
        round(max(box.bbox[2] for box in boxes), 5),
        round(max(box.bbox[3] for box in boxes), 5),
    )


def _robust_extent_bbox(
    boxes: list[RawBox],
    *,
    low_quantile: float = 0.10,
    high_quantile: float = 0.90,
    padding: float = 0.014,
) -> tuple[float, float, float, float]:
    coordinates = np.asarray([box.bbox for box in boxes], dtype=np.float32)
    lower = np.quantile(coordinates, low_quantile, axis=0)
    upper = np.quantile(coordinates, high_quantile, axis=0)
    bbox = (
        max(0.0, float(lower[0]) - padding),
        max(0.0, float(lower[1]) - padding),
        min(1.0, float(upper[2]) + padding),
        min(1.0, float(upper[3]) + padding),
    )
    return _normalize_bbox(bbox) or (0.0, 0.0, 0.0, 0.0)


def _intersection_bbox(boxes: list[RawBox]) -> tuple[float, float, float, float] | None:
    bbox = (
        max(box.bbox[0] for box in boxes),
        max(box.bbox[1] for box in boxes),
        min(box.bbox[2] for box in boxes),
        min(box.bbox[3] for box in boxes),
    )
    return bbox if bbox[2] > bbox[0] and bbox[3] > bbox[1] else None


def _support_confidence(boxes: list[RawBox]) -> float:
    source_count = len({box.source for box in boxes})
    mean_confidence = sum(box.confidence for box in boxes) / max(len(boxes), 1)
    return round(float(min(1.0, 0.25 * min(source_count, 4) + 0.75 * mean_confidence)), 5)


def _mean_confidence(values: list[Any]) -> float:
    if not values:
        return 0.0
    confidences = []
    for value in values:
        if isinstance(value, RawBox):
            confidences.append(value.confidence)
        elif isinstance(value, dict):
            try:
                confidences.append(float(value.get("confidence") or 0.0))
            except (TypeError, ValueError):
                pass
    return round(float(sum(confidences) / max(len(confidences), 1)), 5)


def _zone_from_bbox(bbox: tuple[float, float, float, float]) -> str:
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    horizontal = "left" if cx < 0.45 else "right" if cx > 0.55 else "center"
    vertical = "near" if cy > 0.66 else "far" if cy < 0.33 else "mid"
    return f"{vertical}_{horizontal}"


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)


def _canonical_json(value: Any) -> str:
    return json.dumps(_json_safe(value), separators=(",", ":"), sort_keys=True)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
