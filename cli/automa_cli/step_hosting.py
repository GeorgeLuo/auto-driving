"""CLI entry to the shared step loader.

``load_staged_runner`` asks the shared loader for the code source named on the
activation. A controller bundle imports ``implementations`` from that bundle;
otherwise the runner comes from the installed package. ``plugin_report`` copies
a hosted runner's plugin report for the worker's and the workbench's
publications. Live selection changes are the cycle host's
(``AutonomyCycleHost.sync_selection``), shared with the onboard host.
"""

from __future__ import annotations

import copy
from typing import Any

from autonomy.decision_cycle.activation import StepActivation
from autonomy.runtime.plugin_loader import (
    BUNDLE_PREFIXES,
    code_source_from_activation,
    load_runner,
)

__all__ = ["BUNDLE_PREFIXES", "load_staged_runner", "plugin_report"]


def load_staged_runner(activation: StepActivation) -> Any:
    """The activation's runner, loaded from its controller bundle when it names one."""

    return load_runner(activation, source=code_source_from_activation(activation))


def plugin_report(runner: Any) -> dict[str, Any] | None:
    """A copy of the runner's plugin report, or ``None`` when it publishes none."""

    report_for = getattr(runner, "plugin_report", None)
    if not callable(report_for):
        return None
    report = report_for()
    if not isinstance(report, dict):
        return None
    return copy.deepcopy(report)
