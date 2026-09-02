"""Manifest-backed plugin discovery and selection for the replay workbench.

The workbench deliberately has a smaller plugin boundary than the general lab
candidate tooling.  Discovery is metadata-only: a manifest is parsed and
validated without importing its entrypoint.  Importing and constructing a
plugin happens only after the operator selects its catalog id for a replay.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Sequence

from autonomy.perception import PERCEPTION_TEXT_SCHEMA, PerceptionMapper
from autonomy.perception.activation import instantiate_perception_mapper
from implementations.perception.catalog import (
    PERCEPTION_MAPPER_SPEC,
    PERCEPTION_PLUGIN_SPECS,
)


PLUGIN_CATALOG_SCHEMA = "workbench_plugin_catalog_v1"
PLUGIN_MANIFEST_SCHEMA = "automa_lab_perception_plugin_v0"
DEFAULT_PLUGIN_ROOT_ID = "packaged:implementations.perception.catalog"
_SAFE_PLUGIN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SAFE_SYMBOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PluginCatalogError(ValueError):
    """A bounded catalog or selection boundary failure."""

    boundary = "plugin_catalog"


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

    def normalize_selection(
        self,
        active_ids: Sequence[str] | None,
        *,
        require_explicit_selection: bool | None = None,
    ) -> tuple[str, ...]:
        """Validate and normalize ids in deterministic catalog order."""

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
        by_id = {item.plugin_id: item for item in self.plugins}
        unknown = sorted(set(values) - set(by_id))
        if unknown:
            raise PluginCatalogError(
                "unknown plugin id(s): " + ", ".join(unknown)
            )
        unavailable = sorted(
            value for value in values if not by_id[value].ready
        )
        if unavailable:
            reasons = "; ".join(
                f"{value}: {by_id[value].unavailable_reason or 'unavailable'}"
                for value in unavailable
            )
            raise PluginCatalogError(f"unavailable plugin id(s): {reasons}")
        if require_explicit_selection is None:
            require_explicit_selection = self.explicit_root
        if require_explicit_selection and not values:
            raise PluginCatalogError(
                "an explicit plugin directory requires at least one active plugin id"
            )
        if not values:
            raise PluginCatalogError("active_plugin_ids must contain at least one ready plugin")
        order = {item.plugin_id: index for index, item in enumerate(self.plugins)}
        return tuple(sorted(values, key=lambda value: order[value]))

    def build_mapper(self, active_ids: Sequence[str]) -> PerceptionMapper:
        """Instantiate exactly the selected core-runtime manifest plugins."""

        selected = self.normalize_selection(active_ids, require_explicit_selection=False)
        descriptors = {item.plugin_id: item for item in self.plugins}
        plugin_ids = list(selected)
        plugin_specs: dict[str, str] = {}
        plugin_configs: dict[str, dict[str, Any]] = {}

        for plugin_id in plugin_ids:
            descriptor = descriptors.get(plugin_id)
            if descriptor is None:
                spec = PERCEPTION_PLUGIN_SPECS.get(plugin_id)
                if spec is None:
                    raise PluginCatalogError(f"plugin {plugin_id!r} is not in the catalog")
                plugin_specs[plugin_id] = spec
                plugin_configs[plugin_id] = {}
            else:
                if not descriptor.ready:
                    raise PluginCatalogError(
                        f"plugin {plugin_id!r} is unavailable: "
                        f"{descriptor.unavailable_reason or 'not ready'}"
                    )
                if not descriptor.entrypoint:
                    raise PluginCatalogError(f"plugin {plugin_id!r} has no entrypoint")
                plugin_specs[plugin_id] = descriptor.entrypoint
                plugin_configs[plugin_id] = dict(descriptor.config)

        if self.root is not None:
            with _import_root(self.root):
                mapper = instantiate_perception_mapper(
                    PERCEPTION_MAPPER_SPEC,
                    {
                        "plugins": plugin_ids,
                        "plugin_specs": plugin_specs,
                        "plugin_configs": plugin_configs,
                    },
                )
        else:
            mapper = instantiate_perception_mapper(
                PERCEPTION_MAPPER_SPEC,
                {
                    "plugins": plugin_ids,
                    "plugin_specs": plugin_specs,
                    "plugin_configs": plugin_configs,
                },
            )

        # Runtime classes use implementation ids (for example
        # ``floor-continuity-v1``).  The workbench's stable public provenance is
        # the manifest/catalog id, so alias the instantiated objects after the
        # mapper has validated their contracts.
        for configured_id, plugin in zip(plugin_ids, mapper.plugins, strict=True):
            plugin.plugin_id = configured_id
        return mapper


def packaged_plugin_catalog() -> PluginCatalog:
    """Return the backward-compatible packaged lightweight catalog."""

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
            config={},
            output={
                "schema": PERCEPTION_TEXT_SCHEMA,
                "kind": "sensor_frame" if plugin_id == "frame" else "floor_boundary",
            },
            inputs=[
                {
                    "name": "frame",
                    "component_id": "camera.rgb:front_camera",
                    "provider_spec": (
                        "implementations.perception.components.camera:provide_camera_frame"
                    ),
                }
            ],
            model={},
            runtime={"python": "core", "support": "packaged"},
            status="ready",
            source="packaged",
            default=True,
        )
        for plugin_id in ("frame", "floor_plane")
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
    """Yield manifest files while pruning directory symlinks."""

    def onerror(_error: OSError) -> None:
        return None

    for current, directories, files in os.walk(
        root, topdown=True, followlinks=False, onerror=onerror
    ):
        current_path = Path(current)
        for name in sorted(files):
            if name == "plugin.json":
                yield current_path / name
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
    relative = Path(*module_name.split("."))
    candidates = (root / (str(relative) + ".py"), root / relative / "__init__.py")
    for candidate in candidates:
        if candidate.is_file():
            try:
                return candidate.resolve()
            except OSError:
                return None
    # Module-style manifests in the repository are rooted above the declared
    # plugin directory (for example ``lab.plugins.perception.foo``).  Resolve
    # those identities by suffix search rather than importing a parent package;
    # discovery must not execute unselected plugin code.
    module_parts = tuple(module_name.split("."))
    tail = module_parts[-3:] if len(module_parts) >= 3 else module_parts
    for candidate in sorted(root.rglob("*.py")):
        try:
            relative_parts = candidate.relative_to(root).parts
        except ValueError:
            continue
        if len(relative_parts) >= len(tail) and tuple(relative_parts[-len(tail):]) == (
            *tail[:-1],
            f"{tail[-1]}.py",
        ):
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
