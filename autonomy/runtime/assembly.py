"""Assemble a vehicle host's runtime from its staged activations.

Every vehicle host runs the same startup: read ``runtime/<step>/active.json``,
load the steps from the installed controller release, and follow restaged
selections. Only the control target differs, so the caller passes
``create_host``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from autonomy.decision_cycle.activation import (
    STEPS,
    StepActivation,
    read_step_activation,
    step_activation_path,
)
from autonomy.decision_cycle.cycle import DecisionSteps
from autonomy.decision_cycle.steps import builtin_activation, decision_steps
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.plugin_loader import INSTALLED_PACKAGE

logger = logging.getLogger(__name__)


def read_staged_activations(runtime_root: Path) -> dict[str, StepActivation]:
    """Each staged step activation; a missing or unreadable one is logged and left out."""

    activations: dict[str, StepActivation] = {}
    for step in STEPS:
        path = step_activation_path(runtime_root, step)
        if not path.exists():
            logger.warning("No %s activation at %s", step, path)
            continue
        try:
            activations[step] = read_step_activation(path, step)
        except Exception:
            logger.exception(
                "Unable to read the %s activation at %s; the step %s", step, path,
                "uses its built-in plugin" if builtin_activation(step) is not None else "stays empty",
            )
            continue
        logger.info("Activated %s plugins %s", step, ", ".join(activations[step].plugins) or "(none)")
    return activations


def staged_runtime(
    runtime_root: Path,
    create_host: Callable[[DecisionSteps], AutonomyCycleHost],
    *,
    host_name: str | None = None,
) -> AutonomyCycleHost:
    """The host for the staged steps.

    ``host_name`` is stamped before the first cycle, so live reporting and a
    later recording name the same pipeline.
    """

    runtime_root = Path(runtime_root)
    activations = read_staged_activations(runtime_root)
    host = create_host(decision_steps(activations, source=INSTALLED_PACKAGE))
    host.follow_activations(activations, runtime_root)
    if isinstance(host_name, str) and host_name:
        host.note_host_name(host_name)
    return host
