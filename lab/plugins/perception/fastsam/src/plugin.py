from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from autonomy.perception import (
    PerceivedThing,
    PerceptionPluginContract,
    PerceptionPluginResult,
    PerceptionRequest,
    ViewLocation,
)
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID
from implementations.perception.components import (
    camera_component_id,
    camera_frame,
    camera_frame_error,
)
from implementations.perception.text import thing_line


FRONT_CAMERA_COMPONENT = camera_component_id(FRONT_CAMERA_SENSOR_ID)


class FastSamRegionPlugin:
    """Generate class-agnostic region proposals from a normalized camera frame."""

    plugin_id = "fastsam-regions-v0"
    contract = PerceptionPluginContract(
        required_components=(FRONT_CAMERA_COMPONENT,),
        state_mode="stateless",
        artifact_policy="optional",
    )

    def __init__(
        self,
        *,
        model_path: str,
        device: str = "cpu",
        image_size: int = 640,
        confidence: float = 0.35,
        iou: float = 0.9,
        min_area_fraction: float = 0.002,
        max_regions: int = 32,
        write_artifacts: bool = True,
    ) -> None:
        self.model_path = Path(model_path)
        self.device = str(device)
        self.image_size = max(160, int(image_size))
        self.confidence = max(0.0, min(1.0, float(confidence)))
        self.iou = max(0.0, min(1.0, float(iou)))
        self.min_area_fraction = max(0.0, min(1.0, float(min_area_fraction)))
        self.max_regions = max(1, int(max_regions))
        self.write_artifacts = bool(write_artifacts)
        self._model = None

    def reset(self) -> None:
        return None

    def describe_schema(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "reads": ["normalized front_camera RGB pixels"],
            "emits": ["thing kind=region_proposal for each accepted class-agnostic mask"],
            "properties": [
                "area_fraction",
                "centroid_xy_norm",
                "contour_xy_norm",
                "touches_lower_image",
                "model_confidence",
            ],
            "limits": [
                "regions have no semantic class",
                "regions do not imply obstacle or traversability",
                "single-frame masks do not estimate depth or persistence",
            ],
        }

    def perceive(self, request: PerceptionRequest) -> PerceptionPluginResult:
        frame = camera_frame(request, FRONT_CAMERA_SENSOR_ID)
        if frame is None:
            return PerceptionPluginResult(
                status="unavailable",
                lines=("signal id=fastsam_regions_available value=false reason=no_front_camera confidence=0.000",),
                observations={self.plugin_id: {"input_error": camera_frame_error(request, FRONT_CAMERA_SENSOR_ID)}},
                limits=("front camera image missing",),
            )

        model = self._load_model()
        results = model.predict(
            source=cv2.cvtColor(frame.rgb, cv2.COLOR_RGB2BGR),
            device=self.device,
            imgsz=self.image_size,
            conf=self.confidence,
            iou=self.iou,
            retina_masks=True,
            verbose=False,
        )
        result = results[0]
        masks = _mask_array(result)
        confidences = _confidence_array(result, len(masks))
        proposals = _region_proposals(
            masks,
            confidences,
            min_area_fraction=self.min_area_fraction,
            max_regions=self.max_regions,
        )

        things = tuple(_proposal_thing(index, proposal) for index, proposal in enumerate(proposals))
        lines = [
            "signal id=fastsam_regions_available "
            f"value={'true' if things else 'false'} confidence={_mean_confidence(things):.3f} "
            f"raw_masks={len(masks)} regions={len(things)}"
        ]
        lines.extend(thing_line(thing) for thing in things)

        artifacts: dict[str, str] = {}
        if request.output_dir is not None and self.write_artifacts:
            artifacts = _write_artifacts(
                frame.rgb,
                proposals,
                request.output_dir / "fastsam",
                model_path=self.model_path,
            )

        speed = dict(getattr(result, "speed", {}) or {})
        return PerceptionPluginResult(
            status="ok" if things else "empty",
            lines=tuple(lines),
            things=things,
            observations={
                self.plugin_id: {
                    "model": self.model_path.name,
                    "device": self.device,
                    "image_size": self.image_size,
                    "raw_mask_count": len(masks),
                    "region_count": len(things),
                    "speed_ms": speed,
                }
            },
            artifacts=artifacts,
            limits=(
                "FastSAM regions are class-agnostic current-frame proposals",
                "no semantic identity, depth, traversability, or persistence is inferred",
            ),
        )

    def _load_model(self):
        if self._model is not None:
            return self._model
        if not self.model_path.is_file():
            raise FileNotFoundError(
                f"FastSAM model is missing at {self.model_path}; "
                "run `./cli/automa vehicles perception setup fastsam`"
            )
        from ultralytics import FastSAM

        self._model = FastSAM(str(self.model_path))
        return self._model


def _mask_array(result) -> np.ndarray:
    masks = getattr(result, "masks", None)
    data = getattr(masks, "data", None)
    if data is None:
        return np.empty((0, 0, 0), dtype=bool)
    if hasattr(data, "detach"):
        data = data.detach()
    if hasattr(data, "cpu"):
        data = data.cpu()
    if hasattr(data, "numpy"):
        data = data.numpy()
    array = np.asarray(data)
    return array > 0.5


def _confidence_array(result, count: int) -> np.ndarray:
    boxes = getattr(result, "boxes", None)
    data = getattr(boxes, "conf", None)
    if data is None:
        return np.ones(count, dtype=np.float32)
    if hasattr(data, "detach"):
        data = data.detach()
    if hasattr(data, "cpu"):
        data = data.cpu()
    if hasattr(data, "numpy"):
        data = data.numpy()
    values = np.asarray(data, dtype=np.float32).reshape(-1)
    if len(values) < count:
        values = np.pad(values, (0, count - len(values)), constant_values=1.0)
    return values[:count]


def _region_proposals(
    masks: np.ndarray,
    confidences: np.ndarray,
    *,
    min_area_fraction: float,
    max_regions: int,
) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    for index, mask in enumerate(masks):
        height, width = mask.shape[:2]
        area = int(mask.sum())
        area_fraction = area / max(1, width * height)
        if area_fraction < min_area_fraction:
            continue
        ys, xs = np.nonzero(mask)
        if len(xs) == 0:
            continue
        contour = _normalized_contour(mask)
        proposals.append({
            "mask": mask,
            "area_fraction": round(float(area_fraction), 6),
            "bbox": (
                round(float(xs.min()) / max(width - 1, 1), 5),
                round(float(ys.min()) / max(height - 1, 1), 5),
                round(float(xs.max()) / max(width - 1, 1), 5),
                round(float(ys.max()) / max(height - 1, 1), 5),
            ),
            "centroid": (
                round(float(xs.mean()) / max(width - 1, 1), 5),
                round(float(ys.mean()) / max(height - 1, 1), 5),
            ),
            "contour": contour,
            "confidence": round(float(confidences[index]), 5),
            "touches_lower_image": bool(ys.max() >= height * 0.85),
        })
    proposals.sort(key=lambda item: (item["confidence"], item["area_fraction"]), reverse=True)
    return proposals[:max_regions]


def _normalized_contour(mask: np.ndarray) -> list[list[float]]:
    height, width = mask.shape[:2]
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return []
    contour = max(contours, key=cv2.contourArea)
    epsilon = max(1.0, cv2.arcLength(contour, True) * 0.01)
    points = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2)
    if len(points) > 64:
        indices = np.linspace(0, len(points) - 1, 64, dtype=int)
        points = points[indices]
    return [
        [
            round(float(x) / max(width - 1, 1), 5),
            round(float(y) / max(height - 1, 1), 5),
        ]
        for x, y in points
    ]


def _proposal_thing(index: int, proposal: dict[str, Any]) -> PerceivedThing:
    return PerceivedThing(
        thing_id=f"fastsam_region_{index:03d}",
        kind="region_proposal",
        label="class-agnostic image region",
        location=ViewLocation(
            frame="image",
            zone=_zone(proposal["centroid"]),
            bbox_xyxy_norm=proposal["bbox"],
        ),
        confidence=proposal["confidence"],
        properties={
            "evidence": "fastsam_mask",
            "area_fraction": proposal["area_fraction"],
            "centroid_xy_norm": proposal["centroid"],
            "contour_xy_norm": proposal["contour"],
            "touches_lower_image": proposal["touches_lower_image"],
            "model_confidence": proposal["confidence"],
        },
    )


def _zone(centroid: tuple[float, float]) -> str:
    x, y = centroid
    horizontal = "left" if x < 0.4 else "right" if x > 0.6 else "center"
    vertical = "near" if y > 0.66 else "far" if y < 0.33 else "mid"
    return f"{vertical}_{horizontal}"


def _mean_confidence(things: tuple[PerceivedThing, ...]) -> float:
    if not things:
        return 0.0
    return float(sum(thing.confidence for thing in things) / len(things))


def _write_artifacts(
    rgb: np.ndarray,
    proposals: list[dict[str, Any]],
    output_dir: Path,
    *,
    model_path: Path,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    overlay = rgb.copy()
    palette = np.array([
        [38, 166, 91],
        [43, 116, 189],
        [218, 135, 39],
        [172, 74, 184],
        [201, 72, 74],
    ], dtype=np.uint8)
    for index, proposal in enumerate(proposals):
        mask = proposal["mask"]
        color = palette[index % len(palette)]
        overlay[mask] = (0.55 * overlay[mask] + 0.45 * color).astype(np.uint8)
    overlay_path = output_dir / "regions.png"
    cv2.imwrite(str(overlay_path), cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))
    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps({
            "model": str(model_path),
            "regions": [
                {key: value for key, value in proposal.items() if key != "mask"}
                for proposal in proposals
            ],
        }, indent=2),
        encoding="utf-8",
    )
    return {
        "fastsam_regions": str(overlay_path),
        "fastsam_summary": str(summary_path),
    }
