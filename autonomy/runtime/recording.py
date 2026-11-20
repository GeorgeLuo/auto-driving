"""Completed-cycle recordings shared by local and onboard hosts.

The host commits a cycle and its exact image before counting the decision or
ending the run. Live latest-only publications are independent of this history.
``manifest.json`` is a header written once. Each frame is appended as one JSON
line in the entries file that header names, and a commit does not read or
rewrite earlier entries. A final line with no newline was never committed. A
manifest that carries an inline ``frames`` list, each with its own ``steps``,
still loads.
A frame line names the generation the host applied and holds none of its step
selections. The recording stores each generation once, as
``generations/<generation_id>.json``, before the first line that names it. A
display-only change, such as a renamed preset, keeps the generation, so the
stored selections keep the labels of its first frame.
Each manifest entry carries a control application change: the number of records
kept from the end of the previous window, followed by added records. Record
order, not wall-clock order, preserves equal or backwards timestamps. Replay
restores the recorded window independently of the runtime limit.
Each recorded step names the controller release the host imported, which is
the code replay must run; a host outside a release records none. A plugin
uploaded to the host's catalog is outside every release, so the recording keeps
each uploaded source its generations name, as ``plugins/<sha256>.py``.
The host recording and the CLI export each write their own directory through
one ``RecordingWriter``.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import secrets
import threading
from collections.abc import Callable, Iterator, Mapping
from functools import lru_cache
from pathlib import Path

from autonomy.decision_cycle.activation import (
    STEPS, StepActivation, activation_generation_id, require_step,
    step_activation_from_payload,
)
from autonomy.decision_cycle.context import recorded_context
from autonomy.decision_cycle.action.publication import (
    read_mode, read_user_steering, read_user_throttle,
)
from autonomy.decision_cycle.cycle import DecisionCycleResult
from autonomy.fields import FieldError, require_nested
from autonomy.plugins import uploaded_source
from autonomy.runtime.execution import ControlApplication
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID

RECORDING_MANIFEST_SCHEMA = "automa_recording_manifest"
RECORDING_MANIFEST_NAME = "manifest.json"
# One JSON object per line, beside the header. Not the ``frames/`` image directory.
RECORDING_ENTRIES_NAME = "manifest.jsonl"
# Header field that names the entries file. An inline ``frames`` list has no such field.
RECORDING_ENTRIES_FIELD = "entries"
# The step selections of each generation, as ``<generation_id>.json``.
GENERATIONS_DIR = "generations"
# Uploaded plugin sources a recording carries, as ``<sha256>.py``.
UPLOADED_SOURCES_DIR = "plugins"
# Manifest entry change from the previous published application window.
CONTROL_APPLICATION_CHANGE_FIELD = "control_application_change"
TRANSPORT_BATCH = 8


def control_application_windows(changes: list[object]) -> list[tuple[ControlApplication, ...] | None]:
    """Decode manifest changes in order; missing windows break retained history."""
    windows: list[tuple[ControlApplication, ...] | None] = []
    previous: tuple[ControlApplication, ...] = ()
    for position, change in enumerate(changes):
        if change is None:
            windows.append(None)
            previous = ()
            continue
        try:
            if not isinstance(change, dict) or set(change) != {"kept", "added"}:
                raise ValueError("expected an object with kept and added")
            kept, added = change["kept"], change["added"]
            if type(kept) is not int or not 0 <= kept <= len(previous):
                raise ValueError("kept must be a nonnegative integer within the previous window")
            if not isinstance(added, list):
                raise ValueError("added must be a list")
            retained = previous[-kept:] if kept else ()
            previous = retained + tuple(
                require_nested(ControlApplication.from_dict, item,
                               field_name=f"frames.{position}.control_application_change.added.{index}")
                for index, item in enumerate(added)
            )
        except FieldError:
            raise
        except (TypeError, ValueError) as exc:
            raise ValueError(f"frame {position} has invalid control applications: {exc}") from exc
        windows.append(previous)
    return windows


def _installed_release() -> dict[str, object] | None:
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


def write_json_atomically(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def recorded_selections(record: Mapping[str, object]) -> dict[str, StepActivation | None]:
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
        if not all(step in activations for step in STEPS):
            raise ValueError("recorded generation requires every cycle step")
        expected = activation_generation_id(
            {step: activations[step] for step in STEPS}, prefix="decision",
        )
        if generation != expected:
            raise ValueError("recorded generation does not match its step selections")
    return activations


def _recording_text(value: object, label: str) -> str:
    if type(value) is not str or not value or any(char.isspace() for char in value):
        raise ValueError(f"{label} must be a nonempty string")
    return value


def recording_frame_entry(
    *, frame_id: str, timestamp_ms: int, image_path: str | None,
    steps: Mapping[str, object], frame_index: object = None,
    context: object = None,
    control_application_change: object = None,
    mode: object = None, user_steering: object = None,
    user_throttle: object = None, absence_reason: str | None = None,
) -> dict[str, object]:
    """The inputs needed to replay one cycle; image paths are relative to its archive."""

    entry: dict[str, object] = {
        "frame_id": frame_id,
        "timestamp_ms": timestamp_ms,
        "image_path": image_path,
        "steps": copy.deepcopy(steps),
    }
    if frame_index is not None:
        entry["frame_index"] = frame_index
    for key, value in (("mode", mode), ("user_steering", user_steering), ("user_throttle", user_throttle)):
        if value is not None:
            entry[key] = value
    if context is not None:
        entry["context"] = recorded_context(context)
    if control_application_change is not None:
        entry[CONTROL_APPLICATION_CHANGE_FIELD] = copy.deepcopy(control_application_change)
    if absence_reason is not None:
        entry["absence_reason"] = absence_reason
    return entry


def append_recording_entry(path: Path, entry: Mapping[str, object]) -> int:
    """Append one committed frame as a single JSON line; return its length in bytes.

    The line's trailing newline is the commit. A write that does not finish
    is discarded, and a reader ignores a trailing line with no newline, so
    earlier frames stay readable.
    """

    line = (json.dumps(entry) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        end = os.lseek(descriptor, 0, os.SEEK_END)
        complete = False
        try:
            complete = os.write(descriptor, line) == len(line)
        finally:
            if not complete:
                os.ftruncate(descriptor, end)
        if not complete:
            raise OSError(f"could not append a recording entry to {path.name}")
    finally:
        os.close(descriptor)
    return len(line)


def _entries_path(root: Path, name: object) -> Path:
    if type(name) is not str or not name or name != Path(name).name or name in {".", ".."}:
        raise ValueError("recording entries must name a file beside the manifest")
    path = Path(root) / name
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("recording entries must be a regular file")
    return path


def _entry_object(line: bytes, index: int) -> dict[str, object]:
    try:
        item = json.loads(line)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"recording entry {index} is not a JSON object") from exc
    if not isinstance(item, dict):
        raise ValueError(f"recording entry {index} must be an object")
    return item


def iter_recording_entries(root: Path, manifest: Mapping[str, object]) -> Iterator[dict[str, object]]:
    """Read the entries committed when this read starts, one line at a time."""

    path = _entries_path(root, manifest.get(RECORDING_ENTRIES_FIELD))
    try:
        with path.open("rb") as stream:
            remaining = os.fstat(stream.fileno()).st_size
            index = 0
            while remaining:
                line = stream.readline(remaining)
                if not line.endswith(b"\n"):
                    break
                yield _entry_object(line, index)
                remaining -= len(line)
                index += 1
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ValueError(f"recording entries could not be read: {exc}") from exc


@lru_cache(maxsize=16)
def _entry_offsets(path: Path, identity: tuple[int, ...]) -> tuple[int, ...]:
    """Index a closed recording without keeping any frame bodies in memory."""

    with path.open("rb") as stream:
        size = identity[-1]
        offsets = []
        offset = 0
        while offset < size:
            line = stream.readline(size - offset)
            if not line.endswith(b"\n"):
                break
            offsets.append(offset)
            offset += len(line)
    return tuple(offsets)


def recording_entry(root: Path, manifest: Mapping[str, object], index: int) -> dict[str, object]:
    """Read one frame of a closed entry file by its zero-based position."""

    path = _entries_path(root, manifest.get(RECORDING_ENTRIES_FIELD))
    info = path.stat() if path.exists() else None
    offsets = () if info is None else _entry_offsets(path, (
        info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size,
    ))
    if not 0 <= index < len(offsets):
        raise ValueError("frame index is outside the capture")
    with path.open("rb") as stream:
        stream.seek(offsets[index])
        return _entry_object(stream.readline(), index)


def recording_entries(root: Path, manifest: Mapping[str, object]) -> list[object]:
    """Committed entries of an append-only run recording, in record order."""

    return list(iter_recording_entries(root, manifest))


def uploaded_sources(steps: Mapping[str, object]) -> set[str]:
    """The uploaded source files, ``<sha256>.py``, these step selections name."""

    return {
        module
        for payload in steps.values() if payload is not None
        for spec in payload["plugin_specs"].values()
        if (module := spec.partition(":")[0]).endswith(".py") and not Path(module).is_absolute()
    }


def entry_selections(
    root: Path, manifest: Mapping[str, object], entries: list[object],
) -> list[dict[str, StepActivation | None]]:
    """Each entry's step selections, read and validated once per generation.

    An appended entry names a stored generation, and every frame of that
    generation shares one selections mapping. An inline entry carries its own
    ``steps``; one without them selects nothing.
    """

    if RECORDING_ENTRIES_FIELD not in manifest:
        return [recorded_selections(entry) if isinstance(entry, dict) else {} for entry in entries]
    generations: dict[object, dict[str, StepActivation | None]] = {}
    selections = []
    for entry in entries:
        generation_id = entry.get("generation_id") if isinstance(entry, dict) else None
        if generation_id not in generations:
            generations[generation_id] = _stored_generation(root, generation_id)
        selections.append(generations[generation_id])
    return selections


def recording_generation(root: Path, generation_id: object) -> dict[str, object]:
    """Read a generation's stored selections without loading its plugins."""

    if type(generation_id) is not str or Path(generation_id).name != generation_id:
        raise ValueError("a recorded frame must name its generation")
    directory = root / GENERATIONS_DIR
    path = directory / f"{generation_id}.json"
    if directory.is_symlink() or path.is_symlink() or not path.is_file():
        raise ValueError(f"recorded generation {generation_id} must be a regular file")
    try:
        steps = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"recorded generation {generation_id} could not be read: {exc}") from exc
    if not isinstance(steps, dict):
        raise ValueError("recorded steps must be an object")
    return steps


def _stored_generation(root: Path, generation_id: object) -> dict[str, StepActivation | None]:
    steps = recording_generation(root, generation_id)
    return recorded_selections({"steps": steps, "generation_id": generation_id})


def _require_frame_file_name(frame_id: object) -> str:
    if not isinstance(frame_id, str) or not frame_id or Path(frame_id).name != frame_id or frame_id in {".", ".."}:
        raise ValueError("recorded frame_id must be a filename")
    return frame_id


class RecordingWriter:
    """Writes one new run directory: each generation once, then a line per frame.

    The host recording and the CLI export each own one, so both directories
    hold the same files. ``offsets`` holds where each committed line starts.
    """

    def __init__(self, root: Path, *, host_name: str, run_id: str) -> None:
        self.root = Path(root)
        self.host_name = _recording_text(host_name, "host_name")
        self.run_id = _recording_text(run_id, "run_id")
        if (self.root / RECORDING_MANIFEST_NAME).exists():
            raise ValueError("recording run already exists")
        self.offsets: list[int] = []
        self.size = 0
        self._frame_ids: set[str] = set()
        self._generations: set[object] = set()

    def store_generation(
        self, generation_id: object, steps: object, read_source: Callable[[str], bytes | None],
    ) -> None:
        """Write a generation and the uploaded sources it names, unless the run holds it.

        ``steps`` must hash to ``generation_id``, and each source to its name.
        """

        if generation_id in self._generations:
            return
        generation_id = _recording_text(generation_id, "generation_id")
        if type(steps) is not dict or set(steps) != set(STEPS):
            raise ValueError("recording requires the applied selections of every cycle step")
        recorded_selections({"steps": steps, "generation_id": generation_id})
        for name in sorted(uploaded_sources(steps)):
            source = read_source(name)
            if source is None or f"{hashlib.sha256(source).hexdigest()}.py" != name:
                raise ValueError(f"recorded generation {generation_id} ran uploaded source {name} without carrying it")
            (self.root / UPLOADED_SOURCES_DIR).mkdir(parents=True, exist_ok=True)
            (self.root / UPLOADED_SOURCES_DIR / name).write_bytes(source)
        (self.root / GENERATIONS_DIR).mkdir(parents=True, exist_ok=True)
        write_json_atomically(self.root / GENERATIONS_DIR / f"{generation_id}.json", steps)
        self._generations.add(generation_id)

    def commit(self, frame: Mapping[str, object], image: bytes, extension: str) -> None:
        """Commit one frame of a stored generation and its camera image.

        Every check runs before the frame writes anything. The first commit
        writes the header. The line is durable when this returns.
        """

        frame_id = _require_frame_file_name(frame.get("frame_id"))
        if frame_id in self._frame_ids:
            raise ValueError("recorded frame_id must be unique within the run")
        if frame.get("host_name") != self.host_name or frame.get("run_id") != self.run_id:
            raise ValueError("recorded frame belongs to another run or vehicle")
        if frame.get("generation_id") not in self._generations:
            raise ValueError("recorded frame names a generation the run has not stored")
        if extension not in {".jpg", ".jpeg", ".png"} or not image:
            raise ValueError("recorded camera image is missing or has an unsupported extension")
        image_path = f"frames/{frame_id}_{FRONT_CAMERA_SENSOR_ID}{extension}"
        (self.root / "frames").mkdir(parents=True, exist_ok=True)
        if not self.offsets:
            write_json_atomically(self.root / RECORDING_MANIFEST_NAME, {
                "schema": RECORDING_MANIFEST_SCHEMA,
                "host_name": self.host_name,
                "run_id": self.run_id,
                RECORDING_ENTRIES_FIELD: RECORDING_ENTRIES_NAME,
            })
        (self.root / image_path).write_bytes(image)
        start = self.size
        self.size += append_recording_entry(self.root / RECORDING_ENTRIES_NAME, {**frame, "image_path": image_path})
        self.offsets.append(start)
        self._frame_ids.add(frame_id)


def cycle_frame(
    result: DecisionCycleResult, *, host_name: str, run_id: str,
    status: str = "ok", error: str | None = None,
) -> dict[str, object]:
    """One completed frame's line: its outputs, inputs, application and generation.

    The generation is the one the host applied. The line holds none of its
    step selections; the recording stores those once.
    """

    context = result.context
    generation = context.metadata["generation_id"]
    if not isinstance(generation, str) or not generation:
        raise ValueError("cycle frame requires the applied generation_id")
    application = None
    if result.application is not None:
        control = result.application.control
        application = {
            "applied": result.application.applied,
            "mode": result.application.mode,
            "reason": result.application.reason,
            "steering": control.steering,
            "throttle": control.throttle,
        }
    frame: dict[str, object] = {
        "host_name": host_name, "run_id": run_id, "generation_id": generation,
        "frame_id": context.frame_id, "frame_index": context.frame_index,
        "timestamp_ms": context.sensor_frame.readings[FRONT_CAMERA_SENSOR_ID].captured_at_ms,
        "completed_at_ms": result.completed_at_ms,
        "timing": result.timing,
        "context": {
            "timestamp_ms": context.timestamp_ms,
            "metadata": {key: value for key, value in context.metadata.items() if key != "step_activations"},
        },
        "status": status,
        "error": error,
        "application": application,
        **result.export_cycle(),
        "control": result.control_record(),
    }
    for key, value in (
        ("mode", read_mode(context.shared_memory)),
        ("user_steering", read_user_steering(context.shared_memory)),
        ("user_throttle", read_user_throttle(context.shared_memory)),
    ):
        if value is not None:
            frame[key] = value
    return frame


def host_runs_dir(runtime_root: Path) -> Path:
    """Where a vehicle host records its runs.

    The CLI exports recordings under ``automation/runs``. The host's own
    history stays separate, even when both processes are local.
    """
    return Path(runtime_root) / "automation" / "host-runs"


class RunRecording:
    """Durable ordered cycles for exactly one run; bounded batches for transport."""

    def __init__(self, root: Path, *, host_name: str, run_id: str | None = None,
                 encode_image: Callable[[object], bytes] | None = None) -> None:
        self.run_id = run_id or f"automation-{secrets.token_hex(12)}"
        if Path(self.run_id).name != self.run_id or self.run_id in {".", ".."}:
            raise ValueError("recording run_id must be a directory name")
        self.root = Path(root) / self.run_id
        self.host_name = host_name
        self.encode_image = encode_image
        self.writer = RecordingWriter(self.root, host_name=host_name, run_id=self.run_id)
        self._lock = threading.RLock()
        self._previous_applications = ()

    @property
    def count(self) -> int:
        """Committed frames; a frame counts once its line is durable."""
        return len(self.writer.offsets)

    def append(
        self, result: DecisionCycleResult,
        control_applications: tuple[ControlApplication, ...] | None = None,
        *,
        status: str = "ok",
        error: str | None = None,
    ) -> None:
        """Record a cycle and the applications the host published before it.

        ``None`` means the host published none, so the entry records none.
        """
        context = result.context
        reading = context.sensor_frame.readings[FRONT_CAMERA_SENSOR_ID]
        if reading.path is not None:
            source = Path(reading.path)
            image, extension = source.read_bytes(), source.suffix.lower()
        elif self.encode_image is not None:
            image, extension = self.encode_image(reading.value), ".jpg"
        else:
            raise ValueError("recording requires a camera image or an image encoder")
        frame = cycle_frame(
            result, host_name=self.host_name, run_id=self.run_id,
            status=status, error=error,
        )
        with self._lock:
            applications = control_applications or ()
            previous = self._previous_applications
            if control_applications is not None:
                # Use record identity and order, never wall-clock time: equal-ms
                # writes and backwards clock steps must still be recorded once.
                cursor = next((index + 1 for index in range(len(applications) - 1, -1, -1)
                               if previous and applications[index] is previous[-1]), None)
                kept = cursor or 0
                frame[CONTROL_APPLICATION_CHANGE_FIELD] = {
                    "kept": kept, "added": [item.to_dict() for item in applications[kept:]],
                }
            self.writer.store_generation(
                context.metadata["generation_id"], context.metadata["step_activations"],
                lambda name: uploaded_source(name).read_bytes(),
            )
            self.writer.commit(frame, image, extension)
            self._previous_applications = applications

    def status(self) -> dict[str, object]:
        # Count is published after append commits; export must not block status.
        return {"run_id": self.run_id, "recorded_count": self.count}

    def read(self, *, run_id: str, after: int) -> dict[str, object]:
        """Up to a batch of committed frames, and the generations and sources they name."""
        with self._lock:
            if run_id != self.run_id:
                raise ValueError("recording belongs to another run")
            recorded_count = self.count
            if type(after) is not int or not 0 <= after <= recorded_count:
                raise ValueError("recording cursor is outside the committed history")
            stop = min(after + TRANSPORT_BATCH, recorded_count)
            start = self.writer.offsets[after] if after < recorded_count else self.writer.size
            end = self.writer.offsets[stop] if stop < recorded_count else self.writer.size
        # Committed lines and files never change, so they are read without the lock.
        frames = _committed_lines(self.root / RECORDING_ENTRIES_NAME, start, end)
        generations = {
            generation_id: json.loads((self.root / GENERATIONS_DIR / f"{generation_id}.json").read_text(encoding="utf-8"))
            for generation_id in {frame["generation_id"] for frame in frames}
        }
        names = sorted({name for steps in generations.values() for name in uploaded_sources(steps)})
        return {
            "run_id": self.run_id, "recorded_count": recorded_count, "after": after,
            "frames": [{
                "frame": frame,
                "image_base64": _base64(self.root / frame["image_path"]),
                "image_extension": Path(frame["image_path"]).suffix,
            } for frame in frames],
            "generations": generations,
            "sources": {name: _base64(self.root / UPLOADED_SOURCES_DIR / name) for name in names},
        }


def _committed_lines(path: Path, start: int, end: int) -> list[dict[str, object]]:
    if start == end:
        return []
    with path.open("rb") as entries:
        entries.seek(start)
        return [json.loads(line) for line in entries.read(end - start).splitlines()]


def _base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")
