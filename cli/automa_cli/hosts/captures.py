"""Captures the viewer lists: one row per recorded run, then one frame at a time.

A host records under ``automation/host-runs``. The CLI export writes a second
copy under ``automation/runs``. The list keeps the host copy when both exist.
An active run is still being written, so its frames stay unread.

The run monitor imports the views package, so this reader stays beside the host
record and the viewer command calls it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode

from autonomy.decision_cycle.activation import STEPS
from autonomy.decision_cycle.cycle import CYCLE_RECORD_KEYS
from autonomy.runtime.frame_loop import FRAME_PUBLICATION_SCHEMA
from autonomy.runtime.recording import (
    RECORDING_ENTRIES_FIELD,
    UPLOADED_SOURCES_DIR,
    iter_recording_entries,
    recording_entry,
    recording_generation,
)
from autonomy.runtime.report import REPORT_SCHEMA

from .local import read_host_record
from .runs.models import RunState
from .runs.store import RunRecordError, read_run_state
from ..replay.models import ReplayFrame
from ..replay.source import (
    content_type_for_path,
    manifest_frame_entries,
    read_image_manifest,
    recorded_replay_frame,
)
from ..shared.io.processes import pid_alive
from ..shared.paths import ROOT, runtime_root

_ESCAPE = "capture must stay inside the runtime root"
_NOT_RECORDING = "capture is not a recording"
_ACTIVE = "an active capture is still being written"
_KINDS = frozenset({"host-runs", "runs"})
_OPEN = frozenset({"starting", "running"})
_MANIFESTS_KEPT = 4

# A closed recording never changes, so its list row and its parsed manifest are
# kept until the files' sizes or modification times change.
_closed_rows: dict[Path, tuple[tuple[int, ...], dict[str, object]]] = {}
_manifests: dict[Path, tuple[tuple[int, ...], dict[str, object]]] = {}


def list_captures() -> dict[str, object]:
    """Every recording under the runtime root, newest first, one row per run."""

    root = runtime_root()
    captures: list[dict[str, object]] = []
    if root.is_dir():
        for host in _host_dirs(root):
            captures.extend(_host_captures(root, host))
    captures.sort(key=_newest, reverse=True)
    return {"schema": "automa_captures", "captures": captures}


def capture_metadata(capture: str) -> dict[str, object]:
    """One capture's identity, generations, and which files hold its bytes."""

    run, relative, manifest = _open(capture)
    tally = _Tally(manifest)
    tally.walk(run, manifest)
    active = _is_active(run)
    return {
        "schema": "automa_capture",
        "capture": relative.as_posix(),
        "host": tally.host or relative.parts[0],
        "vehicle": tally.vehicle,
        "run_id": _run_id(manifest, run),
        "format": "entry" if RECORDING_ENTRIES_FIELD in manifest else "inline",
        "state": "active" if active else "closed",
        "viewable": not active,
        "started_at_ms": tally.started,
        "ended_at_ms": tally.ended,
        "duration_ms": tally.duration(),
        "frame_count": tally.count,
        "flagged_frames": tally.flagged,
        "release": _shared_release(tally.generations),
        "generations": tally.generations,
        "sources": _sources(run, tally.pairs),
        "files": [group.to_dict() for group in _groups(run)],
    }


def capture_frame(capture: str, index: str) -> dict[str, object]:
    """One closed frame, in the shape a host's latest frame is served."""

    run, manifest, entry, position = _closed_entry(capture, index)
    frame = recorded_replay_frame(run, manifest, entry, position)
    return _publication(capture, run, manifest, entry, frame=frame, position=position)


def capture_image(capture: str, index: str) -> tuple[bytes, str]:
    """The image a closed frame names. A frame without one is refused."""

    run, manifest, entry, position = _closed_entry(capture, index)
    frame = recorded_replay_frame(run, manifest, entry, position)
    if frame.image_path is None:
        raise ValueError("this frame has no image")
    content_type = frame.content_type or content_type_for_path(frame.image_path)
    return frame.image_path.read_bytes(), content_type


class _Tally:
    """Frame count, times, and generations from one pass over a recording."""

    def __init__(self, manifest: dict[str, object]) -> None:
        self.count = 0
        self.flagged = 0
        self.started: int | None = None
        self.ended: int | None = None
        self.vehicle = _text(manifest.get("vehicle_id"))
        self.host = _text(manifest.get("host_name"))
        self.generations: list[dict[str, object]] = []
        self.pairs: list[tuple[str, str]] = []
        self._seen: set[str | None] = set()

    def walk(self, run: Path, manifest: dict[str, object]) -> None:
        for entry in _frames(run, manifest):
            self.add(run, entry)

    def add(self, run: Path, entry: object) -> None:
        self.count += 1
        if isinstance(entry, dict):
            self._note(run, entry)

    def duration(self) -> int | None:
        if self.started is not None and self.ended is not None and self.ended >= self.started:
            return self.ended - self.started
        return None

    def _note(self, run: Path, entry: dict[str, object]) -> None:
        self._times(entry)
        self._identity(entry)
        self._pairs(entry)
        self._generation(run, entry)

    def _times(self, entry: dict[str, object]) -> None:
        if _flagged(entry):
            self.flagged += 1
        stamp = _millis(entry.get("timestamp_ms"))
        if self.started is None and stamp is not None:
            self.started = stamp
        finish = _millis(entry.get("completed_at_ms"))
        if finish is None:
            finish = stamp
        if finish is not None:
            self.ended = finish

    def _identity(self, entry: dict[str, object]) -> None:
        if self.vehicle is None:
            self.vehicle = _text(entry.get("vehicle_id"))
        if self.host is None:
            self.host = _text(entry.get("host_name"))

    def _pairs(self, entry: dict[str, object]) -> None:
        sources = entry.get("plugin_sources")
        if not isinstance(sources, dict):
            return
        for key, value in sources.items():
            if isinstance(key, str) and isinstance(value, str):
                self.pairs.append((key, value))

    def _generation(self, run: Path, entry: dict[str, object]) -> None:
        token = entry.get("generation_id")
        key = token if isinstance(token, str) else None
        if key in self._seen:
            return
        self._seen.add(key)
        rows, release = _step_rows(_steps_of(run, entry, key))
        self.generations.append({"generation_id": key, "release": release, "steps": rows})


class _Group:
    def __init__(self, name: str, size: int, relative: str) -> None:
        self.name = name
        self.bytes = size
        self.count = 1
        self.largest = relative
        self.largest_bytes = size

    def add(self, size: int, relative: str) -> None:
        self.bytes += size
        self.count += 1
        if size > self.largest_bytes:
            self.largest = relative
            self.largest_bytes = size

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "bytes": self.bytes,
            "count": self.count,
            "largest": self.largest,
            "largest_bytes": self.largest_bytes,
        }


def _host_dirs(root: Path) -> Iterator[Path]:
    for host in sorted(root.iterdir(), key=lambda item: item.name):
        automation = host / "bundle" / "runtime" / "automation"
        if host.is_symlink() or not host.is_dir() or automation.is_symlink() or not automation.is_dir():
            continue
        yield host


def _host_captures(root: Path, host: Path) -> list[dict[str, object]]:
    automation = host / "bundle" / "runtime" / "automation"
    found: list[dict[str, object]] = []
    seen: set[str] = set()
    for kind in ("host-runs", "runs"):
        for run in _run_dirs(automation / kind):
            if run.name in seen:
                continue
            seen.add(run.name)
            item = _safe_summary(root, host.name, run)
            if item is not None:
                found.append(item)
    return found


def _run_dirs(parent: Path) -> Iterator[Path]:
    if parent.is_symlink() or not parent.is_dir():
        return
    for run in sorted(parent.iterdir(), key=lambda item: item.name):
        manifest = run / "manifest.json"
        if run.is_symlink() or not run.is_dir() or manifest.is_symlink() or not manifest.is_file():
            continue
        yield run


def _safe_summary(root: Path, host: str, run: Path) -> dict[str, object] | None:
    try:
        return _summary(root, host, run)
    except (OSError, ValueError):
        return None


def _summary(root: Path, host: str, run: Path) -> dict[str, object]:
    active = _is_active(run)
    signature = _signature(run)
    kept = None if active else _closed_rows.get(run)
    if kept is not None and kept[0] == signature:
        return kept[1]
    row = _tallied_summary(root, host, run, active=active)
    if not active:
        _closed_rows[run] = (signature, row)
    return row


def _signature(run: Path) -> tuple[int, ...]:
    """Sizes and modification times of the files that define a recording."""

    values: list[int] = []
    for path in (run / "manifest.json", *sorted(run.glob("manifest*.jsonl"))):
        info = path.stat()
        values.extend((info.st_mtime_ns, info.st_size))
    return tuple(values)


def _tallied_summary(root: Path, host: str, run: Path, *, active: bool) -> dict[str, object]:
    manifest = _manifest(run)
    tally = _Tally(manifest)
    tally.walk(run, manifest)
    return {
        "capture": _capture_id(root, run),
        "host": tally.host or host,
        "vehicle": tally.vehicle,
        "run_id": _run_id(manifest, run),
        "started_at_ms": tally.started,
        "duration_ms": tally.duration(),
        "frame_count": tally.count,
        "size_bytes": sum(group.bytes for group in _groups(run)),
        "state": "active" if active else "closed",
    }


def _capture_id(root: Path, run: Path) -> str:
    return run.resolve().relative_to(root.resolve()).as_posix()


def _newest(item: dict[str, object]) -> tuple[bool, int, str]:
    started = item.get("started_at_ms")
    run_id = item.get("run_id")
    return (type(started) is int, started if type(started) is int else 0, run_id if isinstance(run_id, str) else "")


def _open(capture: str) -> tuple[Path, PurePosixPath, dict[str, object]]:
    relative = _relative(capture)
    try:
        root = runtime_root().resolve()
    except OSError as exc:
        raise ValueError(_ESCAPE) from exc
    run = _contained(root, relative)
    _require_layout(relative, run)
    return run, relative, _manifest(run)


def _relative(capture: str) -> PurePosixPath:
    if not capture or "\\" in capture or "\x00" in capture or capture.startswith("/"):
        raise ValueError(_ESCAPE)
    path = PurePosixPath(capture)
    if path.is_absolute() or not path.parts or any(part in {".", ".."} for part in path.parts):
        raise ValueError(_ESCAPE)
    return path


def _contained(root: Path, relative: PurePosixPath) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink() or not _stays(current, root):
            raise ValueError(_ESCAPE)
    return current


def _stays(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root)
    except OSError:
        return False


def _require_layout(relative: PurePosixPath, run: Path) -> None:
    parts = relative.parts
    if len(parts) < 3 or parts[-3] != "automation" or parts[-2] not in _KINDS:
        raise ValueError(_NOT_RECORDING)
    manifest = run / "manifest.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError(_NOT_RECORDING)


def _manifest(run: Path) -> dict[str, object]:
    """The parsed manifest, reused while the manifest file is unchanged."""

    info = (run / "manifest.json").stat()
    signature = (info.st_mtime_ns, info.st_size)
    kept = _manifests.get(run)
    if kept is not None and kept[0] == signature:
        return kept[1]
    _path, manifest = read_image_manifest(run)
    if manifest is None:
        raise ValueError(_NOT_RECORDING)
    _manifests.pop(run, None)
    _manifests[run] = (signature, manifest)
    while len(_manifests) > _MANIFESTS_KEPT:
        del _manifests[next(iter(_manifests))]
    return manifest


def _frames(run: Path, manifest: dict[str, object]) -> Iterator[object]:
    if RECORDING_ENTRIES_FIELD in manifest:
        yield from iter_recording_entries(run, manifest)
    else:
        yield from manifest_frame_entries(run, manifest, run / "manifest.json")


def _closed_entry(capture: str, index: str) -> tuple[Path, dict[str, object], dict[str, object], int]:
    run, _relative, manifest = _open(capture)
    if _is_active(run):
        raise ValueError(_ACTIVE)
    position = _index(index)
    return run, manifest, _entry_at(run, manifest, position), position


def _index(text: str) -> int:
    if not text.isascii() or not text.isdigit():
        raise ValueError("index must be a non-negative integer")
    return int(text)


def _entry_at(run: Path, manifest: dict[str, object], index: int) -> dict[str, object]:
    if RECORDING_ENTRIES_FIELD in manifest:
        return recording_entry(run, manifest, index)
    entries = manifest_frame_entries(run, manifest, run / "manifest.json")
    entry = entries[index] if 0 <= index < len(entries) else None
    if isinstance(entry, dict):
        return entry
    raise ValueError("frame index is outside the capture")


def _is_active(run: Path) -> bool:
    state = _state(run.parent.parent)
    if state is None or state.recording is not True or state.status not in _OPEN:
        return False
    return _names_run(state.run_dir, run) and _alive(run.parent.parent, state)


def _state(automation: Path) -> RunState | None:
    try:
        return read_run_state(automation)
    except RunRecordError:
        return None


def _names_run(recorded: str | None, run: Path) -> bool:
    resolved = _resolve_run_dir(recorded)
    try:
        current = None if resolved is None else run.resolve()
    except OSError:
        return False
    if resolved is None or current is None or resolved.name != current.name or resolved.parent.name not in _KINDS:
        return False
    return resolved.parent.parent == current.parent.parent


def _resolve_run_dir(recorded: str | None) -> Path | None:
    if not recorded:
        return None
    candidate = Path(recorded)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    try:
        return candidate.resolve()
    except OSError:
        return None


def _alive(automation: Path, state: RunState) -> bool:
    record = read_host_record(automation)
    pid = state.pid if record is None else record.get("pid")
    try:
        return pid_alive(pid)
    except OverflowError:
        return False


def _steps_of(run: Path, entry: dict[str, object], key: str | None) -> object:
    if key is None:
        return entry.get("steps")
    try:
        return recording_generation(run, key)
    except ValueError:
        return entry.get("steps")


def _step_rows(steps: object) -> tuple[list[dict[str, object]], dict[str, object] | None]:
    if not isinstance(steps, dict):
        return [], None
    rows: list[dict[str, object]] = []
    release = None
    for step in STEPS:
        if step not in steps:
            continue
        payload = steps[step]
        rows.append({"step": step, "plugins": _plugins(payload)})
        if release is None:
            release = _release(payload)
    return rows, release


def _plugins(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    plugins = payload.get("plugins")
    if not isinstance(plugins, list):
        return []
    return [item for item in plugins if isinstance(item, str)]


def _release(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None
    metadata = payload.get("metadata")
    bundle = metadata.get("controller_bundle") if isinstance(metadata, dict) else None
    release = bundle.get("release") if isinstance(bundle, dict) else None
    if isinstance(release, dict):
        return release
    return None


def _shared_release(generations: list[dict[str, object]]) -> dict[str, object] | None:
    found = [release for item in generations if isinstance(release := item.get("release"), dict)]
    if not found or any(item != found[0] for item in found):
        return None
    return found[0]


def _sources(run: Path, pairs: list[tuple[str, str]]) -> list[dict[str, object]]:
    found = _plugin_files(run)
    seen = {str(item["name"]) for item in found}
    for key, value in pairs:
        extra = _pair_source(run, key, value, seen)
        if extra is not None:
            found.append(extra)
    return found


def _plugin_files(run: Path) -> list[dict[str, object]]:
    directory = run / UPLOADED_SOURCES_DIR
    if directory.is_symlink() or not directory.is_dir():
        return []
    sources = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.is_symlink() or not path.is_file():
            continue
        sources.append({"name": path.name, "bytes": path.stat().st_size})
    return sources


def _pair_source(run: Path, key: str, value: str, seen: set[str]) -> dict[str, object] | None:
    stored = _stored_source(run, value)
    name = stored[0] if stored is not None else PurePosixPath(key).name
    if not name or name in {".", ".."} or name in seen:
        return None
    seen.add(name)
    if stored is not None:
        return {"name": name, "bytes": stored[1]}
    return {"name": name, "bytes": len(value.encode("utf-8"))}


def _stored_source(run: Path, value: str) -> tuple[str, int] | None:
    relative = PurePosixPath(value)
    if not value or relative.is_absolute() or any(part in {".", ".."} for part in relative.parts):
        return None
    candidate = run.joinpath(*relative.parts)
    if candidate.is_symlink() or not candidate.is_file():
        return None
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(run.resolve()):
        return None
    return relative.name, resolved.stat().st_size


def _groups(run: Path) -> list[_Group]:
    groups: dict[str, _Group] = {}
    for path in run.rglob("*"):
        _add_file(run, path, groups)
    return sorted(groups.values(), key=lambda group: group.bytes, reverse=True)


def _add_file(run: Path, path: Path, groups: dict[str, _Group]) -> None:
    if path.is_symlink() or not path.is_file():
        return
    relative = path.relative_to(run).as_posix()
    size = path.stat().st_size
    key = relative.split("/", 1)[0]
    group = groups.get(key)
    if group is None:
        groups[key] = _Group(key, size, relative)
        return
    group.add(size, relative)


def _publication(
    capture: str, run: Path, manifest: dict[str, object], entry: dict[str, object], *,
    frame: ReplayFrame, position: int,
) -> dict[str, object]:
    timing = _timing(entry)
    timestamp = _millis(entry.get("timestamp_ms"))
    if timestamp is None:
        timestamp = frame.timestamp_ms
    read_at, age, stale = _clock(entry, timestamp, timing)
    host = _text(manifest.get("host_name")) or _text(entry.get("host_name")) or _host_dir_name(run)
    return {
        "schema": FRAME_PUBLICATION_SCHEMA,
        "host_name": host,
        "health": "healthy",
        "ok": True,
        "read_at_ms": read_at,
        "result_age_ms": age,
        "stale_after_ms": stale,
        "report": _report(
            host, _run_id(manifest, run), entry, frame,
            read_at=read_at, timestamp=timestamp, timing=timing,
        ),
        "image": _image(frame, position, capture, timestamp, read_at),
        "host_telemetry": None,
        "evidence": None,
    }


def _clock(entry: dict[str, object], timestamp: int, timing: dict[str, object] | None) -> tuple[int, int, int]:
    completed = _millis(entry.get("completed_at_ms"))
    read_at = timestamp if completed is None else completed
    duration = None if timing is None else _positive(timing.get("duration_ms"))
    stale = 1000 if duration is None else max(1, int(duration * 2))
    return read_at, max(0, read_at - timestamp), stale


def _report(
    host: str, run_id: str, entry: dict[str, object], frame: ReplayFrame, *,
    read_at: int, timestamp: int, timing: dict[str, object] | None,
) -> dict[str, object]:
    application = entry.get("application")
    return {
        "schema": REPORT_SCHEMA,
        "host_name": host,
        "run_id": run_id,
        "generation_id": _text(entry.get("generation_id")) or "",
        "frame_id": frame.frame_id,
        "frame_index": frame.frame_index,
        "timestamp_ms": timestamp,
        "published_at_ms": read_at,
        "cycle": {key: entry.get(key) for key in CYCLE_RECORD_KEYS},
        "application": application if isinstance(application, dict) else None,
        "values": {"timing": timing},
        "status": _text(entry.get("status")) or "ok",
        "error": _text(entry.get("error")),
    }


def _image(frame: ReplayFrame, position: int, capture: str, captured: int, published: int) -> dict[str, object]:
    url = None
    if frame.image_path is not None:
        url = "/api/captures/image?" + urlencode({"capture": capture, "index": str(position)})
    return {
        "frame_id": frame.frame_id,
        "frame_index": frame.frame_index,
        "captured_at_ms": captured,
        "published_at_ms": published,
        "content_type": frame.content_type,
        "width_px": frame.metadata.get("width_px"),
        "height_px": frame.metadata.get("height_px"),
        "url": url,
    }


def _timing(entry: dict[str, object]) -> dict[str, object] | None:
    timing = entry.get("timing")
    if isinstance(timing, dict):
        return timing
    return None


def _host_dir_name(run: Path) -> str:
    parts = run.parts
    if len(parts) >= 6 and parts[-3] == "automation" and parts[-2] in _KINDS:
        return parts[-6]
    return ""


def _run_id(manifest: dict[str, object], run: Path) -> str:
    return _text(manifest.get("run_id")) or run.name


def _flagged(entry: dict[str, object]) -> bool:
    error = entry.get("error")
    if isinstance(error, str) and error.strip():
        return True
    if entry.get("status") == "error":
        return True
    reason = entry.get("absence_reason")
    return isinstance(reason, str) and bool(reason.strip())


def _text(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _millis(value: object) -> int | None:
    if type(value) is int:
        return value
    return None


def _positive(value: object) -> float | None:
    if type(value) is int and value > 0:
        return float(value)
    if type(value) is float and value > 0:
        return value
    return None
