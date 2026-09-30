"""Framework-owned diagnostic sink for perception plugins."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Mapping


class PerceptionDiagnosticSink:
    """Framework-owned, opt-in destination for plugin diagnostics."""

    def __init__(
        self,
        *,
        output_dir: Path | None,
        plugin_id: str,
        allowed_artifacts: tuple[str, ...],
    ) -> None:
        self._root = (
            output_dir / _safe_name(plugin_id)
            if output_dir is not None
            else None
        )
        self._allowed = frozenset(allowed_artifacts)
        self._artifacts: dict[str, str] = {}

    @property
    def enabled(self) -> bool:
        return self._root is not None

    @property
    def directory(self) -> Path | None:
        """Directory for helpers that emit several related diagnostic files."""

        if self._root is not None:
            self._root.mkdir(parents=True, exist_ok=True)
        return self._root

    @property
    def artifacts(self) -> dict[str, str]:
        return dict(self._artifacts)

    def emit(
        self,
        artifact_id: str,
        filename: str,
        writer: Callable[[Path], Any],
    ) -> Path | None:
        if not self.enabled:
            return None
        self._validate_artifact_id(artifact_id)
        if not filename or Path(filename).name != filename:
            raise ValueError("diagnostic filenames must be plain file names")
        assert self._root is not None
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / filename
        writer(path)
        if not path.is_file():
            raise RuntimeError(f"diagnostic writer did not create {path}")
        self._artifacts[artifact_id] = str(path)
        return path

    def emit_json(self, artifact_id: str, filename: str, payload: Any) -> Path | None:
        return self.emit(
            artifact_id,
            filename,
            lambda path: path.write_text(
                json.dumps(payload, indent=2, sort_keys=True),
                encoding="utf-8",
            ),
        )

    def register(self, artifacts: Mapping[str, str | Path]) -> None:
        if not self.enabled and artifacts:
            raise RuntimeError("cannot register diagnostics when the sink is disabled")
        for artifact_id, raw_path in artifacts.items():
            self._validate_artifact_id(artifact_id)
            path = Path(raw_path)
            if not path.is_file():
                raise FileNotFoundError(path)
            assert self._root is not None
            root = self._root.resolve()
            resolved = path.resolve()
            if resolved.parent != root and root not in resolved.parents:
                raise ValueError(
                    f"diagnostic {artifact_id!r} is outside the plugin namespace: {path}"
                )
            self._artifacts[artifact_id] = str(path)

    def _validate_artifact_id(self, artifact_id: str) -> None:
        if artifact_id not in self._allowed:
            raise ValueError(
                f"plugin emitted undeclared diagnostic {artifact_id!r}; "
                f"declared: {sorted(self._allowed)}"
            )


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return normalized or "plugin"
