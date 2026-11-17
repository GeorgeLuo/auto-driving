"""Replay a recording's selections with replacements from one uploaded collection."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from autonomy.decision_cycle.activation import (
    STEPS,
    StepActivation,
    activation_for_definitions,
    activation_generation_id,
)
from autonomy.plugins import LocalPluginCatalog, PluginDefinition
from autonomy.runtime.plugin_catalog import upload_collection
from implementations.decision_cycle.catalog import CUSTOM_PRESET

from ..shared.collection_manifest import read_manifest
from ..shared.plugin_catalog import packaged_plugin_definitions
from .models import ImageSource, SourceValidationError
from .selections import recorded_runner


def check_manifest_selection(
    manifest: Path | None, preset: str | None, plugins: Sequence[str] | None, *, flag_prefix: str = "",
) -> None:
    if manifest is None:
        return
    if preset is not None:
        raise ValueError(f"--manifest cannot be used with --{flag_prefix}preset")
    if plugins is not None:
        raise ValueError(f"--manifest cannot be used with --{flag_prefix}plugin")


def apply_collection_manifest(
    source: ImageSource, path: Path, catalog: LocalPluginCatalog,
) -> tuple[ImageSource, dict[str, object]]:
    """Register the collection, rebuild recorded selections, and check later restages."""

    recorded = {id(frame.selections): frame.selections for frame in source.frames}
    if not any(recorded.values()):
        raise SourceValidationError("--manifest requires a source with a recorded selection")

    name, definitions = upload_collection(catalog, read_manifest(path))
    replacements = {(item.step, item.plugin_id): item for item in definitions}
    selected = {
        (activation.step, plugin_id)
        for selections in recorded.values() for activation in selections.values()
        if activation is not None for plugin_id in activation.plugins
    }
    for step, plugin_id in replacements:
        if (step, plugin_id) not in selected:
            raise SourceValidationError(f"--manifest names {step}/{plugin_id}, which the recording did not run")

    packaged = {
        (step, item.plugin_id): item for step in STEPS for item in packaged_plugin_definitions(step)
    }
    rebuilt = {
        key: {step: None if activation is None else _replace_plugins(activation, packaged, replacements)
              for step, activation in selections.items()}
        for key, selections in recorded.items()
    }
    _preflight_restages(rebuilt)
    frames = tuple(replace(frame, selections=rebuilt[id(frame.selections)]) for frame in source.frames)
    return replace(source, frames=frames), {
        "path": str(path), "collection": name,
        "replaced_plugins": [f"{step}/{plugin_id}" for step, plugin_id in replacements],
    }


def _replace_plugins(
    activation: StepActivation,
    packaged: dict[tuple[str, str], PluginDefinition],
    replacements: dict[tuple[str, str], PluginDefinition],
) -> StepActivation:
    metadata = dict(activation.metadata)
    metadata.pop("controller_bundle", None)
    metadata.pop("host_name", None)
    definitions = []
    for plugin_id in activation.plugins:
        key = (activation.step, plugin_id)
        if key in replacements:
            definitions.append(replacements[key])
            metadata["preset"] = CUSTOM_PRESET
            continue
        definition = packaged.get(key)
        if definition is None:
            raise SourceValidationError(f"{activation.step}/{plugin_id} is missing from the installed catalog and --manifest")
        definitions.append(replace(definition, config=activation.plugin_configs.get(plugin_id, {})))
    return activation_for_definitions(activation.step, definitions, metadata=metadata)


def _preflight_restages(generations: dict[int, dict[str, StepActivation | None]]) -> None:
    """Check each later configuration before frame 0. Startup builds the first."""

    checked: set[str] = set()
    for index, selections in enumerate(generations.values()):
        for step, activation in selections.items():
            if activation is None:
                continue
            identity = activation_generation_id({step: activation})
            if identity not in checked and index:
                recorded_runner(activation)
            checked.add(identity)
