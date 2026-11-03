"""Where a host's controller bundle lives.

Reading and reconstructing the releases ``hosts update`` packages into that
bundle lives in ``releases``. A missing release raises ``RuntimeHostError``
from there.
"""

from __future__ import annotations

from pathlib import Path

from autonomy.runtime.layout import RuntimeLayout

from .paths import RUNTIME_ROOT, safe_path_part


def controller_bundle_paths(host_dir: Path) -> dict[str, str]:
    return RuntimeLayout(host_dir / "bundle").bundle_paths()


def bundle_paths(host_name: str) -> dict[str, str]:
    return controller_bundle_paths(RUNTIME_ROOT / safe_path_part(host_name))


def bundle_root(host_name: str) -> Path:
    return Path(bundle_paths(host_name)["root_dir"])
