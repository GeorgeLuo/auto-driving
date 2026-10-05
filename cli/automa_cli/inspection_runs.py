"""Executable step selections stored by offline inspections.

Each inspection records a step's selection under the step's name: perception
recordings hold ``perception``, and memory recordings hold ``perception`` and
``memory``. Each selection uses the same ``preset`` and ``config`` shape. Plugin
names alone describe a run but cannot restore it.
"""

from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.activation import StepActivation, step_activation


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
