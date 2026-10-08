"""Build the cycle's steps from activations.

``STEP_RUNNERS`` maps each step to its runner. ``step_runner`` builds the
runner for one activation. ``builtin_activation`` is the selection a step uses
when it has no activation document: the observation, plan, and action steps
fall back to their built-in plugins; perception, memory, and proposal have no
default and stay empty. ``load_decision_steps`` reads
``runtime_root/<step>/active.json`` for every step.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from autonomy.decision_cycle.action.runner import ActionRunner
from autonomy.decision_cycle.activation import (
    STEPS,
    StepActivation,
    read_step_activation_if_present,
    require_step,
    step_activation,
    step_activation_path,
)
from autonomy.decision_cycle.cycle import DecisionSteps
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.decision_cycle.observation.runner import ObservationRunner
from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.decision_cycle.plan.runner import PlanRunner
from autonomy.decision_cycle.proposal.runner import ProposalRunner

STEP_RUNNERS: dict[str, Any] = {
    "perception": PerceptionRunner,
    "observation": ObservationRunner,
    "memory": MemoryRunner,
    "proposal": ProposalRunner,
    "plan": PlanRunner,
    "action": ActionRunner,
}

_BUILTINS = {
    "observation": (
        "perception_summary",
        "autonomy.decision_cycle.observation.perception_summary:PerceptionSummary",
    ),
    "plan": (
        "highest_confidence",
        "autonomy.decision_cycle.plan.highest_confidence:HighestConfidencePlan",
    ),
    "action": ("selected", "autonomy.decision_cycle.action.selected:SelectedAction"),
}


def builtin_activation(step: str) -> StepActivation | None:
    """The built-in selection for ``step``, or ``None`` when it has none."""

    builtin = _BUILTINS.get(require_step(step))
    if builtin is None:
        return None
    plugin_id, spec = builtin
    return step_activation(step, [plugin_id], {plugin_id: spec})


def step_runner(activation: StepActivation) -> Any:
    return STEP_RUNNERS[activation.step].from_activation(activation)


def snapshot_step_activations(steps: DecisionSteps) -> dict[str, Any]:
    """Detached executable selections actually applied by the frame's runners.

    A pending selection is not a running selection. Preserve the loaded specs
    and configs, but take plugin IDs from the applied instances. Unconfigured
    callables have no executable activation and are omitted.
    """
    snapshot: dict[str, Any] = {}
    for step in STEPS:
        runner = getattr(steps, step)
        if runner is None:
            snapshot[step] = None
            continue
        activation = getattr(runner, "activation", None)
        if activation is not None:
            snapshot[step] = replace(activation, plugins=tuple(runner.plugin_ids)).to_payload()
        elif hasattr(runner, "applied"):
            definitions = [definition for definition, _ in runner.applied]
            snapshot[step] = step_activation(
                step,
                [item.plugin_id for item in definitions],
                {item.plugin_id: item.entrypoint for item in definitions},
                {item.plugin_id: item.config for item in definitions},
            ).to_payload()
    return snapshot


def decision_steps(
    activations: dict[str, StepActivation | None] | None = None,
) -> DecisionSteps:
    """Runners for the given activations; unlisted steps use their built-ins."""

    activations = dict(activations or {})
    runners: dict[str, Any] = {}
    for step in STEPS:
        activation = activations[step] if step in activations else builtin_activation(step)
        if activation is not None and activation.step != step:
            raise ValueError(f"activation for {activation.step!r} given as {step!r}")
        runners[step] = step_runner(activation) if activation is not None else None
    return DecisionSteps(**runners)


def read_step_activations(runtime_root: Path) -> dict[str, StepActivation]:
    """The activations present under ``runtime_root``, by step."""

    found: dict[str, StepActivation] = {}
    for step in STEPS:
        activation = read_step_activation_if_present(step_activation_path(runtime_root, step), step)
        if activation is not None:
            found[step] = activation
    return found


def load_decision_steps(runtime_root: Path) -> DecisionSteps:
    """Runners for the activations under ``runtime_root``, with built-in fallbacks."""

    return decision_steps(read_step_activations(runtime_root))
