from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

from autonomy.perception.interface import PerceivedThing, PerceptionRequest, ViewLocation
from autonomy.perception.motion.scene_motion import analyze_scene_motion
from implementations.perception.chain import PerceptionPluginResult
from implementations.perception.text import thing_line
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID


class MotionGroupsPlugin:
    """Group pairwise visual motion between the previous and current frame."""

    plugin_id = "motion-groups-v0"

    def __init__(
        self,
        *,
        max_features: int = 160,
        min_distance: int = 5,
        patch_radius: int = 4,
        search_radius: int = 90,
        min_score: float = 0.70,
        max_groups: int = 5,
        min_group_size: int = 6,
        residual_threshold: float = 7.0,
        write_artifacts: bool = True,
    ) -> None:
        self.max_features = int(max_features)
        self.min_distance = int(min_distance)
        self.patch_radius = int(patch_radius)
        self.search_radius = int(search_radius)
        self.min_score = float(min_score)
        self.max_groups = int(max_groups)
        self.min_group_size = int(min_group_size)
        self.residual_threshold = float(residual_threshold)
        self.write_artifacts = bool(write_artifacts)
        self._state_dir = tempfile.TemporaryDirectory(prefix="automa_motion_groups_")
        self._previous_image_path: Path | None = None

    def describe_schema(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "reads": ["front_camera RGB pixels", "previous front_camera frame retained by plugin instance"],
            "assumptions": [
                "nearby frames have enough visual overlap for patch matching",
                "motion groups are image-space evidence, not semantic object identities",
            ],
            "emits": [
                "signal id=motion_groups_available",
                "thing kind=motion_group for coherent pairwise motion groups",
            ],
            "artifacts": ["scene_motion"],
        }

    def perceive(self, request: PerceptionRequest) -> PerceptionPluginResult:
        front = request.snapshot.readings.get(FRONT_CAMERA_SENSOR_ID)
        if front is None or front.path is None:
            self._previous_image_path = None
            return PerceptionPluginResult(
                lines=("signal id=motion_groups_available value=false confidence=0.000 reason=no_front_camera",),
                observations={self.plugin_id: {"front_camera_available": False}},
                limits=("front camera image missing",),
            )

        current_path = Path(front.path)
        previous_path = self._previous_image_path

        if previous_path is None or not previous_path.exists():
            self._cache_current_frame(current_path)
            return PerceptionPluginResult(
                lines=("signal id=motion_groups_available value=false confidence=0.000 reason=no_previous_frame",),
                observations={self.plugin_id: {"has_previous_frame": False}},
                limits=("motion groups require at least two frames from the same plugin instance",),
            )

        output_dir = request.output_dir / "motion_groups" if request.output_dir and self.write_artifacts else Path(self._state_dir.name) / "latest"
        try:
            result = analyze_scene_motion(
                previous_path,
                current_path,
                output_dir,
                max_features=self.max_features,
                min_distance=self.min_distance,
                patch_radius=self.patch_radius,
                search_radius=self.search_radius,
                min_score=self.min_score,
                max_groups=self.max_groups,
                min_group_size=self.min_group_size,
                residual_threshold=self.residual_threshold,
            )
        except Exception as exc:
            self._cache_current_frame(current_path)
            return PerceptionPluginResult(
                lines=(f"signal id=motion_groups_available value=false confidence=0.000 reason=analysis_failed error={type(exc).__name__}",),
                observations={self.plugin_id: {"error": str(exc)}},
                limits=("motion group analysis failed",),
            )

        self._cache_current_frame(current_path)

        things: list[PerceivedThing] = []
        with Image.open(current_path) as image:
            width, height = image.size

        for group in result.groups:
            bbox = _bbox_xyxy_to_norm(group.target_bbox, width, height)
            thing = PerceivedThing(
                thing_id=f"motion_group_{group.group_id}",
                kind="motion_group",
                label=group.kind_hint,
                location=ViewLocation(
                    frame="image",
                    zone=_zone_from_bbox(bbox),
                    bbox_xyxy_norm=bbox,
                ),
                confidence=_group_confidence(group.match_count, group.mean_score, self.min_group_size),
                properties={
                    "match_count": group.match_count,
                    "source_bbox_px": group.source_bbox,
                    "target_bbox_px": group.target_bbox,
                    "center_shift_px": group.center_shift_px,
                    "median_motion_px": group.median_motion_px,
                    "scale": group.scale,
                    "median_residual_px": group.median_residual_px,
                    "kind_hint": group.kind_hint,
                },
            )
            things.append(thing)

        lines = [
            (
                "signal id=motion_groups_available "
                f"value={'true' if things else 'false'} confidence={_overall_motion_confidence(things):.3f} "
                f"keypoints={result.keypoint_count} matches={result.match_count} groups={len(things)}"
            )
        ]
        lines.extend(thing_line(thing) for thing in things)

        artifacts = result.output_files if self.write_artifacts else {}
        return PerceptionPluginResult(
            lines=tuple(lines),
            things=tuple(things),
            observations={
                self.plugin_id: {
                    "keypoint_count": result.keypoint_count,
                    "match_count": result.match_count,
                    "grouped_match_count": result.grouped_match_count,
                    "ungrouped_match_count": result.ungrouped_match_count,
                    "groups": [group.__dict__ for group in result.groups],
                }
            },
            artifacts=artifacts,
            limits=("motion groups are pairwise image-space evidence, not persistent object identities",),
        )

    def _cache_current_frame(self, current_path: Path) -> None:
        state_path = Path(self._state_dir.name) / "previous_frame.png"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(current_path, state_path)
        self._previous_image_path = state_path


def _bbox_xyxy_to_norm(box: list[int], width: int, height: int) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = box
    return (
        round(max(0, min(width - 1, x0)) / max(width - 1, 1), 4),
        round(max(0, min(height - 1, y0)) / max(height - 1, 1), 4),
        round(max(0, min(width - 1, x1)) / max(width - 1, 1), 4),
        round(max(0, min(height - 1, y1)) / max(height - 1, 1), 4),
    )


def _group_confidence(match_count: int, mean_score: float, min_group_size: int) -> float:
    support = min(1.0, match_count / max(min_group_size * 3.0, 1))
    return round(float(0.65 * support + 0.35 * max(0.0, min(1.0, mean_score))), 5)


def _overall_motion_confidence(things: list[PerceivedThing]) -> float:
    if not things:
        return 0.0
    return float(sum(thing.confidence for thing in things) / len(things))


def _zone_from_bbox(bbox: tuple[float, float, float, float] | None) -> str:
    if bbox is None:
        return "unknown"
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    horizontal = "left" if cx < 0.45 else "right" if cx > 0.55 else "center"
    vertical = "near" if cy > 0.66 else "far" if cy < 0.33 else "mid"
    return f"{vertical}_{horizontal}"
