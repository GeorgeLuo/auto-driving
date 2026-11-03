"""Read and reconstruct the controller releases ``hosts update`` packages.

Path lookup stays in ``shared.controller_releases``. This module extracts a
release archive once into ``releases/<archive sha256>`` and does not package
or deploy one.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path
from typing import Any

from ..shared.controller_releases import bundle_root
from ..shared.paths import display_path
from .errors import RuntimeHostError


def update_command(host_name: str) -> str:
    return f"./cli/automa hosts update --name {host_name}"


def release_manifest(
    host_name: str, archive_sha256: str | None = None, *, tree_sha256: str | None = None,
) -> dict[str, Any]:
    """The latest release, or the newest whose archive or source tree has that sha256."""

    releases = bundle_root(host_name) / "releases"
    wanted = archive_sha256 or tree_sha256
    if wanted is None:
        candidates = [releases / "latest-controller-bundle.json"]
    else:
        candidates = sorted(releases.glob("*.manifest.json"), reverse=True)
    for path in candidates:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if archive_sha256 is not None and (manifest.get("archive") or {}).get("sha256") != archive_sha256:
            continue
        if tree_sha256 is not None and manifest.get("tree_sha256") != tree_sha256:
            continue
        return manifest
    named = "No controller release" if wanted is None else f"Controller release {wanted[:12]} is not"
    raise RuntimeHostError(
        f"{named} in {display_path(releases)}.\nRun: {update_command(host_name)}"
    )


def release_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "archive_sha256": manifest["archive"]["sha256"],
        "archive": Path(manifest["archive"]["path"]).name,
        "tree_sha256": manifest.get("tree_sha256"),
        "created_at_ms": manifest.get("created_at_ms"),
    }


def release_code_dir(
    host_name: str, archive_sha256: str | None = None, *, tree_sha256: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """The release's code, extracted once into ``releases/<archive sha256>``."""

    manifest = release_manifest(host_name, archive_sha256, tree_sha256=tree_sha256)
    summary = release_summary(manifest)
    sha = summary["archive_sha256"]
    releases = bundle_root(host_name) / "releases"
    target = releases / sha
    if target.is_dir():
        return target, summary
    archive = releases / summary["archive"]
    try:
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    except OSError as exc:
        raise RuntimeHostError(
            f"Controller release archive {display_path(archive)} is unreadable: {exc}\n"
            f"Run: {update_command(host_name)}"
        ) from exc
    if digest != sha:
        raise RuntimeHostError(
            f"Controller release archive {display_path(archive)} has sha256 {digest[:12]}, "
            f"not {sha[:12]}.\nRun: {update_command(host_name)}"
        )
    pending = releases / f".{sha}.{os.getpid()}.pending"
    shutil.rmtree(pending, ignore_errors=True)
    pending.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as bundle:
        bundle.extractall(pending, filter="data")
    try:
        pending.rename(target)
    except OSError:
        # Another command extracted the same release first.
        shutil.rmtree(pending, ignore_errors=True)
    return target, summary
