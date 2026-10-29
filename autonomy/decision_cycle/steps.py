"""Build the cycle's steps from activations.

``STEP_RUNNERS`` maps each step to its runner. ``ONE_PLUGIN_STEPS`` are the
runners marked ``single_plugin``; ``CLEARABLE_STEPS`` are the rest, and a
missing selection replays them with no plugins. ``step_runner`` builds the
runner for one activation. ``builtin_activation`` is the selection a step uses
when it has no activation document: observation and action fall back to their
built-in plugins. Perception, memory, proposal, and plan have no built-in and
stay empty. ``load_decision_steps`` reads
``runtime_root/<step>/active.json`` for every step.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from autonomy.decision_cycle.action.runner import ActionRunner
from autonomy.decision_cycle.activation import (
    STEPS,
    STEP_ACTIVATION_SCHEMA,
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
from autonomy.runtime.plugin_loader import CodeSource, load_runner

STEP_RUNNERS: dict[str, Any] = {
    "perception": PerceptionRunner,
    "observation": ObservationRunner,
    "memory": MemoryRunner,
    "proposal": ProposalRunner,
    "plan": PlanRunner,
    "action": ActionRunner,
}

# A one-plugin step runs exactly one selected plugin and cannot be cleared.
ONE_PLUGIN_STEPS = tuple(step for step in STEPS if STEP_RUNNERS[step].single_plugin)
CLEARABLE_STEPS = tuple(step for step in STEPS if step not in ONE_PLUGIN_STEPS)

_BUILTINS = {
    "observation": (
        "perception_summary",
        "autonomy.decision_cycle.observation.perception_summary:PerceptionSummary",
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
    """Construct the runner from the process's already-imported package."""

    return STEP_RUNNERS[activation.step].from_activation(activation)


def applied_step_activations(steps: DecisionSteps) -> dict[str, Any]:
    """Detached activation payloads for the definitions the runners actually use.

    Preserve loaded specs and metadata for replay, replacing selected IDs and
    configs with the applied definitions. Unconfigured callables are omitted.
    """
    activations: dict[str, Any] = {}
    for step in STEPS:
        runner = getattr(steps, step)
        if runner is None:
            activations[step] = None
            continue
        loaded = getattr(runner, "activation", None)
        if not hasattr(runner, "applied"):
            if loaded is not None:
                payload = loaded.to_payload()
                payload["plugins"] = list(runner.plugin_ids)
                activations[step] = payload
            continue
        definitions = [definition for definition, _ in runner.applied]
        specs = dict(loaded.plugin_specs) if loaded is not None else {}
        configs = dict(loaded.plugin_configs) if loaded is not None else {}
        for definition in definitions:
            specs[definition.plugin_id] = definition.executable_entrypoint
            if definition.config:
                configs[definition.plugin_id] = definition.config
            else:
                configs.pop(definition.plugin_id, None)
        payload = {
            "schema": STEP_ACTIVATION_SCHEMA,
            "step": step,
            "plugins": [definition.plugin_id for definition in definitions],
            "plugin_specs": specs,
            "plugin_configs": deepcopy(configs),
        }
        if loaded is not None and loaded.metadata:
            payload["metadata"] = deepcopy(dict(loaded.metadata))
        activations[step] = payload
    return activations


def decision_steps(
    activations: dict[str, StepActivation | None] | None = None,
    *,
    source: CodeSource | None = None,
) -> DecisionSteps:
    """Runners for the given activations; unlisted steps use their built-ins.

    ``source`` is the installed package when omitted. A host that staged the
    activations passes the bundle code source instead.
    """

    activations = dict(activations or {})
    runners: dict[str, Any] = {}
    for step in STEPS:
        activation = activations[step] if step in activations else builtin_activation(step)
        if activation is not None and activation.step != step:
            raise ValueError(f"activation for {activation.step!r} given as {step!r}")
        try:
            runners[step] = (
                load_runner(activation, source=source) if activation is not None else None
            )
        except Exception as exc:
            raise RuntimeError(f"could not load {step}: {type(exc).__name__}: {exc}") from exc
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
