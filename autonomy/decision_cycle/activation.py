"""Activation documents: which plugins each cycle step runs.

Every step is activated the same way. ``runtime/<step>/active.json`` holds a
``StepActivation``: the step ID, the selected plugin IDs in order
(``plugins``), each available plugin's ``module.path:Name`` entrypoint
(``plugin_specs``), and the keyword arguments each is constructed with
(``plugin_configs``). ``metadata`` carries the preset label and the source
provenance a generation hashes. Runners do not branch on the preset.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from autonomy.plugins import PluginDefinition, PluginManagementError

STEP_ACTIVATION_SCHEMA = "automa_step_activation"
# The cycle's steps in the order they run.
STEPS = ("perception", "observation", "memory", "proposal", "plan", "action")
# The three steps that decide the cycle's action. Pipeline identity uses all STEPS.
DECISION_STEPS = ("proposal", "plan", "action")
ACTIVATION_FILENAME = "active.json"
_PAYLOAD_KEYS = {"schema", "step", "plugins", "plugin_specs", "plugin_configs", "metadata"}


def require_step(step: object) -> str:
    if step not in STEPS:
        raise ValueError(f"unknown cycle step {step!r}; steps are {', '.join(STEPS)}")
    return step  # type: ignore[return-value]


@dataclass(frozen=True)
class StepActivation:
    """One step's plugin selection, validated against its specs on construction."""

    step: str
    plugins: tuple[str, ...]
    plugin_specs: Mapping[str, str]
    plugin_configs: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    source_path: Path | None = None

    def __post_init__(self) -> None:
        require_step(self.step)
        plugins = self.plugins
        if isinstance(plugins, (str, bytes)) or not isinstance(plugins, (list, tuple)):
            raise ValueError(f"{self.step} plugins must be a list of plugin IDs")
        if not all(isinstance(item, str) for item in plugins):
            raise ValueError(f"{self.step} plugins must be a list of plugin IDs")
        if len(plugins) != len(set(plugins)):
            raise ValueError(f"{self.step} plugins must be unique")
        if not isinstance(self.plugin_specs, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in self.plugin_specs.items()
        ):
            raise ValueError(f"{self.step} plugin_specs must map plugin IDs to entrypoints")
        if not isinstance(self.plugin_configs, Mapping) or not all(
            isinstance(value, Mapping) for value in self.plugin_configs.values()
        ):
            raise ValueError(f"{self.step} plugin_configs must map plugin IDs to objects")
        if not isinstance(self.metadata, Mapping):
            raise ValueError(f"{self.step} activation metadata must be an object")
        object.__setattr__(self, "plugins", tuple(plugins))
        object.__setattr__(self, "plugin_specs", dict(self.plugin_specs))
        object.__setattr__(
            self,
            "plugin_configs",
            {key: deepcopy(dict(value)) for key, value in self.plugin_configs.items()},
        )
        object.__setattr__(self, "metadata", deepcopy(dict(self.metadata)))
        # Unknown plugin IDs fail here, before any plugin is constructed.
        selected_definitions(self)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": STEP_ACTIVATION_SCHEMA,
            "step": self.step,
            "plugins": list(self.plugins),
            "plugin_specs": dict(self.plugin_specs),
            "plugin_configs": deepcopy(dict(self.plugin_configs)),
        }
        if self.metadata:
            payload["metadata"] = deepcopy(dict(self.metadata))
        return payload


def step_activation(
    step: str,
    plugins: list[str] | tuple[str, ...],
    plugin_specs: Mapping[str, str],
    plugin_configs: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> StepActivation:
    return StepActivation(
        step=step,
        plugins=tuple(plugins),
        plugin_specs=plugin_specs,
        plugin_configs=plugin_configs or {},
        metadata=metadata or {},
    )


def step_activation_from_payload(
    payload: Mapping[str, Any],
    *,
    step: str | None = None,
    source_path: Path | None = None,
) -> StepActivation:
    where = f": {source_path}" if source_path is not None else ""
    if not isinstance(payload, Mapping):
        raise ValueError(f"step activation must be a JSON object{where}")
    if payload.get("schema") != STEP_ACTIVATION_SCHEMA:
        raise ValueError(
            f"step activation has unsupported schema {payload.get('schema')!r}{where}"
        )
    unknown = sorted(set(payload) - _PAYLOAD_KEYS)
    if unknown:
        raise ValueError(f"step activation has unknown keys {', '.join(unknown)}{where}")
    declared = payload.get("step")
    if step is not None and declared != step:
        raise ValueError(f"activation is for step {declared!r}, not {step!r}{where}")
    return StepActivation(
        step=declared,
        plugins=payload.get("plugins"),  # type: ignore[arg-type]
        plugin_specs=payload.get("plugin_specs"),  # type: ignore[arg-type]
        plugin_configs=payload.get("plugin_configs", {}),
        metadata=payload.get("metadata", {}),
        source_path=source_path,
    )


def step_activation_path(runtime_root: Path, step: str) -> Path:
    """``runtime_root/<step>/active.json``."""

    return Path(runtime_root) / require_step(step) / ACTIVATION_FILENAME


def load_activation_json(text: str) -> Any:
    """Parse an activation document, refusing a key repeated in one object.

    ``json.loads`` keeps the last of repeated keys, so a plugin ID listed twice
    in ``plugin_specs`` or ``plugin_configs`` would silently lose a definition.
    """

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"activation repeats key {key!r}")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=unique)


def read_step_activation(path: Path, step: str | None = None) -> StepActivation:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{step or 'step'} activation is missing: {path}")
    try:
        payload = load_activation_json(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{exc}: {path}") from exc
    return step_activation_from_payload(payload, step=step, source_path=path)


def read_step_activation_if_present(path: Path, step: str) -> StepActivation | None:
    path = Path(path)
    return read_step_activation(path, step) if path.exists() else None


def write_step_activation(path: Path, activation: StepActivation) -> Path:
    """Write the activation atomically and return its path."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(activation.to_payload(), indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=".active-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return path


def activation_presets(
    activations: Mapping[str, StepActivation | Mapping[str, Any] | None],
) -> dict[str, str]:
    """Each step's preset label; steps without one are omitted."""

    presets: dict[str, str] = {}
    for step, activation in activations.items():
        if isinstance(activation, StepActivation):
            metadata: Any = activation.metadata
        else:
            metadata = activation.get("metadata") if isinstance(activation, Mapping) else None
        preset = metadata.get("preset") if isinstance(metadata, Mapping) else None
        if isinstance(preset, str):
            presets[step] = preset
    return presets


def selected_definitions(activation: StepActivation) -> tuple[PluginDefinition, ...]:
    """The activation's plugins in order. An ID with no spec is unknown."""

    missing = [plugin_id for plugin_id in activation.plugins if plugin_id not in activation.plugin_specs]
    if missing:
        raise PluginManagementError(
            f"unknown plugin id {missing[0]!r} for step {activation.step!r}"
        )
    revisions = activation.metadata.get("plugin_revisions")
    revisions = revisions if isinstance(revisions, Mapping) else {}
    definitions: list[PluginDefinition] = []
    for plugin_id in activation.plugins:
        spec = activation.plugin_specs[plugin_id]
        metadata: dict[str, Any] = {}
        module, separator, _attribute = spec.partition(":")
        file_name = Path(module).name
        if separator and _revision_filename(file_name):
            metadata["source_file"] = file_name
        revision = revisions.get(plugin_id)
        if isinstance(revision, str) and revision:
            metadata["revision"] = revision
        definitions.append(PluginDefinition(
            step=activation.step,
            plugin_id=plugin_id,
            entrypoint=spec,
            config=activation.plugin_configs.get(plugin_id, {}),
            metadata=metadata,
        ))
    return tuple(definitions)


def activation_for_definitions(
    step: str,
    definitions: tuple[PluginDefinition, ...] | list[PluginDefinition],
    *,
    metadata: Mapping[str, Any] | None = None,
    source_path: Path | None = None,
) -> StepActivation:
    """An activation whose selected definitions are exactly ``definitions``.

    Specs are the definitions' executable entrypoints, so an uploaded revision
    stays the file it was built from. Display metadata is copied through and
    is not part of the executable identity.
    """

    specs: dict[str, str] = {}
    configs: dict[str, Mapping[str, Any]] = {}
    revisions: dict[str, str] = {}
    for definition in definitions:
        if definition.step != step:
            raise PluginManagementError(
                f"plugin {definition.plugin_id!r} belongs to {definition.step!r}, not {step!r}"
            )
        specs[definition.plugin_id] = definition.executable_entrypoint
        if definition.config:
            configs[definition.plugin_id] = dict(definition.config)
        revision = definition.metadata.get("revision")
        if isinstance(revision, str) and revision:
            revisions[definition.plugin_id] = revision
    kept = dict(metadata or {})
    if revisions:
        kept["plugin_revisions"] = revisions
    else:
        kept.pop("plugin_revisions", None)
    return StepActivation(
        step=step,
        plugins=tuple(definition.plugin_id for definition in definitions),
        plugin_specs=specs,
        plugin_configs=configs,
        metadata=kept,
        source_path=source_path,
    )


def stamp_source(
    activation: StepActivation,
    *,
    host_name: str | None = None,
    release: Mapping[str, Any] | None = None,
) -> StepActivation:
    """Fill source provenance that is not already on ``activation``.

    A recorded release and host name are left as they were. Recording can copy
    this provenance later without changing the generation.
    """

    metadata = dict(activation.metadata)
    if isinstance(host_name, str) and host_name and not isinstance(metadata.get("host_name"), str):
        metadata["host_name"] = host_name
    if isinstance(release, Mapping) and isinstance(release.get("tree_sha256"), str):
        bundle = dict(metadata.get("controller_bundle") or {})
        existing = bundle.get("release")
        tree = existing.get("tree_sha256") if isinstance(existing, Mapping) else None
        if not isinstance(tree, str) or not tree:
            bundle["release"] = {
                key: value for key, value in release.items() if key in {"tree_sha256", "created_at_ms"}
            }
            metadata["controller_bundle"] = bundle
    if metadata == dict(activation.metadata):
        return activation
    return replace(activation, metadata=metadata)


def _revision_filename(name: str) -> bool:
    stem = name[:-3] if name.endswith(".py") else ""
    return len(stem) == 64 and all(character in "0123456789abcdef" for character in stem)


def _canonical_spec(spec: str) -> str:
    """An entrypoint whose uploaded file is named by its revision, not its path."""

    module, separator, attribute = spec.partition(":")
    name = Path(module).name
    if separator and _revision_filename(name):
        return f"{name}:{attribute}"
    return spec


def _source_identity(metadata: Mapping[str, Any], plugins: list[str]) -> dict[str, Any]:
    """Release and upload revisions. Preset labels and absolute paths are omitted."""

    identity: dict[str, Any] = {}
    host_name = metadata.get("host_name")
    if isinstance(host_name, str) and host_name:
        identity["host_name"] = host_name
    bundle = metadata.get("controller_bundle")
    release = bundle.get("release") if isinstance(bundle, Mapping) else None
    tree = release.get("tree_sha256") if isinstance(release, Mapping) else None
    if isinstance(tree, str) and tree:
        identity["tree_sha256"] = tree
    revisions = metadata.get("plugin_revisions")
    if isinstance(revisions, Mapping):
        kept = {
            key: value for key, value in revisions.items()
            if key in plugins and isinstance(key, str) and isinstance(value, str) and value
        }
        if kept:
            identity["plugin_revisions"] = {key: kept[key] for key in sorted(kept)}
    return identity


def executable_document(activation: StepActivation | Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The fields that make one step's selection the same code and configuration."""

    if activation is None:
        return None
    if isinstance(activation, StepActivation):
        plugins = list(activation.plugins)
        specs = dict(activation.plugin_specs)
        configs = {key: dict(value) for key, value in activation.plugin_configs.items()}
        metadata = activation.metadata
    elif isinstance(activation, Mapping):
        plugins = list(activation.get("plugins") or [])
        specs = dict(activation.get("plugin_specs") or {})
        raw_configs = activation.get("plugin_configs") or {}
        configs = {key: dict(value) for key, value in raw_configs.items()} if isinstance(raw_configs, Mapping) else {}
        metadata = activation.get("metadata") or {}
        metadata = metadata if isinstance(metadata, Mapping) else {}
    else:
        raise TypeError("activation must be a StepActivation, a payload, or None")
    return {
        "plugins": plugins,
        "plugin_specs": {plugin_id: _canonical_spec(specs[plugin_id]) for plugin_id in plugins if plugin_id in specs},
        "plugin_configs": {
            plugin_id: configs[plugin_id] for plugin_id in plugins if plugin_id in configs and configs[plugin_id]
        },
        "source": _source_identity(metadata, plugins),
    }


def activation_generation_id(
    activations: Mapping[str, StepActivation | Mapping[str, Any] | None],
    *,
    prefix: str = "generation",
) -> str:
    """A content-derived ID for a set of step activations.

    Equal plugin order, specs, configs, and source provenance give the same ID.
    Display metadata and absolute install paths do not. An absent step is null.
    """

    canonical: dict[str, Any] = {}
    for step in sorted(activations):
        canonical[require_step(step)] = executable_document(activations[step])
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"{prefix}-{digest[:16]}"
