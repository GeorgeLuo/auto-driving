"""Where a vehicle's Mac-side runtime views record themselves."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..shared.paths import RUNTIME_ROOT
from .step_activations import host_bundle


def runtime_view_dir(host_name: str, *, runtime_root: Path = RUNTIME_ROOT) -> Path:
    """A standalone view uses the same bundle runtime layout for every vehicle."""
    bundle = host_bundle(host_name, runtime_root)
    return Path(bundle["runtime_dir"]) / "view"


def discover_runtime_view(
    host_name: str,
    probe: Callable[[Path], dict[str, Any]],
    *,
    runtime_root: Path = RUNTIME_ROOT,
) -> dict[str, Any]:
    """Prefer a run's view, then a standalone view, without starting either."""
    view_dir = runtime_view_dir(host_name, runtime_root=runtime_root)
    run = probe(view_dir.parent / "automation")
    if run.get("available"):
        return run
    standalone = probe(view_dir)
    return standalone if standalone.get("available") else run
