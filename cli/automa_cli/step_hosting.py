"""Run staged step activations in a CLI-hosted cycle (the Chase automation worker).

``load_staged_runner`` builds a step's runner. When the activation was staged
into a controller bundle, the runner's plugins are imported from that bundle
and every runner call runs inside the bundle's import context, so the worker
runs the staged code rather than the workspace. ``plugin_report`` copies a
hosted runner's plugin report for the worker's and the workbench's
publications. Live selection changes are the cycle host's
(``AutonomyCycleHost.sync_selection``), shared with the onboard host.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from autonomy.decision_cycle.activation import StepActivation
from autonomy.decision_cycle.steps import step_runner

from .staged_bundle import StagedBundleImport

# Plugins come from the staged bundle's implementations. autonomy stays on the
# host so plugins and the cycle share the host's value classes.
BUNDLE_PREFIXES = ("implementations",)


class StagedStep:
    """A step runner whose calls run inside its controller bundle's imports."""

    def __init__(self, runner: Any, import_context: StagedBundleImport) -> None:
        self.runner = runner
        self.import_context = import_context

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        with self.import_context.activate():
            return self.runner(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        value = getattr(self.runner, name)
        if not callable(value):
            return value

        def invoke_in_bundle(*args: Any, **kwargs: Any) -> Any:
            with self.import_context.activate():
                return value(*args, **kwargs)

        return invoke_in_bundle


def bundle_root(activation: StepActivation) -> Path | None:
    controller_bundle = activation.metadata.get("controller_bundle")
    root = controller_bundle.get("root_dir") if isinstance(controller_bundle, dict) else None
    return Path(root) if isinstance(root, str) and root else None


def load_staged_runner(activation: StepActivation) -> Any:
    """The activation's runner, loaded from its controller bundle when it names one."""

    root = bundle_root(activation)
    if root is None:
        return step_runner(activation)
    if not root.is_dir():
        raise FileNotFoundError(f"Controller bundle is missing: {root}")
    import_context = StagedBundleImport(root, BUNDLE_PREFIXES)
    with import_context.activate():
        runner = step_runner(activation)
    return StagedStep(runner, import_context)


def plugin_report(runner: Any) -> dict[str, Any] | None:
    """A copy of the runner's plugin report, or ``None`` when it publishes none."""

    report_for = getattr(runner, "plugin_report", None)
    if not callable(report_for):
        return None
    report = report_for()
    if not isinstance(report, dict):
        return None
    return copy.deepcopy(report)
