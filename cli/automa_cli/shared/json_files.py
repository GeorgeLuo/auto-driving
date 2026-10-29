"""JSON files the CLI writes beside a vehicle's runtime and reads back."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any


def write_json_atomically(path: Path, payload: Any) -> None:
    """Replace a JSON file in one step so a reader never sees a partial write.

    The parent directory is created when missing. An existing file keeps its
    permission bits.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
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


def read_json(path: Path) -> Any:
    """A JSON file's value, or ``None`` when it is missing or not valid JSON."""

    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
