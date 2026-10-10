"""Executable selections shared by vehicle recordings and offline replay.

Per-frame ``steps`` snapshots retain restaging and disabled steps. Older
inspections store selections under step names using ``preset`` and ``config``.
Both forms restore plugin entrypoints and constructor configs with their IDs.
A selection a vehicle ran is rebuilt from the release it records, not from
whatever is checked out or staged now.
"""

from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.activation import (
    StepActivation, step_activation,
)
from autonomy.runtime.plugin_loader import CodeSource, load_runner
from autonomy.runtime.recording import recorded_selections

from .runtime_hosts import release_code_dir


def replay_step(runner: Any, activation: StepActivation | None, shared_memory: dict[str, Any]) -> Any:
    """Restore a step using the live runner's selection and reset lifecycle."""
    previous = getattr(runner, "activation", None)
    if activation is not None and previous is not None:
        if (
            previous.plugin_specs == activation.plugin_specs
            and previous.plugin_configs == activation.plugin_configs
            and recorded_release(previous) == recorded_release(activation)
        ):
            if tuple(runner.plugin_ids) != activation.plugins:
                runner.plugin_manager.select(activation.plugins)
                runner.apply_selection(shared_memory)
            runner.adopt_activation(activation)
            return runner
    replacement = recorded_runner(activation) if activation is not None else None
    if runner is not None:
        runner.reset(shared_memory)
    return replacement


def recorded_release(activation: StepActivation) -> tuple[str, str] | None:
    """The vehicle and release source tree sha256 a recorded selection ran on."""

    metadata = activation.metadata
    bundle = metadata.get("controller_bundle")
    release = bundle.get("release") if isinstance(bundle, dict) else None
    tree_sha256 = release.get("tree_sha256") if isinstance(release, dict) else None
    vehicle_id = metadata.get("vehicle_id")
    if isinstance(tree_sha256, str) and isinstance(vehicle_id, str):
        return vehicle_id, tree_sha256
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
    vehicle_id, tree_sha256 = release
    code_dir, _ = release_code_dir(vehicle_id, tree_sha256=tree_sha256)
    return load_runner(activation, source=CodeSource(bundle_root=code_dir))


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


def recorded_selection(step: str, manifest: dict[str, Any]) -> StepActivation | None:
    """Restore a recorded selection, including an explicitly empty one.

    Older memory reports contain only plugin names; callers use their default
    when there is no executable config. An invalid recorded config raises the
    same activation error as a staged selection.
    """

    selections = recorded_selections(manifest)
    if step in selections:
        return selections[step]
    record = manifest.get(step)
    if not isinstance(record, dict) or "config" not in record:
        return None
    config = record["config"]
    if not isinstance(config, dict):
        raise TypeError(f"recorded {step} config must be an object")
    return step_activation(
        step,
        config.get("plugins", []),
        config.get("plugin_specs", {}),
        config.get("plugin_configs", {}),
        metadata={"preset": record.get("preset") or "recorded"},
    )
