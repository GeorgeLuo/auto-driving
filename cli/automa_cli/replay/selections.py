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

from autonomy.decision_cycle.activation import StepActivation
from autonomy.decision_cycle.restage import Restaged, restage
from autonomy.decision_cycle.steps import builtin_activation
from autonomy.plugins import PluginManagementError
from autonomy.runtime.plugin_loader import code_source_from_activation, load_runner
from autonomy.shared_memory import read_shared_memory_file
from implementations.decision_cycle.catalog import selection_activation

from ..releases.store import release_code_dir


@dataclass(frozen=True)
class SharedMemorySeed:
    """A run's starting map and the file document it was decoded from."""

    path: Path
    document: dict[str, object]
    shared_memory: dict[str, object]


def read_shared_memory_seed(path: Path | None) -> SharedMemorySeed | None:
    """Decode the file once into a fresh run map, keeping its document."""

    if path is None:
        return None
    shared_memory, document = read_shared_memory_file(path)
    return SharedMemorySeed(path, document, shared_memory)


def add_seed_provenance(report: dict[str, object], seed: SharedMemorySeed | None) -> None:
    """Record a seeded run's file path and document under ``shared_memory_seed``."""

    if seed is not None:
        report["shared_memory_seed"] = {"path": str(seed.path), "document": seed.document}


def replace_replay_pipeline(
    runners: dict[str, object],
    activations: dict[str, StepActivation | None],
    recorded: dict[str, StepActivation | None],
    *,
    overrides: set[str],
    build: Callable[[str, StepActivation], object] | None = None,
) -> Restaged | None:
    """Resolve replay overrides and apply the cycle's restage rule."""
    desired = resolve_replay_activations(
        recorded, defaults=activations,
        overrides={step: activations[step] for step in overrides if step in activations},
    )
    return restage(
        runners, activations, desired,
        build=build or (lambda _step, activation: recorded_runner(activation)),
    )


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


def recorded_runner(activation: StepActivation) -> object:
    """The selection's runner, imported from the controller release it records.

    A selection with no recorded release, such as a default or an override,
    runs on the installed package. A recorded release missing from the
    vehicle's local releases raises, naming the update that packages one.
    """

    release = recorded_release(activation)
    source = code_source_from_activation(activation)
    if release is not None:
        host_name, tree_sha256 = release
        code_dir, _ = release_code_dir(host_name, tree_sha256=tree_sha256)
        source = replace(source, bundle_root=code_dir)
    try:
        return load_runner(activation, source=source)
    except Exception as exc:
        notes = getattr(exc, "__notes__", ())
        if notes:
            raise PluginManagementError(f"{' '.join(notes)}: {type(exc).__name__}: {exc}") from exc
        raise


def selection_record(activation: StepActivation) -> dict[str, object]:
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


def selection_report(activation: StepActivation) -> dict[str, object]:
    """A report's selection entry: the selection record and its plugin IDs."""

    return {**selection_record(activation), "plugins": list(activation.plugins)}
