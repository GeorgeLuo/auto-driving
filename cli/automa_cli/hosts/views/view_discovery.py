"""Where a vehicle's Mac-side runtime views record themselves."""

from __future__ import annotations

from pathlib import Path

from ...releases.paths import bundle_paths


def runtime_view_dir(host_name: str) -> Path:
    """A standalone view uses the same bundle runtime layout for every vehicle."""
    bundle = bundle_paths(host_name)
    return Path(bundle["runtime_dir"]) / "view"
