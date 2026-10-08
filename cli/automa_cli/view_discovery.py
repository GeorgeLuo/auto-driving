"""Runtime view locations shared by local workers and remote-host monitors."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from .bundles import controller_bundle_paths
from .paths import ROOT, safe_path_part

RUNTIME_ROOT = Path(os.environ.get("AUTOMA_RUNTIME_ROOT", ROOT / "runtime" / "vehicles"))


def runtime_view_dir(vehicle_id: str, *, runtime_root: Path = RUNTIME_ROOT) -> Path:
    """A standalone view uses the same bundle runtime layout for every vehicle."""
    bundle = controller_bundle_paths(runtime_root / safe_path_part(vehicle_id))
    return Path(bundle["runtime_dir"]) / "view"


def discover_runtime_view(
    vehicle_id: str,
    probe: Callable[[Path], dict[str, Any]],
    *,
    runtime_root: Path = RUNTIME_ROOT,
) -> dict[str, Any]:
    """Prefer a run's view, then a standalone view, without starting either."""
    vehicle_dir = runtime_root / safe_path_part(vehicle_id)
    runtime_dir = Path(controller_bundle_paths(vehicle_dir)["runtime_dir"])
    directories = (
        runtime_dir / "automation",
        runtime_view_dir(vehicle_id, runtime_root=runtime_root),
        # Read existing records while older standalone processes wind down.
        vehicle_dir / "picar_observation",
    )
    first = probe(directories[0])
    if first.get("available"):
        return first
    for directory in directories[1:]:
        status = probe(directory)
        if status.get("available"):
            return status
    return first
