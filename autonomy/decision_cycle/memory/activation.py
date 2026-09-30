"""Memory activation documents and implementation configuration."""

from __future__ import annotations

import importlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autonomy.plugins import LocalPluginCatalog, PluginDefinition, PluginManager

from autonomy.decision_cycle.memory.plugin import MemoryImplementation
from autonomy.decision_cycle.memory.selection import memory_plugin_manager
from autonomy.decision_cycle.memory.snapshots.fallback import validate_framework_fallback_capacity
from autonomy.decision_cycle.memory.snapshots.values import (
    DEFAULT_MAX_PROPERTY_BYTES,
    DEFAULT_MAX_SERIALIZED_BYTES,
    MemoryBounds,
)

if TYPE_CHECKING:
    from autonomy.decision_cycle.memory.plugin_runner import PluginMemoryRunner


MEMORY_ACTIVATION_SCHEMA = "automa_memory_activation_v0"
DEFAULT_MAX_RECORDS = 32
DEFAULT_MAX_AGE_MS = 10_000
DEFAULT_EVICTION_POLICY = "oldest_first"


@dataclass(frozen=True)
class MemoryActivation:
    """An activation document with one resolved snapshot of its plugin selection."""

    implementation_id: str | None
    implementation_spec: str | None
    implementation_config: dict[str, Any] | None
    bounds: MemoryBounds | None
    source_path: Path
    payload: dict[str, Any]
    available_definitions: tuple[PluginDefinition, ...] = field(init=False, repr=False)
    selected_definitions: tuple[PluginDefinition, ...] = field(init=False, repr=False)
    _selection_config: dict[str, Any] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        memory = self.payload.get("memory")
        if not isinstance(memory, dict):
            raise ValueError(f"memory activation has no memory section: {self.source_path}")
        explicit = "plugins" in memory or "plugin_specs" in memory
        if explicit:
            plugins = memory.get("plugins")
            specs = memory.get("plugin_specs")
            configs = memory.get("plugin_configs", {})
            if not isinstance(plugins, list) or not all(isinstance(item, str) for item in plugins):
                raise ValueError("memory.plugins must be a list of plugin IDs")
            if not isinstance(specs, dict) or not isinstance(configs, dict):
                raise ValueError("memory plugin_specs and plugin_configs must be objects")
            selection_config = deepcopy(
                {"plugins": plugins, "plugin_specs": specs, "plugin_configs": configs}
            )
        else:
            if not isinstance(self.implementation_id, str) or not self.implementation_id.strip():
                raise ValueError(f"memory activation has no implementation_id: {self.source_path}")
            if not isinstance(self.implementation_spec, str) or not self.implementation_spec.strip():
                raise ValueError(f"memory activation has no implementation_spec: {self.source_path}")
            if not isinstance(self.implementation_config, dict):
                raise ValueError(f"memory activation has invalid implementation_config: {self.source_path}")
            selection_config = {
                "plugins": [self.implementation_id.strip()],
                "plugin_specs": {
                    self.implementation_id.strip(): self.implementation_spec.strip()
                },
                "plugin_configs": {
                    self.implementation_id.strip(): deepcopy(self.implementation_config)
                },
            }

        manager = memory_plugin_manager(
            selection_config["plugin_specs"], selection_config["plugin_configs"]
        )
        manager.select(selection_config["plugins"])
        object.__setattr__(self, "available_definitions", manager.available)
        object.__setattr__(self, "selected_definitions", manager.selected)
        object.__setattr__(self, "_selection_config", selection_config)

        if explicit:
            # Old single-implementation fields may remain in the document as
            # staging metadata. The selected definitions own execution.
            final = manager.selected[-1] if manager.selected else None
            config = dict(final.config) if final is not None else {}
            object.__setattr__(self, "implementation_id", final.plugin_id if final else None)
            object.__setattr__(self, "implementation_spec", final.entrypoint if final else None)
            object.__setattr__(self, "implementation_config", deepcopy(config))
            object.__setattr__(self, "bounds", bounds_from_config(config))
        else:
            config = deepcopy(self.implementation_config)
            object.__setattr__(self, "implementation_id", self.implementation_id.strip())
            object.__setattr__(self, "implementation_spec", self.implementation_spec.strip())
            object.__setattr__(self, "implementation_config", config)
            if self.bounds is None:
                object.__setattr__(self, "bounds", bounds_from_config(config))


def memory_selection_config(activation: MemoryActivation) -> dict[str, Any]:
    """Return the selection normalized when the activation was constructed."""

    return deepcopy(activation._selection_config)


def memory_manager_from_activation(activation: MemoryActivation) -> PluginManager:
    manager = PluginManager("memory", LocalPluginCatalog(activation.available_definitions))
    manager.select(activation.selected_definitions)
    return manager


def read_memory_activation(path: Path) -> MemoryActivation:
    if not path.exists():
        raise FileNotFoundError(f"memory activation is missing: {path}")
    payload = json_load_object(path)
    if payload.get("schema") != MEMORY_ACTIVATION_SCHEMA:
        raise ValueError(
            f"memory activation has unsupported schema {payload.get('schema')!r}: {path}"
        )
    memory = payload.get("memory")
    if not isinstance(memory, dict):
        raise ValueError(f"memory activation has no memory section: {path}")

    return MemoryActivation(
        implementation_id=memory.get("implementation_id"),
        implementation_spec=memory.get("implementation_spec"),
        implementation_config=memory.get("implementation_config"),
        bounds=None,
        source_path=path,
        payload=payload,
    )


def bounds_from_config(config: dict[str, Any]) -> MemoryBounds:
    max_records = config.get("max_records", DEFAULT_MAX_RECORDS)
    max_age_ms = config.get("max_age_ms", DEFAULT_MAX_AGE_MS)
    eviction_policy = config.get("eviction_policy", DEFAULT_EVICTION_POLICY)
    max_property_bytes = config.get("max_property_bytes", DEFAULT_MAX_PROPERTY_BYTES)
    max_serialized_bytes = config.get(
        "max_serialized_bytes", DEFAULT_MAX_SERIALIZED_BYTES
    )
    bounds = MemoryBounds(
        max_records=int(max_records),
        max_age_ms=int(max_age_ms) if max_age_ms is not None else None,
        eviction_policy=str(eviction_policy or DEFAULT_EVICTION_POLICY),
        max_property_bytes=(
            int(max_property_bytes) if max_property_bytes is not None else None
        ),
        max_serialized_bytes=(
            int(max_serialized_bytes) if max_serialized_bytes is not None else None
        ),
    )
    validate_framework_fallback_capacity(bounds)
    return bounds


def load_memory_implementation(
    activation: MemoryActivation,
    *,
    reload_module: bool = False,
) -> MemoryImplementation:
    if activation.implementation_spec is None or activation.implementation_config is None:
        raise ValueError("memory activation selects no implementation")
    return instantiate_memory_implementation(
        activation.implementation_spec,
        activation.implementation_config,
        reload_module=reload_module,
    )


def load_memory_step_if_present(path: Path) -> PluginMemoryRunner | None:
    """Load an activated memory step when the activation document exists.

    Missing paths return None so Chase and Donkey hosts can share optional
    wiring without requiring memory before package activation exists.
    """

    if not path.exists():
        return None
    from autonomy.decision_cycle.memory.plugin_runner import PluginMemoryRunner

    return PluginMemoryRunner(read_memory_activation(path))


def instantiate_memory_implementation(
    implementation_spec: str,
    implementation_config: dict[str, Any],
    *,
    reload_module: bool = False,
) -> MemoryImplementation:
    module_name, separator, class_name = implementation_spec.partition(":")
    if not separator or not module_name or not class_name:
        raise ValueError("memory implementation spec must be 'module.path:ClassName'")
    importlib.invalidate_caches()
    module = importlib.import_module(module_name)
    if reload_module:
        module = importlib.reload(module)
    implementation_cls = getattr(module, class_name)
    implementation = implementation_cls(**deepcopy(implementation_config))
    if not isinstance(implementation, MemoryImplementation):
        raise TypeError(
            "configured memory implementation does not satisfy MemoryImplementation: "
            f"{implementation_spec}"
        )
    if not isinstance(implementation.implementation_id, str) or not implementation.implementation_id.strip():
        raise TypeError(
            f"memory implementation must declare a non-empty implementation_id: {implementation_spec}"
        )
    return implementation


def json_load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"memory activation must be a JSON object: {path}")
    return payload


def __getattr__(name: str) -> Any:
    """Expose the host-facing legacy name without making activation own execution."""
    if name not in {"ActivatedMemoryStep", "PluginMemoryRunner"}:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from autonomy.decision_cycle.memory.plugin_runner import PluginMemoryRunner

    return PluginMemoryRunner
