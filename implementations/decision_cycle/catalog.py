"""Packaged plugins for every cycle step, and activations built from them.

Each step's ``<step>/catalog.json`` lists:

- ``plugins``: each packaged plugin's entrypoint (``spec``), a description,
  and its default config. Each plugin declares its own ID; ``step_plugins``
  reads those IDs and refuses a step in which two packaged plugins declare
  the same one, since the file's owner resolves such conflicts.
- ``presets``, optional: named, ordered selections of those plugins, with
  config overrides (``plugin_configs``) for some of them. The steps whose
  file has presets are the ``PRESET_STEPS``.
- ``default``: the step's default selection, a ``preset`` where the step has
  presets and a ``plugins`` list otherwise.

``STEP_PLUGINS``, ``STEP_PRESETS``, ``DEFAULT_STEP_PRESETS`` and
``DEFAULT_STEP_PLUGINS`` hold what the files list. ``packaged_activation``
builds a ``StepActivation`` that makes every packaged plugin of the step
available, selects the requested ones in order, and applies config
overrides. ``preset_activation`` builds an activation from a named preset of
a step. ``selection_activation`` is every step's selection: a preset or a
plugin list, else the step's default.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from functools import cache
from pathlib import Path
from typing import Any

from autonomy.decision_cycle.activation import STEPS, StepActivation, require_step, step_activation
from autonomy.plugins import LocalPluginCatalog, PluginDefinition

CATALOG_DIR = Path(__file__).resolve().parent


def catalog_path(step: str) -> Path:
    """The file that lists ``step``'s packaged plugins, presets and default."""

    return CATALOG_DIR / step / "catalog.json"


def _read_catalog(step: str) -> dict[str, Any]:
    path = catalog_path(step)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc


_CATALOGS: dict[str, dict[str, Any]] = {step: _read_catalog(step) for step in STEPS}

STEP_PLUGINS: dict[str, tuple[dict[str, Any], ...]] = {
    step: tuple(catalog["plugins"]) for step, catalog in _CATALOGS.items()
}
STEP_PRESETS: dict[str, dict[str, dict[str, Any]]] = {
    step: catalog["presets"] for step, catalog in _CATALOGS.items() if "presets" in catalog
}
DEFAULT_STEP_PRESETS: dict[str, str] = {
    step: _CATALOGS[step]["default"]["preset"] for step in STEP_PRESETS
}
DEFAULT_STEP_PLUGINS: dict[str, tuple[str, ...]] = {
    step: tuple(
        STEP_PRESETS[step][DEFAULT_STEP_PRESETS[step]]["plugins"]
        if step in STEP_PRESETS
        else catalog["default"]["plugins"]
    )
    for step, catalog in _CATALOGS.items()
}
# The steps whose packaged selections include named presets.
# Empty replay follows CLEARABLE_STEPS, the steps that can run no plugins.
PRESET_STEPS: tuple[str, ...] = tuple(STEP_PRESETS)
# The ``preset`` metadata of a selection that equals no preset.
CUSTOM_PRESET = "custom"


def step_plugins(step: str) -> dict[str, dict[str, Any]]:
    """The step's packaged plugin entries by the ID each plugin declares.

    Raises ``DuplicatePluginIdError`` when two packaged plugins of the step
    declare the same ID.
    """

    return dict(_declared_entries(require_step(step)))


@cache
def _declared_entries(step: str) -> dict[str, dict[str, Any]]:
    # Reading a declared ID imports the plugin's module; do it once per step.
    catalog = LocalPluginCatalog()
    entries: dict[str, dict[str, Any]] = {}
    for entry in STEP_PLUGINS[step]:
        definition = PluginDefinition.declared(step, entry["spec"])
        catalog.register(definition)
        entries[definition.plugin_id] = entry
    return entries


def packaged_activation(
    step: str,
    plugins: Sequence[str] | None = None,
    *,
    config_overrides: Mapping[str, Mapping[str, Any]] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> StepActivation:
    """Select packaged ``plugins`` (the step's default when omitted)."""

    catalog = step_plugins(step)
    selected = list(DEFAULT_STEP_PLUGINS[step] if plugins is None else plugins)
    unknown = [plugin_id for plugin_id in selected if plugin_id not in catalog]
    overrides = dict(config_overrides or {})
    unknown += [plugin_id for plugin_id in overrides if plugin_id not in catalog]
    if unknown:
        known = ", ".join(sorted(catalog)) or "(none)"
        raise ValueError(
            f"unknown {step} plugin(s) {', '.join(sorted(set(unknown)))}; known: {known}"
        )
    configs: dict[str, dict[str, Any]] = {}
    for plugin_id, entry in catalog.items():
        config = deepcopy(dict(entry["default_config"]))
        config.update(deepcopy(dict(overrides.get(plugin_id, {}))))
        if config:
            configs[plugin_id] = config
    return step_activation(
        step,
        selected,
        {plugin_id: entry["spec"] for plugin_id, entry in catalog.items()},
        configs,
        metadata=metadata,
    )


def step_presets(step: str) -> dict[str, dict[str, Any]]:
    """The named presets of ``step``; perception, memory, proposal and plan have them."""

    require_step(step)
    try:
        return STEP_PRESETS[step]
    except KeyError:
        raise ValueError(
            f"{step} has no presets; presets exist for: {', '.join(STEP_PRESETS)}"
        ) from None


def preset_activation(step: str, preset: str) -> StepActivation:
    """The activation of a named preset; it records the name in ``preset`` metadata."""

    presets = step_presets(step)
    try:
        entry = presets[preset]
    except KeyError as exc:
        known = ", ".join(sorted(presets))
        raise ValueError(f"unknown {step} preset {preset!r}; known: {known}") from exc
    return packaged_activation(
        step,
        entry["plugins"],
        config_overrides=entry.get("plugin_configs"),
        metadata={"preset": preset},
    )


def plugin_list_preset(step: str, plugins: Sequence[str]) -> str:
    """The preset of ``step`` whose plugins are exactly ``plugins``, else ``custom``."""

    return next(
        (
            name
            for name, entry in step_presets(step).items()
            if list(entry["plugins"]) == list(plugins) and not entry.get("plugin_configs")
        ),
        CUSTOM_PRESET,
    )


def selection_activation(
    step: str,
    *,
    preset: str | None = None,
    plugins: Sequence[str] | None = None,
) -> StepActivation:
    """An activation of ``step`` from a named preset or an ordered plugin list.

    A plugin list, which may be empty, is labeled with the preset it equals,
    else ``custom``; with neither argument the step's default preset applies.
    A step without presets takes a plugin list, else its default plugins.
    """

    if preset is not None and plugins is not None:
        raise ValueError(f"choose either a {step} preset or plugins, not both")
    if preset is None and require_step(step) not in STEP_PRESETS:
        return packaged_activation(step, plugins)
    if plugins is not None:
        return packaged_activation(
            step, plugins, metadata={"preset": plugin_list_preset(step, plugins)}
        )
    return preset_activation(step, preset or DEFAULT_STEP_PRESETS[step])
