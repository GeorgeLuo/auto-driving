"""Completed-cycle recordings shared by local and onboard hosts.

The host commits a cycle and its exact image before counting the decision or
ending the run. Live latest-only publications are independent of this history.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import secrets
import threading
from pathlib import Path
from typing import Any, Callable

from autonomy.decision_cycle.activation import (
    DECISION_STEPS, STEPS, StepActivation, activation_generation_id, require_step,
    step_activation_from_payload,
)
from autonomy.decision_cycle.cycle import DecisionCycleResult
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID

RECORDING_MANIFEST_SCHEMA = "automa_recording_manifest_v0"
RECORDING_MANIFEST_NAME = "manifest.json"


def write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def recorded_selections(record: dict[str, Any]) -> dict[str, StepActivation | None]:
    """Read a frame's executable selections, preserving disabled steps."""
    if "steps" not in record:
        return {}
    payloads = record["steps"]
    if not isinstance(payloads, dict):
        raise ValueError("recorded steps must be an object")
    activations = {
        require_step(step): None if payload is None else step_activation_from_payload(payload, step=step)
        for step, payload in payloads.items()
    }
    generation = record.get("generation_id")
    if generation is not None:
        if not all(step in activations for step in DECISION_STEPS):
            raise ValueError("recorded generation requires proposal, plan, and action selections")
        expected = activation_generation_id(
            {step: activations[step] for step in DECISION_STEPS}, prefix="decision",
        )
        if generation != expected:
            raise ValueError("recorded generation does not match its step selections")
    return activations


def _recording_text(value: Any, label: str) -> str:
    if type(value) is not str or not value or any(char.isspace() for char in value):
        raise ValueError(f"{label} must be a nonempty string")
    return value


def append_recording_frame(
    run_dir: Path,
    *,
    vehicle_id: str,
    run_id: str,
    generation_id: str,
    frame_id: str,
    timestamp_ms: int,
    image_path: Path,
    steps: dict[str, Any],
    frame_index: int | None = None,
    context: dict[str, Any] | None = None,
    plugin_sources: dict[str, str] | None = None,
) -> None:
    """Append one recorded frame to the run directory's manifest.

    Chase and PiCar both call this. The image path is stored relative to the
    run directory, which is the directory replay loads. The frame keeps the
    report identity, original capture time, applied step selections, and inputs.
    """

    vehicle_id = _recording_text(vehicle_id, "vehicle_id")
    run_id = _recording_text(run_id, "run_id")
    generation_id = _recording_text(generation_id, "generation_id")
    frame_id = _recording_text(frame_id, "frame_id")
    if type(timestamp_ms) is not int or timestamp_ms < 0:
        raise ValueError("timestamp_ms must be a nonnegative int")
    if type(steps) is not dict:
        raise ValueError("steps must be the staged step selections")
    if set(steps) != set(STEPS):
        raise ValueError("recording requires the applied selections of every cycle step")
    recorded_selections({"steps": steps, "generation_id": generation_id})
    if frame_index is not None and (type(frame_index) is not int or frame_index < 0):
        raise ValueError("frame_index must be a nonnegative int")
    root = Path(run_dir)
    root.mkdir(parents=True, exist_ok=True)
    candidate = Path(image_path)
    if not candidate.is_absolute():
        resolved = candidate.resolve()
        candidate = resolved if resolved.is_relative_to(root.resolve()) else root / candidate
    try:
        relative_image = candidate.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("recorded image must stay inside the run directory") from exc
    entry: dict[str, Any] = {
        "vehicle_id": vehicle_id,
        "run_id": run_id,
        "generation_id": generation_id,
        "frame_id": frame_id,
        "timestamp_ms": timestamp_ms,
        "image_path": relative_image,
        "steps": copy.deepcopy(steps),
    }
    if frame_index is not None:
        entry["frame_index"] = frame_index
    if context is not None:
        entry["context"] = {
            key: copy.deepcopy(context[key])
            for key in ("mode", "user_steering", "user_throttle") if key in context
        }
    if plugin_sources:
        entry["plugin_sources"] = dict(plugin_sources)
    manifest_path = root / RECORDING_MANIFEST_NAME
    payload: dict[str, Any] = {
        "schema": RECORDING_MANIFEST_SCHEMA,
        "vehicle_id": vehicle_id,
        "run_id": run_id,
        "frames": [],
    }
    if manifest_path.is_file():
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("recording manifest must be a JSON object")
        payload = loaded
    frames = payload.get("frames")
    if not isinstance(frames, list):
        frames = []
    for index, item in enumerate(frames):
        if isinstance(item, dict) and item.get("frame_id") == frame_id:
            frames[index] = entry
            break
    else:
        frames.append(entry)
    payload["schema"] = RECORDING_MANIFEST_SCHEMA
    payload["vehicle_id"] = vehicle_id
    payload["run_id"] = run_id
    payload["frames"] = frames
    write_json_atomically(manifest_path, payload)


def recorded_source_path(run_dir: Path, relative: str) -> Path:
    """Resolve a source asset inside a recording, including after moving it."""
    root = Path(run_dir).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("recorded plugin source must stay inside the run directory")
    return path


def _retain_plugin_sources(run_dir: Path, steps: dict[str, Any],
                           sources: dict[str, bytes] | None) -> dict[str, str]:
    references = {}
    for activation in steps.values():
        for spec in (activation or {}).get("plugin_specs", {}).values():
            original = spec.partition(":")[0]
            if not original.endswith(".py") or original in references:
                continue
            source = Path(original).read_bytes() if sources is None else sources[original]
            relative = f"plugins/{hashlib.sha256(source).hexdigest()}.py"
            asset = run_dir / relative
            asset.parent.mkdir(parents=True, exist_ok=True)
            if not asset.exists():
                temporary = asset.with_suffix(f".{secrets.token_hex(8)}.tmp")
                try:
                    temporary.write_bytes(source)
                    temporary.replace(asset)
                finally:
                    temporary.unlink(missing_ok=True)
            references[original] = relative
    return references


def write_recorded_frame(run_dir: Path, frame: dict[str, Any], image: bytes, extension: str,
                         *, plugin_sources: dict[str, bytes] | None = None) -> None:
    """Commit the same replay artifacts locally or from an onboard transport."""
    frame_id = frame["frame_id"]
    if not frame_id or Path(frame_id).name != frame_id or frame_id in {".", ".."}:
        raise ValueError("recorded frame_id must be a filename")
    if extension not in {".jpg", ".jpeg", ".png"} or not image:
        raise ValueError("recorded camera image is missing or has an unsupported extension")
    sources = _retain_plugin_sources(run_dir, frame["step_activations"], plugin_sources)
    frame = copy.deepcopy(frame)
    if sources:
        frame["plugin_sources"] = sources
    image_path = run_dir / "frames" / f"{frame_id}_{FRONT_CAMERA_SENSOR_ID}{extension}"
    perception_path = run_dir / "perception" / frame_id / "perception.json"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    perception_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(image)
    write_json_atomically(perception_path, frame)
    perception_path.with_suffix(".txt").write_text(
        (frame.get("perception") or {}).get("text", "") + "\n", encoding="utf-8",
    )
    append_recording_frame(
        run_dir, vehicle_id=frame["vehicle_id"], run_id=frame["run_id"],
        generation_id=frame["generation_id"], frame_id=frame_id,
        frame_index=frame["frame_index"], timestamp_ms=frame["captured_at_ms"],
        image_path=image_path, steps=frame["step_activations"], context=frame["context"],
        plugin_sources=sources,
    )


def cycle_frame(result: DecisionCycleResult, *, vehicle_id: str, run_id: str) -> dict[str, Any]:
    """The same completed frame, selections and application on either host."""
    context = result.context
    steps = context.metadata["step_activations"]
    generation = activation_generation_id(
        {step: steps[step] for step in DECISION_STEPS}, prefix="decision",
    )
    return {
        "vehicle_id": vehicle_id, "run_id": run_id,
        "frame_id": context.frame_id, "frame_index": context.frame_index,
        "captured_at_ms": context.sensor_frame.readings[FRONT_CAMERA_SENSOR_ID].captured_at_ms,
        "perception_completed_at_ms": result.completed_at_ms,
        "perception_duration_ms": result.duration_ms,
        "cycle_duration_ms": result.duration_ms,
        "skipped_since_previous": context.metadata.get("skipped_since_previous"),
        "generation_id": generation, "step_activations": steps,
        "context": context.to_dict(), "sensor_frame": context.sensor_frame.to_dict(),
        "perception": result.perception.to_dict() if result.perception else None,
        "observation": result.observation.to_dict() if result.observation else None,
        "memory": result.memory, "control": result.control_record(),
        "decision_cycle": result.to_dict(), "action_policy": context.mode,
    }


class RunRecording:
    """Durable ordered cycles for exactly one run; bounded batches for transport."""

    def __init__(self, root: Path, *, vehicle_id: str, run_id: str | None = None,
                 encode_image: Callable[[Any], bytes] | None = None) -> None:
        self.run_id = run_id or f"automation-{secrets.token_hex(12)}"
        if Path(self.run_id).name != self.run_id or self.run_id in {".", ".."}:
            raise ValueError("recording run_id must be a directory name")
        self.root = Path(root) / self.run_id
        self.vehicle_id = vehicle_id
        self.encode_image = encode_image
        self.count = 0
        self._lock = threading.RLock()
        if (self.root / RECORDING_MANIFEST_NAME).exists():
            raise ValueError("recording run already exists")

    def append(self, result: DecisionCycleResult) -> None:
        context = result.context
        reading = context.sensor_frame.readings[FRONT_CAMERA_SENSOR_ID]
        if reading.path is not None:
            source = Path(reading.path)
            image, extension = source.read_bytes(), source.suffix.lower()
        elif self.encode_image is not None:
            image, extension = self.encode_image(reading.value), ".jpg"
        else:
            raise ValueError("recording requires a camera image or an image encoder")
        frame = cycle_frame(result, vehicle_id=self.vehicle_id, run_id=self.run_id)
        with self._lock:
            if (self.root / "perception" / context.frame_id / "perception.json").exists():
                raise ValueError("recorded frame_id must be unique within the run")
            write_recorded_frame(self.root, frame, image, extension)
            self.count += 1

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {"run_id": self.run_id, "recorded_count": self.count}

    def read(self, *, run_id: str, after: int) -> dict[str, Any]:
        with self._lock:
            if run_id != self.run_id:
                raise ValueError("recording belongs to another run")
            if type(after) is not int or not 0 <= after <= self.count:
                raise ValueError("recording cursor is outside the committed history")
            entries = (
                json.loads((self.root / RECORDING_MANIFEST_NAME).read_text())["frames"]
                if self.count else []
            )
            frames = []
            for entry in entries[after:after + 8]:
                frame_id = entry["frame_id"]
                image_path = self.root / entry["image_path"]
                item = {
                    "frame": json.loads((self.root / "perception" / frame_id / "perception.json").read_text()),
                    "image_base64": base64.b64encode(image_path.read_bytes()).decode("ascii"),
                    "image_extension": image_path.suffix,
                }
                if entry.get("plugin_sources"):
                    item["plugin_sources"] = {
                        original: base64.b64encode(recorded_source_path(self.root, relative).read_bytes()).decode("ascii")
                        for original, relative in entry["plugin_sources"].items()
                    }
                frames.append(item)
            return {**self.status(), "after": after, "frames": frames}
