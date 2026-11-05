"""Where a host's controller bundle lives.

``store`` reads and reconstructs the releases in that bundle. The runtime
root and path sanitization stay in ``shared.paths``.
"""

from __future__ import annotations

from pathlib import Path

from autonomy.runtime.layout import RuntimeLayout

from ..shared.paths import runtime_root as cli_runtime_root, safe_path_part


def controller_bundle_paths(host_dir: Path) -> dict[str, str]:
    return RuntimeLayout(host_dir / "bundle").bundle_paths()


def bundle_paths(host_name: str, runtime_root: Path | None = None) -> dict[str, str]:
    """A host's controller bundle paths under ``runtime_root``, by default the CLI's runtime root."""

    root = cli_runtime_root() if runtime_root is None else Path(runtime_root)
    return controller_bundle_paths(root / safe_path_part(host_name))


def bundle_root(host_name: str) -> Path:
    return Path(bundle_paths(host_name)["root_dir"])
