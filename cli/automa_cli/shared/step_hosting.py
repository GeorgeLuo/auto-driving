"""Reports from hosted step runners.

``plugin_report`` copies a hosted runner's plugin report for the worker's and
the workbench's publications.
"""

from __future__ import annotations

import copy
from typing import Any

__all__ = ["plugin_report"]


def plugin_report(runner: Any) -> dict[str, Any] | None:
    """A copy of the runner's plugin report, or ``None`` when it publishes none."""

    report_for = getattr(runner, "plugin_report", None)
    if not callable(report_for):
        return None
    report = report_for()
    if not isinstance(report, dict):
        return None
    return copy.deepcopy(report)
