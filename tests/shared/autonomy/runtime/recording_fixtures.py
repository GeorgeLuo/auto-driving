"""Read a run directory's committed recording entries."""

from __future__ import annotations

import json
from pathlib import Path

from autonomy.runtime.recording import RECORDING_MANIFEST_NAME, recording_entries


def read_recording_entries(root: Path) -> list[object]:
    """The committed frame entries of one run directory, in record order."""

    manifest = json.loads((Path(root) / RECORDING_MANIFEST_NAME).read_text(encoding="utf-8"))
    return recording_entries(Path(root), manifest)
