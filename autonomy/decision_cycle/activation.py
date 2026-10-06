"""Activation documents: which plugins each cycle step runs.

Every step is activated the same way. ``runtime/<step>/active.json`` holds a
``StepActivation``: the step ID, the selected plugin IDs in order
(``plugins``), each available plugin's ``module.path:Name`` entrypoint
(``plugin_specs``), and the keyword arguments each is constructed with
(``plugin_configs``). ``metadata`` is free-form description for tools, such as
the preset a selection was built from; runners do not read it.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autonomy.plugins import PluginManager

STEP_ACTIVATION_SCHEMA = "automa_step_activation_v0"
# The cycle's steps in the order they run.
STEPS = ("perception", "observation", "memory", "proposal", "plan", "action")
# The steps that decide the cycle's action; their activations identify a decision generation.
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
        self.plugin_manager()

    def plugin_manager(self) -> PluginManager:
        """A fresh manager for this step with the activation's selection applied."""

        manager = PluginManager.from_specs(self.step, self.plugin_specs, self.plugin_configs)
        manager.select(self.plugins)
        return manager

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


def activation_generation_id(
    activations: Mapping[str, StepActivation | Mapping[str, Any] | None],
    *,
    prefix: str = "generation",
) -> str:
    """A content-derived ID for a set of step activations.

    Equal selections, specs, and configs give the same ID regardless of where
    or when they were written; ``metadata`` is not part of the identity.
    """

    canonical: dict[str, Any] = {}
    for step in sorted(activations):
        activation = activations[step]
        if activation is None:
            canonical[require_step(step)] = None
            continue
        payload = (
            activation.to_payload() if isinstance(activation, StepActivation) else dict(activation)
        )
        payload.pop("metadata", None)
        canonical[require_step(step)] = payload
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"{prefix}-{digest[:16]}"
