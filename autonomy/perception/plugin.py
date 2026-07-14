from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping, Protocol, TypeVar, runtime_checkable

from .evidence import PerceptionEvidenceBatch


PLUGIN_STATE_MODES = ("stateless", "pairwise", "windowed")
PluginStateMode = Literal["stateless", "pairwise", "windowed"]
ComponentT = TypeVar("ComponentT")


class PerceptionComponentUnavailable(RuntimeError):
    """A declared plugin input cannot be derived from the sensor snapshot."""


class PerceptionPluginWarmingUp(RuntimeError):
    """A stateful plugin has valid input but not enough history yet."""

    def __init__(self, reason: str, *, measurements: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.measurements = dict(measurements or {})


@dataclass(frozen=True)
class PerceptionPluginInput:
    """One named component injected into a plugin by the framework."""

    name: str
    component_id: str
    provider_spec: str

    def __post_init__(self) -> None:
        if not self.name or not self.component_id or not self.provider_spec:
            raise ValueError("plugin inputs require name, component_id, and provider_spec")
        if ":" not in self.provider_spec:
            raise ValueError("component provider spec must be 'module.path:callable'")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class PerceptionPluginContract:
    """Declarative algorithm requirements and externally visible meaning."""

    inputs: tuple[PerceptionPluginInput, ...] = ()
    state_mode: PluginStateMode = "stateless"
    description: str = ""
    assumptions: tuple[str, ...] = ()
    emits: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    diagnostic_artifacts: tuple[str, ...] = ()
    diagnostics_required: bool = False

    def __post_init__(self) -> None:
        input_names = [item.name for item in self.inputs]
        component_ids = [item.component_id for item in self.inputs]
        if len(input_names) != len(set(input_names)):
            raise ValueError("plugin input names must be unique")
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("plugin component ids must be unique")
        if self.state_mode not in PLUGIN_STATE_MODES:
            raise ValueError(f"unsupported plugin state mode: {self.state_mode!r}")
        if len(self.diagnostic_artifacts) != len(set(self.diagnostic_artifacts)):
            raise ValueError("diagnostic artifact ids must be unique")
        if any(_safe_name(item) != item for item in self.diagnostic_artifacts):
            raise ValueError("diagnostic artifact ids must be non-empty safe names")
        if self.diagnostics_required and not self.diagnostic_artifacts:
            raise ValueError("diagnostics_required needs declared diagnostic artifacts")

    def to_dict(self) -> dict[str, Any]:
        return {
            "inputs": [item.to_dict() for item in self.inputs],
            "state_mode": self.state_mode,
            "description": self.description,
            "assumptions": list(self.assumptions),
            "emits": list(self.emits),
            "limitations": list(self.limitations),
            "diagnostic_artifacts": list(self.diagnostic_artifacts),
            "diagnostics_required": self.diagnostics_required,
        }


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


@dataclass(frozen=True)
class PerceptionPluginInputs:
    """Resolved, typed-by-contract values presented to one plugin."""

    frame_id: str
    captured_at_ms: int
    components: Mapping[str, Any]
    diagnostics: PerceptionDiagnosticSink
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "components", MappingProxyType(dict(self.components)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def require(self, name: str, expected_type: type[ComponentT]) -> ComponentT:
        if name not in self.components:
            raise KeyError(f"plugin input {name!r} was not injected")
        value = self.components[name]
        if not isinstance(value, expected_type):
            raise TypeError(
                f"plugin input {name!r} is {type(value).__name__}, "
                f"expected {expected_type.__name__}"
            )
        return value


@runtime_checkable
class PerceptionPlugin(Protocol):
    plugin_id: str
    contract: PerceptionPluginContract

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        ...


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return normalized or "plugin"
