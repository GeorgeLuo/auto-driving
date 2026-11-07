"""Executable selections shared by vehicle recordings and offline replay.

Per-frame ``steps`` activations retain restaging, disabled steps, plugin
entrypoints and constructor configs with their IDs.
A selection a vehicle ran is rebuilt from the release it records, not from
whatever is checked out or staged now.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from autonomy.decision_cycle.activation import StepActivation, executable_document
from autonomy.decision_cycle.steps import builtin_activation
from autonomy.runtime.plugin_loader import code_source_from_activation, load_runner
from autonomy.shared_memory import read_shared_memory_file
from implementations.decision_cycle.catalog import selection_activation

from ..releases.store import release_code_dir


@dataclass(frozen=True)
class SharedMemorySeed:
    """A run's starting map and the file document it was decoded from."""

    path: Path
    document: dict[str, Any]
    shared_memory: dict[str, Any]


def read_shared_memory_seed(path: Path | None) -> SharedMemorySeed | None:
    """Decode the file once into a fresh run map, keeping its document."""

    if path is None:
        return None
    shared_memory, document = read_shared_memory_file(path)
    return SharedMemorySeed(path, document, shared_memory)


def add_seed_provenance(report: dict[str, Any], seed: SharedMemorySeed | None) -> None:
    """Record a seeded run's file path and document under ``shared_memory_seed``."""

    if seed is not None:
        report["shared_memory_seed"] = {"path": str(seed.path), "document": seed.document}


def replace_replay_pipeline(
    runners: dict[str, Any],
    activations: dict[str, StepActivation | None],
    recorded: dict[str, StepActivation | None],
    *,
    overrides: set[str],
    build: Callable[[str, StepActivation], Any] | None = None,
) -> tuple[dict[str, Any], dict[str, StepActivation | None], bool]:
    """Build a fresh pipeline when the effective selection changed.

    ``recorded`` replaces steps that are not overridden. The current runners
    stay in place when that selection is the one already running, including
    when an override cancels a recorded change. Construction finishes before
    the caller installs the result or clears its map.
    """

    desired = resolve_replay_activations(
        recorded, defaults=activations,
        overrides={step: activations[step] for step in overrides if step in activations},
    )
    same = all(
        executable_document(desired.get(step)) == executable_document(activations.get(step))
        for step in set(desired) | set(activations)
    )
    if same:
        return runners, activations, False
    construct = build or (lambda step, activation: recorded_runner(activation))
    built = {
        step: None if activation is None else construct(step, activation)
        for step, activation in desired.items()
    }
    return built, desired, True


def default_selection(step: str) -> StepActivation:
    """Offline defaults: the cycle's built-in, else the packaged selection."""
    return builtin_activation(step) or selection_activation(step)


def resolve_replay_activations(
    recorded: dict[str, StepActivation | None], *,
    defaults: dict[str, StepActivation | None],
    overrides: dict[str, StepActivation | None],
) -> dict[str, StepActivation | None]:
    """Resolve each step once; an absent recorded slot stays absent."""
    return {
        step: overrides[step] if step in overrides else recorded.get(step, default)
        for step, default in defaults.items()
    }


def recorded_release(activation: StepActivation) -> tuple[str, str] | None:
    """The vehicle and release source tree sha256 a recorded selection ran on."""

    metadata = activation.metadata
    bundle = metadata.get("controller_bundle")
    release = bundle.get("release") if isinstance(bundle, dict) else None
    tree_sha256 = release.get("tree_sha256") if isinstance(release, dict) else None
    host_name = metadata.get("host_name")
    if isinstance(tree_sha256, str) and isinstance(host_name, str):
        return host_name, tree_sha256
    return None


def recorded_runner(activation: StepActivation) -> Any:
    """The selection's runner, imported from the controller release it records.

    A selection with no recorded release, such as a default or an override,
    runs on the installed package. A recorded release missing from the
    vehicle's local releases raises, naming the update that packages one.
    """

    release = recorded_release(activation)
    if release is None:
        return load_runner(activation)
    host_name, tree_sha256 = release
    code_dir, _ = release_code_dir(host_name, tree_sha256=tree_sha256)
    return load_runner(activation, source=replace(code_source_from_activation(activation), bundle_root=code_dir))


def selection_record(activation: StepActivation) -> dict[str, Any]:
    """Record the selected IDs, entrypoints and constructor config together."""

    return {
        "preset": activation.metadata.get("preset") or "recorded",
        "config": {
            "plugins": list(activation.plugins),
            "plugin_specs": dict(activation.plugin_specs),
            "plugin_configs": {
                key: dict(value) for key, value in activation.plugin_configs.items()
            },
        },
    }


def selection_report(activation: StepActivation) -> dict[str, Any]:
    """A report's selection entry: the selection record and its plugin IDs."""

    return {**selection_record(activation), "plugins": list(activation.plugins)}
