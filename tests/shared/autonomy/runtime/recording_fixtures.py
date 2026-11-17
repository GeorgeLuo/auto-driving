"""Read a run directory's committed recording entries."""

from __future__ import annotations

import json
from pathlib import Path

from autonomy.runtime.recording import GENERATIONS_DIR, RECORDING_MANIFEST_NAME, recording_entries


def read_recording_entries(root: Path) -> list[object]:
    """The committed frame entries of one run directory, in record order."""

    manifest = json.loads((Path(root) / RECORDING_MANIFEST_NAME).read_text(encoding="utf-8"))
    return recording_entries(Path(root), manifest)


def read_recording_generations(root: Path) -> dict[str, bytes]:
    """Each stored generation file of one run directory, by file name."""

    return {path.name: path.read_bytes() for path in sorted((Path(root) / GENERATIONS_DIR).glob("*.json"))}
