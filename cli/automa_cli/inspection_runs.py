"""Executable selections shared by vehicle recordings and offline replay.

Per-frame ``steps`` snapshots retain restaging and disabled steps. Older
inspections store selections under step names using ``preset`` and ``config``.
Both forms restore plugin entrypoints and constructor configs with their IDs.
"""

from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.activation import (
    DECISION_STEPS, StepActivation, activation_generation_id, require_step,
    step_activation, step_activation_from_payload,
)
from autonomy.decision_cycle.steps import step_runner


def recorded_selections(record: dict[str, Any]) -> dict[str, StepActivation | None]:
    """Read a frame's executable selections, preserving disabled steps."""
    if "steps" not in record:
        return {}
    payloads = record["steps"]
    if not isinstance(payloads, dict):
        raise ValueError("recorded steps must be an object")
    activations = {
        require_step(step): None if payload is None else step_activation_from_payload(payload, step=step)
        for step, payload in payloads.items()
    }
    generation = record.get("generation_id")
    if generation is not None:
        if not all(step in activations for step in DECISION_STEPS):
            raise ValueError("recorded generation requires proposal, plan, and action selections")
        expected = activation_generation_id(
            {step: activations[step] for step in DECISION_STEPS}, prefix="decision",
        )
        if generation != expected:
            raise ValueError("recorded generation does not match its step selections")
    return activations


def replay_step(runner: Any, activation: StepActivation | None, shared_memory: dict[str, Any]) -> Any:
    """Restore a step using the live runner's selection and reset lifecycle."""
    previous = getattr(runner, "activation", None)
    if activation is not None and previous is not None:
        if (
            previous.plugin_specs == activation.plugin_specs
            and previous.plugin_configs == activation.plugin_configs
        ):
            if tuple(runner.plugin_ids) != activation.plugins:
                runner.plugin_manager.select(activation.plugins)
                runner.apply_selection(shared_memory)
            runner.activation = activation
            return runner
    replacement = step_runner(activation) if activation is not None else None
    if runner is not None:
        runner.reset(shared_memory)
    return replacement


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
