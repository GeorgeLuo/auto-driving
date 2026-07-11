from __future__ import annotations

import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from autonomy.perception.core import observe_frame
from autonomy.perception.traversability import FloorPlaneConfig, process_still

from .memory import AutonomyRunMemory, FrameMemory, timestamp_ms, write_json


def now_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def build_memory_from_image(
    *,
    image_path: Path,
    out_dir: Path,
    run_id: str | None = None,
    source_label: str = "local-image",
    include_traversability: bool = True,
    floor_config: FloorPlaneConfig | None = None,
) -> dict[str, Any]:
    """Represent one image as the current decision-memory frame shape."""
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = out_dir / "frames"
    perception_dir = out_dir / "perception"
    frames_dir.mkdir(parents=True, exist_ok=True)
    perception_dir.mkdir(parents=True, exist_ok=True)

    source = Path(image_path).resolve()
    if not source.exists():
        raise FileNotFoundError(source)

    frame_path = frames_dir / f"frame_000{source.suffix or '.jpg'}"
    if source != frame_path.resolve():
        shutil.copy2(source, frame_path)

    core_observation = observe_frame(frame_path)
    write_json(perception_dir / "frame_observation.json", core_observation)

    traversability: dict[str, Any] | None = None
    if include_traversability:
        traversal_result = process_still(
            frame_path,
            perception_dir / "traversability",
            floor_config or FloorPlaneConfig(),
        )
        traversability = asdict(traversal_result)
        traversability["interpretation"] = (
            "single-frame traversability evidence; not a settled map or obstacle model"
        )

    observation = {
        "schema": "single_frame_memory_observation_v0",
        "source": source_label,
        "core": core_observation,
        "traversability": traversability,
        "relative_motion": None,
        "limits": [
            "single image cannot provide motion groups",
            "single image cannot validate movement or step consistency",
            "traversability is evidence, not world truth",
        ],
    }

    memory = AutonomyRunMemory(
        run_id=run_id or out_dir.name,
        run_type="image_memory_inspection",
        created_at_ms=timestamp_ms(),
        vehicle_source=source_label,
        default_sensor_source="local-file",
    )
    memory.add_event("run-start", {"source": source_label})
    memory.add_event("source-image", {"path": str(source)})

    frame = FrameMemory(
        frame_id="frame_000",
        image_path=str(frame_path.relative_to(out_dir)),
        timestamp_ms=timestamp_ms(),
        sensor_source="local-file",
        command_before_frame=None,
        observation=observation,
    )
    memory.add_frame(frame)
    memory.add_keyframe(
        frame=frame,
        reason="single-image-inspection",
        score=1.0,
    )
    memory.add_event("memory-frame-created", {"frame_id": frame.frame_id})

    memory_path = out_dir / "memory.json"
    memory.write(memory_path)
    _write_summary(out_dir / "summary.md", memory, observation)

    return {
        "out_dir": str(out_dir),
        "memory": str(memory_path),
        "summary": str(out_dir / "summary.md"),
        "frame": str(frame_path),
        "perception": {
            "frame_observation": str(perception_dir / "frame_observation.json"),
            "traversability": None
            if traversability is None
            else traversability["output_files"],
        },
        "memory_object": memory.to_dict(),
    }


def _write_summary(path: Path, memory: AutonomyRunMemory, observation: dict[str, Any]) -> None:
    core = observation["core"]
    traversability = observation.get("traversability") or {}
    lines = [
        f"# Image Memory Inspection: {memory.run_id}",
        "",
        f"- Frames: `{len(memory.frames)}`",
        f"- Keyframes: `{len(memory.keyframes)}`",
        f"- Brightness mean: `{core.get('brightness_mean')}`",
        f"- Contrast std: `{core.get('contrast_std')}`",
        f"- Image size: `{core.get('image_width_px')}x{core.get('image_height_px')}`",
    ]
    if traversability:
        lines.extend(
            [
                f"- Floor fraction ROI: `{traversability.get('floor_fraction_roi'):.3f}`",
                f"- Occupied fraction ROI: `{traversability.get('occupied_fraction_roi'):.3f}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Memory Limits",
            "",
            "- A single image stores frame facts and single-frame traversability evidence.",
            "- Motion groups, step consistency, and action validation require multiple frames plus action history.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
