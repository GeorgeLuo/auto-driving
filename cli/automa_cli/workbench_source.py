"""Bounded image-directory input for the perception-memory workbench."""

from __future__ import annotations

import copy
import hashlib
import json
import mimetypes
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


WORKBENCH_ADAPTER = "image_directory"
WORKBENCH_DEFAULT_MAX_FRAMES = 256
WORKBENCH_DEFAULT_MAX_IMAGE_BYTES = 32 * 1024 * 1024
WORKBENCH_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
WORKBENCH_UNSUPPORTED_IMAGE_EXTENSIONS = {
    ".gif",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
    ".avif",
}

_MANIFEST_NAMES = ("manifest.json", "run.json")
_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class SourceValidationError(ValueError):
    """A user-visible source contract failure."""

    boundary = "source"


@dataclass(frozen=True)
class ReplayFrame:
    source_id: str
    frame_id: str
    frame_index: int
    position: int
    timestamp_ms: int
    image_path: Path | None
    absence_reason: str | None = None
    content_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def absent(self) -> bool:
        return self.absence_reason is not None or self.image_path is None

    def to_dict(self, *, include_path: bool = True) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "frame_id": self.frame_id,
            "frame_index": self.frame_index,
            "position": self.position,
            "timestamp_ms": self.timestamp_ms,
            "image_path": str(self.image_path)
            if include_path and self.image_path
            else None,
            "image_name": self.image_path.name if self.image_path else None,
            "absence_reason": self.absence_reason,
            "absent": self.absent,
            "content_type": self.content_type,
            "metadata": copy.deepcopy(self.metadata),
        }


@dataclass(frozen=True)
class ImageFeed:
    source_path: Path
    source_id: str
    frames: tuple[ReplayFrame, ...]
    manifest_path: Path | None = None
    adapter: str = WORKBENCH_ADAPTER

    def to_dict(self) -> dict[str, Any]:
        """Return the public source summary, not the internal frame inventory."""

        return {
            "adapter": self.adapter,
            "source_path": str(self.source_path),
            "source_id": self.source_id,
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "frame_count": len(self.frames),
        }


def normalize_image_directory(
    source_dir: str | os.PathLike[str],
    *,
    source_root: Path | None = None,
    max_frames: int = WORKBENCH_DEFAULT_MAX_FRAMES,
    max_image_bytes: int = WORKBENCH_DEFAULT_MAX_IMAGE_BYTES,
) -> ImageFeed:
    """Validate and normalize one location-independent image directory."""

    if not isinstance(source_dir, (str, os.PathLike)):
        raise SourceValidationError("source_dir must be a path string")
    raw_source = os.fspath(source_dir)
    if "\x00" in raw_source:
        raise SourceValidationError("source_dir contains a NUL byte")
    if max_frames <= 0:
        raise SourceValidationError("max_frames must be greater than zero")
    if max_image_bytes <= 0:
        raise SourceValidationError("max_image_bytes must be greater than zero")

    root = Path(source_root).expanduser().resolve() if source_root is not None else None
    candidate = Path(raw_source).expanduser()
    if root is not None and not candidate.is_absolute():
        candidate = root / candidate
    source_path = candidate.resolve()
    if root is not None and not _is_within(source_path, root):
        raise SourceValidationError(
            "source_dir must remain inside the configured source root"
        )
    if candidate.is_symlink() or source_path.is_symlink():
        raise SourceValidationError("source_dir may not be a symlink")
    if not source_path.exists():
        raise SourceValidationError(f"source directory does not exist: {source_path}")
    if not source_path.is_dir():
        raise SourceValidationError(f"source path is not a directory: {source_path}")

    manifest_path, manifest = _read_manifest(source_path)
    if manifest is not None:
        entries = manifest.get("frames")
        if not isinstance(entries, list):
            raise SourceValidationError(
                f"manifest {manifest_path.name} frames must be a list"
            )
    else:
        paths = _lexical_image_paths(source_path)
        if not paths:
            raise SourceValidationError(
                f"no supported images found under {source_path}"
            )
        entries = [{"image_path": str(path.relative_to(source_path))} for path in paths]
    if not entries:
        raise SourceValidationError("source manifest contains no frames")
    if len(entries) > max_frames:
        raise SourceValidationError(
            f"source contains {len(entries)} frames; max_frames is {max_frames}"
        )

    source_id = _source_id(source_path, manifest)
    recorded_root = _recorded_root(manifest)
    recorded_source = (
        _recorded_source_mapping(manifest, source_path, recorded_root)
        if manifest_path is not None and manifest_path.name == "run.json"
        else None
    )
    if (
        root is not None
        and recorded_source is not None
        and not _is_within(recorded_source[1], root)
    ):
        raise SourceValidationError(
            "manifest source must remain inside the configured source root"
        )
    frames = tuple(
        _build_frame(
            source_id=source_id,
            position=position,
            entry=entry,
            source_path=source_path,
            recorded_root=recorded_root,
            recorded_source=recorded_source,
            max_image_bytes=max_image_bytes,
        )
        for position, entry in enumerate(entries)
    )
    _validate_frame_sequence(frames)
    return ImageFeed(
        source_path=source_path,
        source_id=source_id,
        frames=frames,
        manifest_path=manifest_path,
    )


def content_type_for_path(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


load_image_feed = normalize_image_directory


def _read_manifest(source_path: Path) -> tuple[Path | None, dict[str, Any] | None]:
    for name in _MANIFEST_NAMES:
        path = source_path / name
        if not os.path.lexists(path):
            continue
        if path.is_symlink() or not path.is_file():
            raise SourceValidationError(f"manifest is not a regular file: {path.name}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SourceValidationError(
                f"could not read manifest {path.name}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise SourceValidationError(
                f"manifest {path.name} must contain a JSON object"
            )
        return path, payload
    return None, None


def _source_id(source_path: Path, manifest: dict[str, Any] | None) -> str:
    declared = None
    if manifest is not None:
        declared = manifest.get("source_id") or manifest.get("run_id")
    if declared is not None:
        if not isinstance(declared, str):
            raise SourceValidationError("source_id must be a string")
        value = declared.strip()
        if not _SOURCE_ID_RE.fullmatch(value):
            raise SourceValidationError(
                "source_id must contain only letters, numbers, underscore, dot, colon, or hyphen"
            )
        return value
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", source_path.name).strip("-") or "source"
    digest = hashlib.sha256(str(source_path).encode("utf-8")).hexdigest()[:12]
    return f"image-directory:{name[:80]}:{digest}"


def _recorded_root(manifest: dict[str, Any] | None) -> Path | None:
    if manifest is None or not isinstance(manifest.get("run_dir"), str):
        return None
    value = manifest["run_dir"].strip()
    return Path(value).expanduser() if value else None


def _recorded_source_mapping(
    manifest: dict[str, Any] | None,
    source_path: Path,
    recorded_root: Path | None,
) -> tuple[Path, Path] | None:
    source = manifest.get("source") if manifest is not None else None
    value = source.get("path") if isinstance(source, dict) else None
    if not isinstance(value, str) or not value.strip():
        return None
    recorded = Path(value).expanduser()
    if recorded.exists():
        return recorded, recorded.resolve()
    repository_root = _relocated_repository_root(source_path, recorded_root)
    if repository_root is None:
        return None
    current = _existing_source_suffix(recorded, repository_root)
    return (recorded, current) if current is not None else None


def _relocated_repository_root(
    source_path: Path,
    recorded_root: Path | None,
) -> Path | None:
    if recorded_root is None or recorded_root.is_absolute():
        return None
    current = source_path
    for part in reversed(recorded_root.parts):
        if current.name != part:
            return None
        current = current.parent
    return current


def _existing_source_suffix(recorded: Path, repository_root: Path) -> Path | None:
    if not recorded.is_absolute():
        candidate = repository_root / recorded
        return candidate.resolve() if candidate.exists() else None
    for start in range(1, len(recorded.parts)):
        candidate = repository_root.joinpath(*recorded.parts[start:])
        if candidate.exists():
            return candidate.resolve()
    return None


def _lexical_image_paths(source_path: Path) -> list[Path]:
    def supported_files(directory: Path) -> list[Path]:
        paths: list[Path] = []
        for path in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            if path.name in _MANIFEST_NAMES:
                continue
            suffix = path.suffix.lower()
            if suffix in WORKBENCH_UNSUPPORTED_IMAGE_EXTENSIONS:
                raise SourceValidationError(f"unsupported image format: {path.name}")
            if suffix not in WORKBENCH_IMAGE_EXTENSIONS:
                continue
            if path.is_symlink():
                raise SourceValidationError(f"image may not be a symlink: {path.name}")
            if path.is_file():
                paths.append(path)
        return paths

    paths = supported_files(source_path)
    if paths:
        return paths
    frames_dir = source_path / "frames"
    if frames_dir.exists():
        if frames_dir.is_symlink() or not frames_dir.is_dir():
            raise SourceValidationError(
                "source frames path must be a regular directory"
            )
        return supported_files(frames_dir)
    return []


def _build_frame(
    *,
    source_id: str,
    position: int,
    entry: Any,
    source_path: Path,
    recorded_root: Path | None,
    recorded_source: tuple[Path, Path] | None,
    max_image_bytes: int,
) -> ReplayFrame:
    if isinstance(entry, str):
        entry = {"image_path": entry}
    if not isinstance(entry, dict):
        raise SourceValidationError(
            f"manifest frame {position} must be a string or object"
        )

    absence_reason = _absence_reason(entry)
    image_path: Path | None = None
    image_metadata: dict[str, Any] = {}
    raw_image = entry.get("image_path")
    if raw_image is not None and str(raw_image).strip():
        image_path = _resolve_image_path(
            source_path,
            str(raw_image),
            recorded_root=recorded_root,
            recorded_source=recorded_source,
            position=position,
        )
        image_metadata = _validate_image_path(
            image_path, max_image_bytes=max_image_bytes
        )
    elif absence_reason is None:
        raise SourceValidationError(
            f"manifest frame {position} has no image or absence reason"
        )

    raw_frame_id = entry.get("frame_id")
    frame_id = str(raw_frame_id or f"{source_id}:frame_{position:06d}").strip()
    if not frame_id or len(frame_id) > 160 or any(char.isspace() for char in frame_id):
        raise SourceValidationError(
            f"manifest frame {position} has an invalid frame_id"
        )
    frame_index = _nonnegative_int(
        entry.get("frame_index"),
        default=position,
        label=f"frame {position} frame_index",
    )
    timestamp_ms = _nonnegative_int(
        entry.get("timestamp_ms", entry.get("captured_at_ms")),
        default=position * 1000,
        label=f"frame {position} timestamp_ms",
    )
    metadata = {"manifest_position": position, **image_metadata}
    if "annotation" in entry:
        metadata["annotation"] = copy.deepcopy(entry["annotation"])
    return ReplayFrame(
        source_id=source_id,
        frame_id=frame_id,
        frame_index=frame_index,
        position=position,
        timestamp_ms=timestamp_ms,
        image_path=image_path,
        absence_reason=absence_reason,
        content_type=content_type_for_path(image_path) if image_path else None,
        metadata=metadata,
    )


def _resolve_image_path(
    source_path: Path,
    value: str,
    *,
    recorded_root: Path | None,
    recorded_source: tuple[Path, Path] | None,
    position: int,
) -> Path:
    declared = Path(value).expanduser()
    allowed_root = source_path
    if declared.is_absolute():
        candidate = declared
        if (
            not _is_within(candidate.resolve(), source_path)
            and recorded_root is not None
        ):
            relative = _recorded_relative_path(declared, recorded_root)
            if relative is not None:
                candidate = source_path / relative
            elif recorded_source is not None:
                recorded, current = recorded_source
                try:
                    relative = declared.relative_to(recorded)
                except ValueError:
                    pass
                else:
                    candidate = current / relative
                    allowed_root = current
    else:
        if ".." in declared.parts:
            raise SourceValidationError(
                f"manifest frame {position} image path must remain inside the source"
            )
        candidate = source_path / declared

    resolved = candidate.resolve()
    if not _is_within(resolved, allowed_root):
        raise SourceValidationError(
            f"manifest frame {position} image escapes the source"
        )
    if _path_contains_symlink(candidate, allowed_root):
        raise SourceValidationError(
            f"manifest frame {position} image path may not traverse symlinks"
        )
    return resolved


def _recorded_relative_path(path: Path, recorded_root: Path) -> Path | None:
    try:
        return path.relative_to(recorded_root)
    except ValueError:
        pass
    if recorded_root.is_absolute():
        return None
    root_parts = recorded_root.parts
    if not root_parts:
        return None
    path_parts = path.parts
    for start in range(len(path_parts) - len(root_parts), -1, -1):
        if path_parts[start : start + len(root_parts)] == root_parts:
            trailing = path_parts[start + len(root_parts) :]
            return Path(*trailing) if trailing else None
    return None


def _absence_reason(entry: dict[str, Any]) -> str | None:
    reason = entry.get("absence_reason")
    if isinstance(reason, str) and reason.strip():
        return reason.strip()[:240]
    if entry.get("absent") is True:
        return "absent"
    return None


def _nonnegative_int(value: Any, *, default: int, label: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or isinstance(value, float) and not value.is_integer():
        raise SourceValidationError(f"{label} must be a nonnegative integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise SourceValidationError(f"{label} must be a nonnegative integer") from exc
    if number < 0:
        raise SourceValidationError(f"{label} must be a nonnegative integer")
    return number


def _validate_image_path(path: Path, *, max_image_bytes: int) -> dict[str, Any]:
    if path.is_symlink():
        raise SourceValidationError(f"image may not be a symlink: {path.name}")
    if not path.exists() or not path.is_file():
        raise SourceValidationError(f"manifest image does not exist: {path.name}")
    if path.suffix.lower() not in WORKBENCH_IMAGE_EXTENSIONS:
        raise SourceValidationError(f"unsupported image format: {path.name}")
    size = path.stat().st_size
    if size <= 0:
        raise SourceValidationError(f"image is empty: {path.name}")
    if size > max_image_bytes:
        raise SourceValidationError(
            f"image {path.name} is {size} bytes; max_image_bytes is {max_image_bytes}"
        )
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            image_format = image.format
        expected_formats = {
            ".jpg": "jpeg",
            ".jpeg": "jpeg",
            ".png": "png",
            ".webp": "webp",
            ".bmp": "bmp",
        }
        if str(image_format or "").lower() != expected_formats[path.suffix.lower()]:
            raise SourceValidationError(
                f"image {path.name} has unsupported decoded format {image_format!r}"
            )
    except Exception as exc:  # noqa: BLE001 - source validation boundary
        raise SourceValidationError(
            f"image {path.name} is not decodable: {exc}"
        ) from exc
    return {
        "bytes": size,
        "width_px": int(width),
        "height_px": int(height),
        "format": str(image_format or "").lower() or None,
    }


def _validate_frame_sequence(frames: tuple[ReplayFrame, ...]) -> None:
    ids = [frame.frame_id for frame in frames]
    if len(ids) != len(set(ids)):
        raise SourceValidationError("source contains duplicate frame_id values")
    indices = [frame.frame_index for frame in frames]
    timestamps = [frame.timestamp_ms for frame in frames]
    if any(current <= previous for previous, current in zip(indices, indices[1:])):
        raise SourceValidationError("frame_index values must be strictly increasing")
    if any(
        current <= previous for previous, current in zip(timestamps, timestamps[1:])
    ):
        raise SourceValidationError("timestamp_ms values must be strictly increasing")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _path_contains_symlink(path: Path, root: Path) -> bool:
    current = path
    while True:
        if current.is_symlink():
            return True
        if current == root:
            return False
        try:
            current.relative_to(root)
        except ValueError:
            return False
        current = current.parent


__all__ = [
    "ImageFeed",
    "ReplayFrame",
    "SourceValidationError",
    "WORKBENCH_ADAPTER",
    "WORKBENCH_DEFAULT_MAX_FRAMES",
    "WORKBENCH_DEFAULT_MAX_IMAGE_BYTES",
    "content_type_for_path",
    "load_image_feed",
    "normalize_image_directory",
]
