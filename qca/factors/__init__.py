"""Interpretable factor measurements; runtime evidence is supplied separately."""

from __future__ import annotations

from typing import Any

from .coupling import analyze_coupling
from .structure import analyze_structure
from .verification import analyze_verification


FACTOR_VERSION = "1"


def measure_factors(sources: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Measure the same explicitly included Python source set for each factor."""

    return dict(sorted({
        **analyze_structure(sources),
        **analyze_coupling(sources),
        **analyze_verification(sources),
    }.items()))


def compare_factors(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    changed_paths: set[str],
) -> dict[str, dict[str, Any]]:
    """Keep scope-wide deltas and present findings on the changed source surface."""

    result = {}
    for name, head in after.items():
        base_metrics = before[name]["metrics"]
        head_metrics = head["metrics"]
        result[name] = {
            **head,
            "base_metrics": base_metrics,
            "delta": {
                key: head_metrics.get(key, 0) - base_metrics.get(key, 0)
                for key in sorted(set(base_metrics) | set(head_metrics))
            },
            "snapshot_finding_count": len(head["findings"]),
            "findings": [item for item in head["findings"] if _touches(item, changed_paths)],
            "finding_scope": "changed files; metrics cover the configured Python scope",
        }
    # Contract removal/shape changes deserve a direct pointer even when the
    # number of public functions remains unchanged.
    old_surface = before["contracts"].get("surface", {})
    new_surface = after["contracts"].get("surface", {})
    if isinstance(old_surface, dict) and isinstance(new_surface, dict):
        result["contracts"]["surface_changes"] = {
            "added": sorted(set(new_surface) - set(old_surface)),
            "removed": sorted(set(old_surface) - set(new_surface)),
            "changed": [
                {"symbol": key, "before": old_surface[key], "after": new_surface[key]}
                for key in sorted(set(old_surface) & set(new_surface))
                if old_surface[key] != new_surface[key]
            ],
        }
    return result


def _touches(finding: dict[str, Any], paths: set[str]) -> bool:
    if finding.get("path") in paths:
        return True
    # Clone groups and cycles can span several owners.
    return any(
        (item.get("path") if isinstance(item, dict) else item) in paths
        for key in ("occurrences", "members", "paths")
        for item in finding.get(key, [])
    )
