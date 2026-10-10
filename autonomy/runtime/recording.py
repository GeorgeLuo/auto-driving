"""Completed-cycle recordings shared by local and onboard hosts.

The host commits a cycle and its exact image before counting the decision or
ending the run. Live latest-only publications are independent of this history.
Each recorded step names the controller release the host imported, which is
the code replay must run; a host outside a release records none. A plugin
uploaded to the host's catalog is outside every release, so the recording keeps
each uploaded source its steps name, as ``plugins/<sha256>.py``.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import secrets
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping

from autonomy.decision_cycle.activation import (
    DECISION_STEPS, STEPS, StepActivation, activation_generation_id, require_step,
    step_activation_from_payload,
)
from autonomy.decision_cycle.cycle import DecisionCycleResult
from autonomy.plugins import uploaded_source
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID

RECORDING_MANIFEST_SCHEMA = "automa_recording_manifest_v0"
RECORDING_MANIFEST_NAME = "manifest.json"
# Uploaded plugin sources a recording carries, as ``<sha256>.py``.
UPLOADED_SOURCES_DIR = "plugins"


def _installed_release() -> dict[str, Any] | None:
    # Resolved at import: a later deploy may relink the package to another release.
    try:
        manifest = json.loads(
            (Path(__file__).resolve().parents[2] / "bundle-manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    tree_sha256 = manifest.get("tree_sha256") if isinstance(manifest, dict) else None
    if not isinstance(tree_sha256, str):
        return None
    return {"tree_sha256": tree_sha256, "created_at_ms": manifest.get("created_at_ms")}


INSTALLED_RELEASE = _installed_release()


def write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def recorded_selections(record: dict[str, Any], *, source_root: Path | None = None) -> dict[str, StepActivation | None]:
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
    # Earlier recordings mapped the host's absolute upload paths to retained
    # assets. Resolve those at the input boundary; all runners use one loader.
    if source_root is not None and record.get("plugin_sources"):
        sources = {
            original: str(recorded_source_path(source_root, relative))
            for original, relative in record["plugin_sources"].items()
        }
        for step, activation in activations.items():
            if activation is not None:
                referenced = {
                    path: sources[path] for spec in activation.plugin_specs.values()
                    if (path := spec.partition(":")[0]) in sources
                }
                if referenced:
                    activations[step] = replace(
                        activation, metadata={**activation.metadata, "plugin_sources": referenced},
                    )
    return activations


def recorded_source_path(run_dir: Path, relative: str) -> Path:
    """Resolve a retained source inside a recording, including after moving it."""
    root = Path(run_dir).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("recorded plugin source must stay inside the run directory")
    return path


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


def uploaded_sources(steps: dict[str, Any]) -> set[str]:
    """The uploaded source files, ``<sha256>.py``, these step selections name."""

    return {
        module
        for payload in steps.values() if payload is not None
        for spec in payload["plugin_specs"].values()
        if (module := spec.partition(":")[0]).endswith(".py") and not Path(module).is_absolute()
    }


def write_recorded_frame(
    run_dir: Path, frame: dict[str, Any], image: bytes, extension: str,
    sources: Mapping[str, bytes],
) -> None:
    """Commit the same replay artifacts locally or from an onboard transport.

    ``sources`` holds each uploaded source the frame names that the run
    directory does not have yet.
    """
    frame_id = frame["frame_id"]
    if not frame_id or Path(frame_id).name != frame_id or frame_id in {".", ".."}:
        raise ValueError("recorded frame_id must be a filename")
    if extension not in {".jpg", ".jpeg", ".png"} or not image:
        raise ValueError("recorded camera image is missing or has an unsupported extension")
    for name in sorted(uploaded_sources(frame["step_activations"])):
        path = run_dir / UPLOADED_SOURCES_DIR / name
        if path.is_file():
            continue
        source = sources.get(name)
        if source is None or f"{hashlib.sha256(source).hexdigest()}.py" != name:
            raise ValueError(f"recorded frame {frame_id} ran uploaded source {name} without carrying it")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source)
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
    )


def ran_on_release(
    steps: dict[str, Any], *, vehicle_id: str, release: dict[str, Any] | None,
) -> dict[str, Any]:
    """Each step payload naming the vehicle and the release its code came from."""
    stamped: dict[str, Any] = {}
    for step, payload in steps.items():
        if payload is None:
            stamped[step] = None
            continue
        metadata = copy.deepcopy(dict(payload.get("metadata") or {}))
        metadata.setdefault("vehicle_id", vehicle_id)
        metadata["controller_bundle"] = {
            **dict(metadata.get("controller_bundle") or {}), "release": copy.deepcopy(release),
        }
        stamped[step] = {**payload, "metadata": metadata}
    return stamped


def cycle_frame(result: DecisionCycleResult, *, vehicle_id: str, run_id: str) -> dict[str, Any]:
    """The same completed frame, selections and application on either host."""
    context = result.context
    steps = ran_on_release(
        context.metadata["step_activations"], vehicle_id=vehicle_id, release=INSTALLED_RELEASE,
    )
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
            sources = {
                name: uploaded_source(name).read_bytes()
                for name in uploaded_sources(frame["step_activations"])
                if not (self.root / UPLOADED_SOURCES_DIR / name).is_file()
            }
            write_recorded_frame(self.root, frame, image, extension, sources)
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
            names: set[str] = set()
            for entry in entries[after:after + 8]:
                frame_id = entry["frame_id"]
                image_path = self.root / entry["image_path"]
                frame = json.loads((self.root / "perception" / frame_id / "perception.json").read_text())
                names |= uploaded_sources(frame["step_activations"])
                frames.append({
                    "frame": frame,
                    "image_base64": base64.b64encode(image_path.read_bytes()).decode("ascii"),
                    "image_extension": image_path.suffix,
                })
            sources = {
                name: base64.b64encode((self.root / UPLOADED_SOURCES_DIR / name).read_bytes()).decode("ascii")
                for name in sorted(names)
            }
            return {**self.status(), "after": after, "frames": frames, "sources": sources}
