"""Packaged plugins for every cycle step, and activations built from them.

``STEP_PLUGINS`` lists, per step, each packaged plugin's entrypoint
(``spec``), a description, and its default config. Each plugin declares its
own ID; ``step_plugins`` reads those IDs and refuses a step in which two
packaged plugins declare the same one, since this directory's owner resolves
such conflicts. ``DEFAULT_STEP_PLUGINS`` is each step's default selection. ``packaged_activation`` builds a
``StepActivation`` that makes every packaged plugin of the step available,
selects the requested ones in order, and applies config overrides.
``perception_preset_activation`` builds one from a named perception
preset.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from functools import cache
from typing import Any

from autonomy.decision_cycle.activation import STEPS, StepActivation, require_step, step_activation
from autonomy.plugins import LocalPluginCatalog, PluginDefinition
from implementations.decision_cycle.action.catalog import ACTION_PLUGINS, DEFAULT_ACTION_PLUGINS
from implementations.decision_cycle.memory.catalog import DEFAULT_MEMORY_PLUGINS, MEMORY_PLUGINS
from implementations.decision_cycle.perception.catalog import (
    PERCEPTION_PLUGINS,
)
from implementations.decision_cycle.perception.presets import (
    DEFAULT_PERCEPTION_PLUGINS,
    PERCEPTION_PRESETS,
)
from implementations.decision_cycle.proposal.catalog import (
    DEFAULT_PROPOSAL_PLUGINS,
    PROPOSAL_PLUGINS,
)

STEP_PLUGINS: dict[str, tuple[dict[str, Any], ...]] = {
    "perception": PERCEPTION_PLUGINS,
    "observation": (
        {
            "spec": "autonomy.decision_cycle.observation.perception_summary:PerceptionSummary",
            "description": "Perception evidence plus the sensor snapshot as the frame record.",
            "default_config": {},
        },
    ),
    "memory": MEMORY_PLUGINS,
    "proposal": PROPOSAL_PLUGINS,
    "plan": (
        {
            "spec": "autonomy.decision_cycle.plan.highest_confidence:HighestConfidencePlan",
            "description": "Select the most confident active candidate; otherwise plan idle.",
            "default_config": {},
        },
    ),
    "action": ACTION_PLUGINS,
}

DEFAULT_STEP_PLUGINS: dict[str, tuple[str, ...]] = {
    "perception": DEFAULT_PERCEPTION_PLUGINS,
    "observation": ("perception_summary",),
    "memory": DEFAULT_MEMORY_PLUGINS,
    "proposal": DEFAULT_PROPOSAL_PLUGINS,
    "plan": ("highest_confidence",),
    "action": DEFAULT_ACTION_PLUGINS,
}

assert tuple(STEP_PLUGINS) == STEPS


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


def perception_preset_activation(preset: str) -> StepActivation:
    try:
        entry = PERCEPTION_PRESETS[preset]
    except KeyError as exc:
        known = ", ".join(sorted(PERCEPTION_PRESETS))
        raise ValueError(f"unknown perception preset {preset!r}; known: {known}") from exc
    return packaged_activation(
        "perception",
        entry["plugins"],
        config_overrides=entry.get("plugin_configs"),
        metadata={"preset": preset},
    )


def default_activations() -> dict[str, StepActivation]:
    """Every step's default packaged activation."""

    return {step: packaged_activation(step) for step in STEPS}
