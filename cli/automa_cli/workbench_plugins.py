"""Manifest-backed plugin discovery and selection for the replay workbench.

The workbench deliberately has a smaller plugin boundary than the general lab
candidate tooling.  Discovery is metadata-only: a manifest is parsed and
validated without importing its entrypoint.  Importing and constructing a
plugin happens only after the operator selects its catalog id for a replay.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Sequence

from autonomy.decision_cycle.perception.interface import (
    PERCEPTION_TEXT_SCHEMA,
    PerceptionMapper,
)
from autonomy.decision_cycle.perception.activation import instantiate_perception_mapper
from autonomy.plugins import LocalPluginCatalog, PluginDefinition, PluginManager
from implementations.decision_cycle.memory.catalog import (
    DEFAULT_MEMORY_IMPLEMENTATION,
    MEMORY_IMPLEMENTATIONS,
)
from implementations.decision_cycle.perception.catalog import (
    DEFAULT_PERCEPTION_ALGORITHM,
    PERCEPTION_ALGORITHMS,
    PERCEPTION_MAPPER_SPEC,
    PERCEPTION_PLUGIN_SPECS,
)


PLUGIN_CATALOG_SCHEMA = "workbench_plugin_catalog_v1"
PLUGIN_MANIFEST_SCHEMA = "automa_lab_perception_plugin_v0"
DEFAULT_PLUGIN_ROOT_ID = "packaged:implementations.decision_cycle.perception.catalog"
_SAFE_PLUGIN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SAFE_SYMBOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PluginCatalogError(ValueError):
    """A bounded catalog or selection boundary failure."""

    boundary = "plugin_catalog"


def _memory_config_signature(spec: str, config: dict[str, Any]) -> tuple[str, str]:
    try:
        encoded = json.dumps(config, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise PluginCatalogError(
            f"memory implementation config is not JSON-comparable: {exc}"
        ) from exc
    return spec, encoded


def _register_memory_definition(
    specs: dict[str, str],
    configs: dict[str, dict[str, Any]],
    catalog_id: str,
    spec: str,
    config: dict[str, Any],
) -> None:
    """Add a definition without letting traversal order replace another one."""

    existing_spec = specs.get(catalog_id)
    if existing_spec is None:
        specs[catalog_id] = spec
        configs[catalog_id] = copy.deepcopy(config)
        return
    existing = _memory_config_signature(existing_spec, configs[catalog_id])
    if existing != _memory_config_signature(spec, config):
        raise PluginCatalogError(
            f"memory catalog id {catalog_id!r} conflicts with an existing definition"
        )


def _memory_definitions(
    plugins: tuple[PluginDescriptor, ...] | Sequence[PluginDescriptor],
) -> tuple[tuple[PluginDefinition, ...], dict[str, str]]:
    """Packaged memory plugins plus companion definitions with stable identities.

    Packaged configuration is never overwritten. Companions that share an
    implementation id keep that id only when they declare the same entrypoint
    and configuration. A different configuration gets its own catalog id, so
    discovery order cannot choose which config is active.
    """

    specs: dict[str, str] = {}
    configs: dict[str, dict[str, Any]] = {}
    for implementation_id, entry in MEMORY_IMPLEMENTATIONS.items():
        specs[implementation_id] = str(entry["implementation_spec"])
        configs[implementation_id] = copy.deepcopy(dict(entry["default_config"]))
    default_config = copy.deepcopy(configs[DEFAULT_MEMORY_IMPLEMENTATION])
    companions: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    for descriptor in plugins:
        companion = descriptor.memory
        if not companion:
            continue
        implementation_id = str(companion["implementation_id"])
        spec = str(companion["implementation_spec"])
        if implementation_id in MEMORY_IMPLEMENTATIONS:
            config = copy.deepcopy(configs[implementation_id])
        else:
            config = copy.deepcopy(default_config)
        config.update(dict(companion.get("implementation_config") or {}))
        companions.setdefault(implementation_id, []).append(
            (descriptor.plugin_id, spec, config)
        )

    perception_memory_ids: dict[str, str] = {}
    for implementation_id in sorted(companions):
        entries = companions[implementation_id]
        entry_specs = {spec for _plugin_id, spec, _config in entries}
        packaged_spec = specs.get(implementation_id)
        if packaged_spec is not None:
            entry_specs.add(packaged_spec)
        if len(entry_specs) > 1:
            raise PluginCatalogError(
                f"memory implementation {implementation_id!r} has conflicting entrypoints"
            )
        spec = next(iter(entry_specs))
        groups: dict[tuple[str, str], set[str]] = {}
        configs_by_signature: dict[tuple[str, str], dict[str, Any]] = {}
        for plugin_id, entry_spec, config in entries:
            signature = _memory_config_signature(entry_spec, config)
            groups.setdefault(signature, set()).add(plugin_id)
            configs_by_signature[signature] = config
        packaged_signature = (
            _memory_config_signature(packaged_spec, configs[implementation_id])
            if packaged_spec is not None
            else None
        )
        if packaged_signature is not None and set(groups) == {packaged_signature}:
            for plugin_id in groups[packaged_signature]:
                perception_memory_ids[plugin_id] = implementation_id
            continue
        if packaged_spec is None and len(groups) == 1:
            signature = next(iter(groups))
            _register_memory_definition(
                specs,
                configs,
                implementation_id,
                spec,
                configs_by_signature[signature],
            )
            for plugin_id in groups[signature]:
                perception_memory_ids[plugin_id] = implementation_id
            continue
        grouped = sorted(
            (
                tuple(sorted(owners)),
                signature,
                configs_by_signature[signature],
            )
            for signature, owners in groups.items()
        )
        for owners, signature, config in grouped:
            if packaged_signature is not None and signature == packaged_signature:
                for plugin_id in owners:
                    perception_memory_ids[plugin_id] = implementation_id
                continue
            catalog_id = ".".join((*owners, implementation_id))
            _register_memory_definition(specs, configs, catalog_id, signature[0], config)
            for plugin_id in owners:
                perception_memory_ids[plugin_id] = catalog_id
    definitions = tuple(
        PluginDefinition(
            step="memory",
            plugin_id=plugin_id,
            entrypoint=spec,
            config=configs[plugin_id],
        )
        for plugin_id, spec in specs.items()
    )
    return definitions, perception_memory_ids


def _selected_memory_id(
    perception_memory_ids: dict[str, str],
    selected_plugin_ids: tuple[str, ...],
) -> str:
    owners = [
        plugin_id
        for plugin_id in selected_plugin_ids
        if plugin_id in perception_memory_ids
    ]
    if len(owners) > 1:
        raise PluginCatalogError("selected plugins declare multiple memory companions")
    if owners:
        return perception_memory_ids[owners[0]]
    return DEFAULT_MEMORY_IMPLEMENTATION


@dataclass(frozen=True)
class PluginDescriptor:
    """Normalized, presentation-safe metadata for one manifest package."""

    plugin_id: str
    name: str
    description: str
    manifest_relative_path: str
    manifest_path: str | None
    entrypoint: str | None
    config: dict[str, Any]
    memory: dict[str, Any]
    inputs: list[dict[str, Any]]
    output: dict[str, Any]
    model: dict[str, Any]
    runtime: dict[str, Any]
    status: str
    unavailable_reason: str | None = None
    source: str = "manifest"
    default: bool = False
    _directory: Path | None = field(default=None, repr=False, compare=False)

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def to_dict(self, *, active_ids: Sequence[str] = ()) -> dict[str, Any]:
        active = self.plugin_id in active_ids and self.ready
        return {
            "id": self.plugin_id,
            "name": self.name,
            "description": self.description,
            "manifest_relative_path": self.manifest_relative_path,
            "manifest_path": self.manifest_path,
            "entrypoint": self.entrypoint,
            "config": _json_safe(self.config),
            "memory": _json_safe(self.memory),
            "inputs": _json_safe(self.inputs),
            "output": _json_safe(self.output),
            "model": _json_safe(self.model),
            "runtime": _json_safe(self.runtime),
            "status": self.status,
            "ready": self.ready,
            "unavailable_reason": self.unavailable_reason,
            "source": self.source,
            "default": self.default,
            "active": active,
        }


@dataclass(frozen=True)
class PluginCatalog:
    """Deterministic catalog plus the trusted root used to discover it."""

    root: Path | None
    plugins: tuple[PluginDescriptor, ...]
    digest: str
    explicit_root: bool = False
    error: str | None = None

    @property
    def root_identity(self) -> str:
        return str(self.root) if self.root is not None else DEFAULT_PLUGIN_ROOT_ID

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(item.plugin_id for item in self.plugins)

    @property
    def ready_ids(self) -> tuple[str, ...]:
        return tuple(item.plugin_id for item in self.plugins if item.ready)

    @property
    def valid(self) -> bool:
        return self.error is None and not any(
            item.unavailable_reason == "duplicate plugin id" for item in self.plugins
        )

    def to_dict(self, *, active_ids: Sequence[str] = ()) -> dict[str, Any]:
        return {
            "schema": PLUGIN_CATALOG_SCHEMA,
            "root": self.root_identity,
            "explicit_root": self.explicit_root,
            "digest": self.digest,
            "valid": self.valid,
            "error": self.error,
            "plugins": [item.to_dict(active_ids=active_ids) for item in self.plugins],
        }

    def list(self, step: str) -> tuple[PluginDefinition, ...]:
        """Expose the selectable catalog to the core manager without imports."""

        if step != "perception":
            return ()
        if self.error:
            raise PluginCatalogError(self.error)
        if not self.valid:
            raise PluginCatalogError("plugin catalog is invalid: duplicate plugin id")
        return tuple(self.resolve(step, item.plugin_id) for item in self.plugins if item.ready)

    def resolve(self, step: str, reference: str | Path) -> PluginDefinition:
        """Adapt discovered manifests to the common selection resolver."""

        if step != "perception":
            raise PluginCatalogError(f"unsupported plugin step {step!r}")
        descriptor = next(
            (item for item in self.plugins if item.plugin_id == reference), None
        )
        if descriptor is None:
            raise PluginCatalogError(f"unknown plugin id(s): {reference}")
        if not descriptor.ready:
            raise PluginCatalogError(
                f"unavailable plugin id(s): {reference}: "
                f"{descriptor.unavailable_reason or 'unavailable'}"
            )
        if not descriptor.entrypoint:
            raise PluginCatalogError(f"plugin {reference!r} has no entrypoint")
        return PluginDefinition(
            step="perception",
            plugin_id=descriptor.plugin_id,
            entrypoint=descriptor.entrypoint,
            config=descriptor.config,
        )

    def normalize_selection(
        self,
        active_ids: Sequence[str] | None,
        *,
        require_explicit_selection: bool | None = None,
    ) -> tuple[str, ...]:
        """Validate ids while preserving the core manager's selection order."""

        if self.error:
            raise PluginCatalogError(self.error)
        if not self.valid:
            raise PluginCatalogError("plugin catalog is invalid: duplicate plugin id")
        raw_values = [] if active_ids is None else list(active_ids)
        if any(not isinstance(value, str) for value in raw_values):
            raise PluginCatalogError("active_plugin_ids must contain non-empty strings")
        values = [value.strip() for value in raw_values]
        if any(not value for value in values):
            raise PluginCatalogError("active_plugin_ids must contain non-empty strings")
        if len(values) != len(set(values)):
            raise PluginCatalogError("active_plugin_ids must not contain duplicates")
        manager = PluginManager("perception", self)
        manager.select(values)
        # Keep the keyword for caller compatibility. An empty normalized
        # selection is the explicit raw-capture mode regardless of catalog
        # root: replay still displays frames, but no perception plugin runs.
        return manager.selected_ids

    def build_mapper(self, active_ids: Sequence[str]) -> PerceptionMapper:
        """Instantiate exactly the selected core-runtime manifest plugins."""

        selected = self.normalize_selection(active_ids, require_explicit_selection=False)
        manager = PluginManager("perception", self)
        manager.select(selected)

        if self.root is not None:
            with _import_root(self.root):
                mapper = instantiate_perception_mapper(
                    PERCEPTION_MAPPER_SPEC, {"plugin_manager": manager},
                )
            perceive = mapper.perceive

            def perceive_in_root(request):
                # Later manager selections can import previously unused packages.
                with _import_root(self.root):
                    return perceive(request)

            mapper.perceive = perceive_in_root
        else:
            mapper = instantiate_perception_mapper(
                PERCEPTION_MAPPER_SPEC, {"plugin_manager": manager},
            )
        return mapper

    def memory_catalog(self) -> tuple[tuple[PluginDefinition, ...], dict[str, str]]:
        """Packaged memory definitions plus companion aliases for this catalog.

        The mapping sends each perception plugin that declares a companion to
        the memory catalog id for that entrypoint and configuration. Packaged
        configuration is never overwritten.
        """

        return _memory_definitions(self.plugins)

    def memory_id_for_selection(self, selected_plugin_ids: Sequence[str]) -> str:
        """Catalog id of the one selected companion, or the packaged default."""

        _definitions, perception_memory_ids = self.memory_catalog()
        return _selected_memory_id(perception_memory_ids, tuple(selected_plugin_ids))

    def memory_manager(self, selected_plugin_ids: Sequence[str]) -> PluginManager:
        """A core memory manager for this catalog and perception selection."""

        definitions, perception_memory_ids = self.memory_catalog()
        selected = _selected_memory_id(perception_memory_ids, tuple(selected_plugin_ids))
        if selected not in {item.plugin_id for item in definitions}:
            raise PluginCatalogError(f"unknown memory implementation {selected!r}")
        manager = PluginManager("memory", LocalPluginCatalog(definitions))
        manager.select((selected,))
        return manager


def packaged_plugin_catalog() -> PluginCatalog:
    """Expose every packaged plugin while keeping the algorithm's defaults."""

    defaults = PERCEPTION_ALGORITHMS[DEFAULT_PERCEPTION_ALGORITHM]["mapper_config"]
    default_ids = defaults["plugins"]
    # Defaults retain their existing display/execution order. Other entries are
    # available choices, not additional default selections.
    plugin_ids = list(dict.fromkeys([*default_ids, *sorted(PERCEPTION_PLUGIN_SPECS)]))
    descriptors = tuple(
        PluginDescriptor(
            plugin_id=plugin_id,
            name={
                "frame": "Front camera frame",
                "floor_plane": "Visible floor plane",
            }.get(plugin_id, plugin_id),
            description={
                "frame": "Frame dimensions and light statistics.",
                "floor_plane": "Visible floor and first-hit boundary evidence.",
            }.get(plugin_id, "Packaged perception plugin."),
            manifest_relative_path=f"packaged/{plugin_id}",
            manifest_path=None,
            entrypoint=PERCEPTION_PLUGIN_SPECS[plugin_id],
            config=dict(defaults.get("plugin_configs", {}).get(plugin_id, {})),
            memory={},
            output={
                "schema": PERCEPTION_TEXT_SCHEMA,
                "kind": {
                    "frame": "sensor_frame",
                    "floor_plane": "floor_boundary",
                }.get(plugin_id, "perception_evidence"),
            },
            inputs=[
                {
                    "name": "frame",
                    "component_id": "camera.rgb:front_camera",
                    "provider_spec": (
                        "implementations.decision_cycle.perception.components.camera:provide_camera_frame"
                    ),
                }
            ],
            model={},
            runtime={"python": "core", "support": "packaged"},
            status="ready",
            source="packaged",
            default=plugin_id in default_ids,
        )
        for plugin_id in plugin_ids
    )
    return _make_catalog(None, descriptors, explicit_root=False)


def discover_plugin_catalog(plugin_dir: str | os.PathLike[str] | None) -> PluginCatalog:
    """Discover every ``plugin.json`` below a canonical directory.

    Invalid packages are retained as unavailable descriptors.  A missing root
    is represented as a catalog error so the server can keep its long-lived
    page available while refusing activation at the named boundary.
    """

    if plugin_dir is None:
        return packaged_plugin_catalog()
    if isinstance(plugin_dir, str) and not plugin_dir.strip():
        return _error_catalog(None, "plugin root must be a non-empty path")
    raw_root = Path(plugin_dir).expanduser()
    try:
        root = raw_root.resolve(strict=False)
    except OSError as exc:
        return _error_catalog(None, f"could not canonicalize plugin root: {exc}")
    if not root.exists():
        return _error_catalog(root, f"plugin root does not exist: {root}")
    if not root.is_dir():
        return _error_catalog(root, f"plugin root is not a directory: {root}")
    try:
        mode = root.stat().st_mode
        if not mode & 0o444 or not mode & 0o111:
            return _error_catalog(root, f"plugin root is not readable: {root}")
        next(root.iterdir(), None)
    except OSError as exc:
        return _error_catalog(root, f"plugin root is not readable: {root}: {exc}")

    descriptors: list[PluginDescriptor] = []
    for manifest_path in _manifest_paths(root):
        descriptors.append(_read_descriptor(root, manifest_path))
    descriptors.sort(key=lambda item: (item.plugin_id, item.manifest_relative_path))
    counts: dict[str, int] = {}
    for item in descriptors:
        counts[item.plugin_id] = counts.get(item.plugin_id, 0) + 1
    if any(count > 1 for count in counts.values()):
        descriptors = [
            PluginDescriptor(
                **{
                    **item.__dict__,
                    "status": "unavailable",
                    "unavailable_reason": "duplicate plugin id",
                }
            )
            if counts[item.plugin_id] > 1
            else item
            for item in descriptors
        ]
    return _make_catalog(root, tuple(descriptors), explicit_root=True)


def build_plugin_catalog(plugin_dir: str | os.PathLike[str] | None) -> PluginCatalog:
    """Compatibility alias for callers that prefer a builder name."""

    return discover_plugin_catalog(plugin_dir)


def _make_catalog(
    root: Path | None,
    descriptors: Sequence[PluginDescriptor],
    *,
    explicit_root: bool,
    error: str | None = None,
) -> PluginCatalog:
    normalized = [
        {
            "id": item.plugin_id,
            "name": item.name,
            "description": item.description,
            "manifest_relative_path": item.manifest_relative_path,
            "entrypoint": item.entrypoint,
            "config": _json_safe(item.config),
            "memory": _json_safe(item.memory),
            "inputs": _json_safe(item.inputs),
            "output": _json_safe(item.output),
            "model": _json_safe(item.model),
            "runtime": _json_safe(item.runtime),
            "status": item.status,
            "unavailable_reason": item.unavailable_reason,
            "source": item.source,
            "default": item.default,
        }
        for item in descriptors
    ]
    payload = {
        "root": "explicit" if explicit_root else DEFAULT_PLUGIN_ROOT_ID,
        "explicit_root": explicit_root,
        "error": error,
        "plugins": normalized,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return PluginCatalog(
        root=root,
        plugins=tuple(descriptors),
        digest=digest,
        explicit_root=explicit_root,
        error=error,
    )


def _error_catalog(root: Path | None, message: str) -> PluginCatalog:
    return _make_catalog(root, (), explicit_root=root is not None, error=message)


def _manifest_paths(root: Path) -> Iterator[Path]:
    """Yield manifest files while pruning directory symlinks.

    A directory with a manifest is a plugin package; its models, sources and
    run outputs are not searched for further plugins.
    """

    def onerror(_error: OSError) -> None:
        return None

    for current, directories, files in os.walk(
        root, topdown=True, followlinks=False, onerror=onerror
    ):
        current_path = Path(current)
        if "plugin.json" in files:
            directories.clear()
            yield current_path / "plugin.json"
            continue
        symlink_dirs = []
        for name in list(directories):
            path = current_path / name
            if path.is_symlink():
                symlink_dirs.append(path)
                directories.remove(name)
        # A package symlink is represented as unavailable rather than followed.
        for path in sorted(symlink_dirs):
            manifest = path / "plugin.json"
            if manifest.exists() or manifest.is_symlink():
                yield manifest


def _read_descriptor(root: Path, manifest_path: Path) -> PluginDescriptor:
    relative = _relative_path(root, manifest_path)
    base = {
        "plugin_id": f"manifest:{relative}",
        "name": relative,
        "description": "",
        "manifest_relative_path": relative,
        "manifest_path": str(manifest_path),
        "entrypoint": None,
        "config": {},
        "memory": {},
        "inputs": [],
        "output": {},
        "model": {},
        "runtime": {},
        "status": "unavailable",
        "unavailable_reason": None,
        "source": "manifest",
        "default": False,
        "_directory": manifest_path.parent,
    }
    try:
        resolved_manifest = manifest_path.resolve(strict=False)
        if not _inside(root, resolved_manifest):
            base["unavailable_reason"] = "manifest path escapes plugin root"
            return PluginDescriptor(**base)
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("manifest must be a JSON object")
        raw_id = payload.get("id")
        if isinstance(raw_id, str):
            base["plugin_id"] = raw_id.strip() or base["plugin_id"]
        if not isinstance(raw_id, str) or not _SAFE_PLUGIN_ID.fullmatch(raw_id.strip()):
            raise ValueError("plugin id must match [A-Za-z0-9][A-Za-z0-9_.-]*")
        name = payload.get("name")
        description = payload.get("description")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("manifest name must be a non-empty string")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("manifest description must be a non-empty string")
        base["name"] = name.strip()
        base["description"] = description.strip()
        if payload.get("schema") != PLUGIN_MANIFEST_SCHEMA:
            raise ValueError(f"unsupported manifest schema {payload.get('schema')!r}")
        plugin = payload.get("plugin")
        if not isinstance(plugin, dict):
            raise ValueError("manifest lacks plugin object")
        entrypoint = plugin.get("entrypoint")
        if not isinstance(entrypoint, str) or not entrypoint.strip():
            raise ValueError("manifest lacks plugin.entrypoint")
        if entrypoint.count(":") != 1:
            raise ValueError("plugin.entrypoint must be module.path:ClassName")
        module_name, _, class_name = entrypoint.strip().partition(":")
        if not _SAFE_MODULE.fullmatch(module_name) or not _SAFE_SYMBOL.fullmatch(class_name):
            raise ValueError("plugin.entrypoint contains an unsafe module or class name")
        base["entrypoint"] = f"{module_name}:{class_name}"
        config = plugin.get("config") or {}
        if not isinstance(config, dict):
            raise ValueError("plugin.config must be an object")
        base["config"] = config
        memory = payload.get("memory") or {}
        if not isinstance(memory, dict):
            raise ValueError("manifest memory must be an object")
        if memory:
            implementation_id = memory.get("implementation_id")
            implementation_spec = memory.get("implementation_spec")
            implementation_config = memory.get("implementation_config") or {}
            if not isinstance(implementation_id, str) or not _SAFE_PLUGIN_ID.fullmatch(implementation_id):
                raise ValueError("memory.implementation_id must be a safe id")
            if not isinstance(implementation_spec, str) or implementation_spec.count(":") != 1:
                raise ValueError("memory.implementation_spec must be module.path:ClassName")
            module_name, _, class_name = implementation_spec.partition(":")
            if not _SAFE_MODULE.fullmatch(module_name) or not _SAFE_SYMBOL.fullmatch(class_name):
                raise ValueError("memory.implementation_spec contains an unsafe module or class name")
            if not isinstance(implementation_config, dict):
                raise ValueError("memory.implementation_config must be an object")
            base["memory"] = {
                "implementation_id": implementation_id,
                "implementation_spec": implementation_spec,
                "implementation_config": implementation_config,
            }
        output = payload.get("output")
        if not isinstance(output, dict):
            raise ValueError("manifest lacks output contract")
        if output.get("schema") != PERCEPTION_TEXT_SCHEMA:
            raise ValueError(f"output must declare {PERCEPTION_TEXT_SCHEMA}")
        if not isinstance(output.get("kind"), str) or not output["kind"].strip():
            raise ValueError("output contract must declare a non-empty kind")
        base["output"] = output
        model = payload.get("model") or {}
        if not isinstance(model, dict):
            raise ValueError("manifest model metadata must be an object")
        base["model"] = model
        runtime = payload.get("runtime")
        if not isinstance(runtime, dict):
            raise ValueError("manifest lacks runtime readiness metadata")
        base["runtime"] = runtime
        if "inputs" not in payload and "input" not in payload:
            raise ValueError("manifest lacks inputs contract")
        inputs = payload.get("inputs", payload.get("input"))
        if isinstance(inputs, dict):
            inputs = [inputs]
        if not isinstance(inputs, list):
            raise ValueError("manifest inputs must be an array or object")
        base["inputs"] = _normalize_inputs(inputs)
        reason = _readiness_reason(root, manifest_path, base)
        base["status"] = "ready" if reason is None else "unavailable"
        base["unavailable_reason"] = reason
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        base["unavailable_reason"] = str(exc)
    return PluginDescriptor(**base)


def _readiness_reason(root: Path, manifest_path: Path, data: dict[str, Any]) -> str | None:
    runtime = data["runtime"]
    python_runtime = runtime.get("python")
    if python_runtime == "core":
        pass
    elif isinstance(python_runtime, str) and python_runtime.strip():
        # The workbench has no isolated worker composition adapter.  Keeping
        # this visible is safer than claiming the candidate can run in-core.
        return "isolated runtime is not supported by the workbench"
    else:
        return "runtime.python must be 'core' or a declared isolated runtime"

    requirements = runtime.get("requirements")
    if requirements is not None:
        if not isinstance(requirements, str) or not requirements.strip():
            return "runtime.requirements must be a path string"
        requirement_path = _bounded_child(manifest_path.parent, requirements)
        if requirement_path is None:
            return "runtime requirements path escapes plugin root"
        if not requirement_path.is_file():
            return "declared runtime requirements file is missing"

    model = data.get("model") or {}
    if model:
        filename = model.get("filename")
        if not isinstance(filename, str) or not filename.strip():
            return "model.filename must be a path string"
        model_path = _bounded_child(manifest_path.parent / "models", filename)
        if model_path is None:
            return "model path escapes plugin root"
        if not model_path.is_file():
            return "declared model file is missing"
        expected = model.get("sha256")
        if isinstance(expected, str) and expected:
            observed = _sha256_file(model_path)
            if observed != expected:
                return "declared model sha256 does not match"

    origin = _entrypoint_origin(root, data["entrypoint"])
    if origin is None:
        return "entrypoint cannot be resolved inside the plugin root"
    if not _inside(root, origin):
        return "entrypoint resolves outside plugin root"
    _module_name, _, class_name = data["entrypoint"].partition(":")
    try:
        module_source = origin.read_text(encoding="utf-8")
        module_tree = ast.parse(module_source, filename=str(origin))
    except (OSError, UnicodeError, SyntaxError) as exc:
        return f"entrypoint module cannot be parsed: {exc}"
    declared_symbols = {
        node.name
        for node in module_tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for node in module_tree.body:
        if isinstance(node, ast.ImportFrom):
            declared_symbols.update(
                alias.asname or alias.name.rsplit(".", 1)[-1]
                for alias in node.names
            )
        elif isinstance(node, ast.Import):
            declared_symbols.update(
                alias.asname or alias.name.rsplit(".", 1)[-1]
                for alias in node.names
            )
    if class_name not in declared_symbols:
        return f"entrypoint class {class_name!r} is not declared in module"
    return None


def _normalize_inputs(inputs: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    names: set[str] = set()
    components: set[str] = set()
    for item in inputs:
        if not isinstance(item, dict):
            raise ValueError("manifest input entries must be objects")
        name = item.get("name")
        component_id = item.get("component_id")
        provider_spec = item.get("provider_spec")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (name, component_id, provider_spec)
        ):
            raise ValueError(
                "manifest input entries require name, component_id, and provider_spec"
            )
        name = name.strip()
        component_id = component_id.strip()
        provider_spec = provider_spec.strip()
        module_name, separator, callable_name = provider_spec.partition(":")
        if (
            separator != ":"
            or not _SAFE_MODULE.fullmatch(module_name)
            or not _SAFE_SYMBOL.fullmatch(callable_name)
        ):
            raise ValueError("manifest input provider_spec contains an unsafe symbol")
        if name in names or component_id in components:
            raise ValueError("manifest input names and component ids must be unique")
        names.add(name)
        components.add(component_id)
        normalized.append(
            {
                **item,
                "name": name,
                "component_id": component_id,
                "provider_spec": provider_spec,
            }
        )
    if not normalized:
        raise ValueError("manifest inputs contract must contain at least one input")
    return normalized


def _entrypoint_origin(root: Path, entrypoint: str) -> Path | None:
    module_name, _, _class_name = entrypoint.partition(":")
    if not module_name or not _SAFE_MODULE.fullmatch(module_name):
        return None
    # Module-style manifests in the repository are rooted above the declared
    # plugin directory (for example ``lab.plugins.perception.foo``). Resolve
    # them by dropping leading packages rather than importing a parent package;
    # discovery must not execute unselected plugin code.
    module_parts = module_name.split(".")
    for start in range(len(module_parts)):
        relative = Path(*module_parts[start:])
        for candidate in (root / (str(relative) + ".py"), root / relative / "__init__.py"):
            if candidate.is_file():
                try:
                    return candidate.resolve()
                except OSError:
                    return None
    return None


_SAFE_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _bounded_child(parent: Path, raw: str) -> Path | None:
    candidate = Path(raw)
    if candidate.is_absolute():
        return None
    try:
        resolved_parent = parent.resolve(strict=False)
        resolved = (parent / candidate).resolve(strict=False)
    except OSError:
        return None
    return resolved if _inside(resolved_parent, resolved) else None


def _sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


@contextmanager
def _import_root(root: Path) -> Iterator[None]:
    additions = [str(root), str(root.parent)]
    original = list(sys.path)
    for item in reversed(additions):
        if item and item not in sys.path:
            sys.path.insert(0, item)
    try:
        yield
    finally:
        sys.path[:] = original


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return str(value)
    return value


__all__ = [
    "DEFAULT_PLUGIN_ROOT_ID",
    "PLUGIN_CATALOG_SCHEMA",
    "PluginCatalog",
    "PluginCatalogError",
    "PluginDescriptor",
    "build_plugin_catalog",
    "discover_plugin_catalog",
    "packaged_plugin_catalog",
]
