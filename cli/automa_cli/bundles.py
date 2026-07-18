from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from .paths import ROOT, display_path

AUTONOMY_DIR = ROOT / "autonomy"
IMPLEMENTATIONS_DIR = ROOT / "implementations"


def controller_bundle_paths(vehicle_runtime_dir: Path) -> dict[str, str]:
    bundle_root = vehicle_runtime_dir / "bundle"
    autonomy_dir = bundle_root / "autonomy"
    implementations_dir = bundle_root / "implementations"
    runtime_dir = bundle_root / "runtime"
    return {
        "root_dir": str(bundle_root),
        "autonomy_dir": str(autonomy_dir),
        "implementations_dir": str(implementations_dir),
        "perception_dir": str(implementations_dir / "perception"),
        "decision_dir": str(implementations_dir / "decision"),
        "runtime_dir": str(runtime_dir),
        "perception_runtime_dir": str(runtime_dir / "perception"),
        "decision_runtime_dir": str(runtime_dir / "decision"),
        "memory_runtime_dir": str(runtime_dir / "memory"),
    }


def sync_controller_bundle(bundle: dict[str, str], *, output: TextIO | None) -> dict[str, Any]:
    bundle_root = Path(bundle["root_dir"])
    autonomy_target = Path(bundle["autonomy_dir"])
    implementations_target = Path(bundle["implementations_dir"])
    _emit(output, "==> Package local controller bundle")
    release = package_controller_bundle(bundle)
    _emit(output, f"Archive: {display_path(Path(release['archive']['path']))}")
    _emit(output, f"Tree SHA-256: {release['tree_sha256']}")
    _emit(output, f"Archive SHA-256: {release['archive']['sha256']}")

    _emit(output, "==> Extract controller bundle release")
    if autonomy_target.exists():
        shutil.rmtree(autonomy_target)
    if implementations_target.exists():
        shutil.rmtree(implementations_target)
    bundle_root.mkdir(parents=True, exist_ok=True)
    _extract_controller_bundle_archive(Path(release["archive"]["path"]), bundle_root)
    _emit(output, f"Extracted release -> {bundle_root}")
    return release


def package_controller_bundle(bundle: dict[str, str]) -> dict[str, Any]:
    bundle_root = Path(bundle["root_dir"])
    release_dir = bundle_root / "releases"
    release_dir.mkdir(parents=True, exist_ok=True)

    entries = _controller_bundle_file_entries(
        [
            BundleSource(source_dir=AUTONOMY_DIR, package_root="autonomy"),
            BundleSource(source_dir=IMPLEMENTATIONS_DIR, package_root="implementations"),
        ]
    )
    tree_sha256 = _tree_sha256(entries)
    created_at_ms = int(time.time() * 1000)
    release_stem = f"autonomy-{tree_sha256[:12]}-{created_at_ms}"
    pending_archive = release_dir / f".{release_stem}.tar.gz.pending"
    archive_path = release_dir / f"{release_stem}.tar.gz"
    manifest_path = release_dir / f"{release_stem}.manifest.json"
    latest_manifest_path = release_dir / "latest-controller-bundle.json"

    manifest: dict[str, Any] = {
        "schema": "automa_controller_bundle_manifest_v0",
        "bundle_kind": "autonomy-controller",
        "created_at_ms": created_at_ms,
        "sources": [
            {"path": str(AUTONOMY_DIR), "package_root": "autonomy"},
            {"path": str(IMPLEMENTATIONS_DIR), "package_root": "implementations"},
        ],
        "target": {
            "root_dir": bundle["root_dir"],
            "autonomy_dir": bundle["autonomy_dir"],
            "implementations_dir": bundle["implementations_dir"],
        },
        "tree_sha256": tree_sha256,
        "file_count": len(entries),
        "total_bytes": sum(int(entry["size"]) for entry in entries),
        "files": entries,
    }

    try:
        with tarfile.open(pending_archive, "w:gz") as archive:
            for entry in entries:
                source_path = ROOT / entry["workspace_relative_path"]
                archive.add(source_path, arcname=entry["archive_path"], recursive=False)
            manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
            info = tarfile.TarInfo("bundle-manifest.json")
            info.size = len(manifest_bytes)
            info.mode = 0o644
            info.mtime = int(created_at_ms / 1000)
            archive.addfile(info, io.BytesIO(manifest_bytes))
        archive_sha256 = _sha256_file(pending_archive)
        pending_archive.replace(archive_path)
    finally:
        if pending_archive.exists():
            pending_archive.unlink()

    manifest["archive"] = {
        "path": str(archive_path),
        "sha256": archive_sha256,
        "format": "tar.gz",
    }
    manifest["manifest"] = {"path": str(manifest_path)}
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    latest_manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def controller_bundle_source_summary() -> dict[str, Any]:
    entries = _controller_bundle_file_entries(
        [
            BundleSource(source_dir=AUTONOMY_DIR, package_root="autonomy"),
            BundleSource(source_dir=IMPLEMENTATIONS_DIR, package_root="implementations"),
        ]
    )
    return {
        "tree_sha256": _tree_sha256(entries),
        "file_count": len(entries),
        "total_bytes": sum(int(entry["size"]) for entry in entries),
    }


def release_activation_summary(release: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": release["schema"],
        "bundle_kind": release["bundle_kind"],
        "created_at_ms": release["created_at_ms"],
        "tree_sha256": release["tree_sha256"],
        "archive_sha256": release["archive"]["sha256"],
        "archive": display_path(Path(release["archive"]["path"])),
        "manifest": display_path(Path(release["manifest"]["path"])),
        "file_count": release["file_count"],
        "total_bytes": release["total_bytes"],
    }


@dataclass(frozen=True)
class BundleSource:
    source_dir: Path
    package_root: str


def _controller_bundle_file_entries(sources: list[BundleSource]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for source in sources:
        for path in sorted(source.source_dir.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(source.source_dir)
            if _skip_controller_bundle_path(relative):
                continue
            archive_path = Path(source.package_root) / relative
            stat = path.stat()
            entries.append(
                {
                    "archive_path": archive_path.as_posix(),
                    "source_root": source.package_root,
                    "source_relative_path": relative.as_posix(),
                    "workspace_relative_path": path.relative_to(ROOT).as_posix(),
                    "size": stat.st_size,
                    "sha256": _sha256_file(path),
                }
            )
    return entries


def _skip_controller_bundle_path(relative: Path) -> bool:
    return (
        "__pycache__" in relative.parts
        or relative.suffix == ".pyc"
        or relative.name == ".DS_Store"
    )


def _tree_sha256(entries: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(str(entry["archive_path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(entry["size"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(entry["sha256"]).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_controller_bundle_archive(archive_path: Path, bundle_root: Path) -> None:
    bundle_root_resolved = bundle_root.resolve()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            target = (bundle_root / member.name).resolve()
            if target != bundle_root_resolved and bundle_root_resolved not in target.parents:
                raise RuntimeError(f"archive member escapes bundle root: {member.name}")
        archive.extractall(bundle_root)


def _emit(output: TextIO | None, message: str) -> None:
    if output is not None:
        print(message, file=output, flush=True)
