from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from autonomy.perception import (
    PerceivedThing,
    PerceptionEvidenceBatch,
    PerceptionPluginContract,
    PerceptionPluginInputs,
    PerceptionPluginWarmingUp,
    PerceptionSignal,
    ViewLocation,
)
from implementations.perception.components import CameraFrame, FRONT_CAMERA_RGB_INPUT

from .scene_motion import MotionGroup, analyze_scene_motion_images


@dataclass
class _SceneTrack:
    track_id: int
    bbox: tuple[float, float, float, float]
    age_frames: int
    support_frames: int
    missed_frames: int
    confidence: float
    kind_hint: str
    properties: dict[str, Any]


class MotionTracksPlugin:
    """Maintain bounded image-space tracks from coherent feature motion groups."""

    plugin_id = "motion-tracks-v0"
    contract = PerceptionPluginContract(
        inputs=(FRONT_CAMERA_RGB_INPUT,),
        state_mode="windowed",
        description="Track bounded coherent feature-motion regions across frames.",
        assumptions=(
            "neighboring processed frames overlap enough for patch matching",
            "coherent feature motion is structure evidence, not semantic identity",
            "track ids are local to one reset-bounded run",
        ),
        emits=(
            "signal scene_tracks_available",
            "run-local spatial evidence for scene tracks",
        ),
        limitations=(
            "scene tracks are image-space motion evidence, not persistent object identities",
            "camera motion, occlusion, frame drops, and weak texture can merge or split tracks",
        ),
        diagnostic_artifacts=("scene_motion", "summary", "scene_tracks"),
    )

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
        max_track_misses: int = 2,
        min_association_iou: float = 0.05,
        max_association_distance: float = 0.25,
    ) -> None:
        self.max_features = int(max_features)
        self.min_distance = int(min_distance)
        self.patch_radius = int(patch_radius)
        self.search_radius = int(search_radius)
        self.min_score = float(min_score)
        self.max_groups = int(max_groups)
        self.min_group_size = int(min_group_size)
        self.residual_threshold = float(residual_threshold)
        self.max_track_misses = max(0, int(max_track_misses))
        self.min_association_iou = max(0.0, min(1.0, float(min_association_iou)))
        self.max_association_distance = max(0.0, float(max_association_distance))
        self.reset()

    def reset(self) -> None:
        self._previous_rgb: np.ndarray | None = None
        self._tracks: dict[int, _SceneTrack] = {}
        self._next_track_id = 1

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        frame = inputs.require("frame", CameraFrame)
        current_rgb = frame.rgb
        previous_rgb = self._previous_rgb
        if previous_rgb is None:
            self._cache_current_frame(current_rgb)
            raise PerceptionPluginWarmingUp(
                "no_previous_frame",
                measurements={"has_previous_frame": False, "active_tracks": 0},
            )

        try:
            result = self._analyze(
                previous_rgb,
                current_rgb,
                inputs.diagnostics.directory,
                frame,
            )
        finally:
            self._cache_current_frame(current_rgb)
        if result.output_files:
            inputs.diagnostics.register(result.output_files)
        candidates = [
            _group_candidate(group, frame.width_px, frame.height_px, self.min_group_size)
            for group in result.groups
        ]
        expired_ids = self._update_tracks(candidates)
        inputs.diagnostics.emit(
            "scene_tracks",
            "scene_tracks.png",
            lambda path: _write_track_overlay(current_rgb, self._tracks, path),
        )
        things = tuple(_track_thing(track) for track in sorted(self._tracks.values(), key=lambda item: item.track_id))
        visible_count = sum(1 for track in self._tracks.values() if track.missed_frames == 0)
        confidence = _overall_track_confidence(things)

        return PerceptionEvidenceBatch(
            signals=(
                PerceptionSignal(
                    "scene_tracks_available",
                    bool(things),
                    confidence,
                    {
                        "keypoints": result.keypoint_count,
                        "matches": result.match_count,
                        "groups": len(candidates),
                        "visible_tracks": visible_count,
                        "active_tracks": len(things),
                        "expired_tracks": len(expired_ids),
                    },
                ),
            ),
            things=things,
            measurements={
                "keypoint_count": result.keypoint_count,
                "match_count": result.match_count,
                "grouped_match_count": result.grouped_match_count,
                "ungrouped_match_count": result.ungrouped_match_count,
                "groups": [group.__dict__ for group in result.groups],
                "active_tracks": [_track_record(track) for track in self._tracks.values()],
                "expired_track_ids": expired_ids,
                "max_track_misses": self.max_track_misses,
            },
        )

    def _update_tracks(self, candidates: list[dict[str, Any]]) -> list[int]:
        associations: list[tuple[float, int, int]] = []
        for track_id, track in self._tracks.items():
            for candidate_index, candidate in enumerate(candidates):
                source_bbox = candidate["source_bbox"]
                overlap = _bbox_iou(track.bbox, source_bbox)
                distance = _center_distance(track.bbox, source_bbox)
                if overlap < self.min_association_iou and distance > self.max_association_distance:
                    continue
                distance_score = max(0.0, 1.0 - distance / max(self.max_association_distance, 1e-6))
                associations.append((0.75 * overlap + 0.25 * distance_score, track_id, candidate_index))
        associations.sort(reverse=True)

        assigned_tracks: set[int] = set()
        assigned_candidates: set[int] = set()
        for _score, track_id, candidate_index in associations:
            if track_id in assigned_tracks or candidate_index in assigned_candidates:
                continue
            track = self._tracks[track_id]
            candidate = candidates[candidate_index]
            track.bbox = candidate["target_bbox"]
            track.age_frames += 1
            track.support_frames += 1
            track.missed_frames = 0
            track.confidence = round(0.65 * candidate["confidence"] + 0.35 * track.confidence, 5)
            track.kind_hint = candidate["kind_hint"]
            track.properties = candidate["properties"]
            assigned_tracks.add(track_id)
            assigned_candidates.add(candidate_index)

        for track_id, track in self._tracks.items():
            if track_id not in assigned_tracks:
                track.age_frames += 1
                track.missed_frames += 1

        for candidate_index, candidate in enumerate(candidates):
            if candidate_index in assigned_candidates:
                continue
            track_id = self._next_track_id
            self._next_track_id += 1
            self._tracks[track_id] = _SceneTrack(
                track_id=track_id,
                bbox=candidate["target_bbox"],
                age_frames=1,
                support_frames=1,
                missed_frames=0,
                confidence=candidate["confidence"],
                kind_hint=candidate["kind_hint"],
                properties=candidate["properties"],
            )

        expired_ids = sorted(
            track_id
            for track_id, track in self._tracks.items()
            if track.missed_frames > self.max_track_misses
        )
        for track_id in expired_ids:
            del self._tracks[track_id]
        return expired_ids

    def _cache_current_frame(self, current_rgb: np.ndarray) -> None:
        self._previous_rgb = np.array(current_rgb, dtype=np.uint8, copy=True)

    def _analyze(
        self,
        previous_rgb: np.ndarray,
        current_rgb: np.ndarray,
        output_dir: Path | None,
        front,
    ):
        return analyze_scene_motion_images(
            Image.fromarray(previous_rgb, mode="RGB"),
            Image.fromarray(current_rgb, mode="RGB"),
            output_dir,
            image_a_label="previous_frame",
            image_b_label=str(front.source_path) if front.source_path is not None else "current_frame",
            max_features=self.max_features,
            min_distance=self.min_distance,
            patch_radius=self.patch_radius,
            search_radius=self.search_radius,
            min_score=self.min_score,
            max_groups=self.max_groups,
            min_group_size=self.min_group_size,
            residual_threshold=self.residual_threshold,
        )


def _group_candidate(
    group: MotionGroup,
    width: int,
    height: int,
    min_group_size: int,
) -> dict[str, Any]:
    source_bbox = _bbox_xyxy_to_norm(group.source_bbox, width, height)
    target_bbox = _bbox_xyxy_to_norm(group.target_bbox, width, height)
    confidence = _group_confidence(group.match_count, group.mean_score, min_group_size)
    return {
        "source_bbox": source_bbox,
        "target_bbox": target_bbox,
        "confidence": confidence,
        "kind_hint": group.kind_hint,
        "properties": {
            "match_count": group.match_count,
            "source_bbox_xyxy_norm": source_bbox,
            "target_bbox_xyxy_norm": target_bbox,
            "center_shift_px": group.center_shift_px,
            "median_motion_px": group.median_motion_px,
            "scale": group.scale,
            "median_residual_px": group.median_residual_px,
            "kind_hint": group.kind_hint,
        },
    }


def _track_thing(track: _SceneTrack) -> PerceivedThing:
    visible = track.missed_frames == 0
    confidence = track.confidence * (0.7 ** track.missed_frames)
    return PerceivedThing(
        thing_id=f"scene_track_{track.track_id:04d}",
        kind="scene_track",
        label=track.kind_hint,
        location=ViewLocation(
            frame="image",
            zone=_zone_from_bbox(track.bbox),
            bbox_xyxy_norm=track.bbox,
        ),
        confidence=round(confidence, 5),
        properties={
            **track.properties,
            "track_id": track.track_id,
            "age_frames": track.age_frames,
            "support_frames": track.support_frames,
            "missed_frames": track.missed_frames,
            "visible": visible,
        },
    )


def _track_record(track: _SceneTrack) -> dict[str, Any]:
    return {
        "track_id": track.track_id,
        "bbox_xyxy_norm": track.bbox,
        "age_frames": track.age_frames,
        "support_frames": track.support_frames,
        "missed_frames": track.missed_frames,
        "confidence": track.confidence,
        "kind_hint": track.kind_hint,
    }


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


def _overall_track_confidence(things: tuple[PerceivedThing, ...]) -> float:
    if not things:
        return 0.0
    return float(sum(thing.confidence for thing in things) / len(things))


def _bbox_iou(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def _center_distance(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    left_center = ((left[0] + left[2]) / 2.0, (left[1] + left[3]) / 2.0)
    right_center = ((right[0] + right[2]) / 2.0, (right[1] + right[3]) / 2.0)
    return float(np.hypot(left_center[0] - right_center[0], left_center[1] - right_center[1]))


def _zone_from_bbox(bbox: tuple[float, float, float, float]) -> str:
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    horizontal = "left" if cx < 0.45 else "right" if cx > 0.55 else "center"
    vertical = "near" if cy > 0.66 else "far" if cy < 0.33 else "mid"
    return f"{vertical}_{horizontal}"


def _write_track_overlay(
    rgb: np.ndarray,
    tracks: dict[int, _SceneTrack],
    output_path: Path,
) -> None:
    image = Image.fromarray(rgb, mode="RGB")
    draw = ImageDraw.Draw(image)
    width, height = image.size
    colors = [
        (46, 204, 113),
        (52, 152, 219),
        (241, 196, 15),
        (155, 89, 182),
        (231, 76, 60),
    ]
    for track_id, track in sorted(tracks.items()):
        color = (140, 140, 140) if track.missed_frames else colors[(track_id - 1) % len(colors)]
        x1, y1, x2, y2 = track.bbox
        box = [
            int(round(x1 * max(width - 1, 1))),
            int(round(y1 * max(height - 1, 1))),
            int(round(x2 * max(width - 1, 1))),
            int(round(y2 * max(height - 1, 1))),
        ]
        draw.rectangle(box, outline=color, width=3)
        label = (
            f"t{track_id} age={track.age_frames} "
            f"support={track.support_frames} miss={track.missed_frames}"
        )
        text_y = max(0, box[1] - 14)
        draw.rectangle([box[0], text_y, min(width - 1, box[0] + len(label) * 7), text_y + 13], fill=(0, 0, 0))
        draw.text((box[0] + 2, text_y), label, fill=color)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
