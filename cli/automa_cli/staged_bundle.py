"""Activation writes, and the staged-bundle import swap the shared loader owns.

``StagedBundleImport`` lives in ``autonomy.runtime.plugin_loader``. This module
re-exports it for callers that already import it with the JSON writer.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from autonomy.runtime.plugin_loader import StagedBundleImport

__all__ = ["StagedBundleImport", "write_json_atomically"]


def write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    """Replace a JSON file in one step so a reader never sees a partial write.

    An existing file keeps its permission bits. The parent directory must
    already exist.
    """

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            mode = None
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            if mode is not None:
                os.fchmod(stream.fileno(), mode)
            stream.write(json.dumps(payload, indent=2, sort_keys=True))
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
