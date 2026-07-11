from __future__ import annotations

from typing import Any

from .types import StartupActionCheckInstruction, StartupActionCheckPlan


def check_startup_action_result(
    *,
    instruction: StartupActionCheckInstruction,
    plan: StartupActionCheckPlan,
    comparison: dict[str, Any],
    noise_floor: dict[str, float] | None = None,
) -> tuple[bool, list[str]]:
    mean_diff = float(comparison.get("mean_abs_diff_norm") or 0.0)
    changed_ratio = float(comparison.get("changed_pixel_ratio") or 0.0)
    noise_mean = float((noise_floor or {}).get("mean_abs_diff_norm") or 0.0)
    noise_changed = float((noise_floor or {}).get("changed_pixel_ratio") or 0.0)
    mean_excess = mean_diff - noise_mean
    changed_excess = changed_ratio - noise_changed
    reasons: list[str] = []

    if instruction.expect_change:
        min_mean = instruction.min_mean_abs_diff_norm
        if min_mean is None:
            min_mean = plan.default_min_mean_abs_diff_norm
        min_changed = instruction.min_changed_pixel_ratio
        if min_changed is None:
            min_changed = plan.default_min_changed_pixel_ratio
        min_mean_excess = plan.default_min_mean_abs_diff_excess_norm
        min_changed_excess = plan.default_min_changed_pixel_ratio_excess
        mean_passed = mean_diff >= min_mean and mean_excess >= min_mean_excess
        changed_passed = changed_ratio >= min_changed and changed_excess >= min_changed_excess
        passed = mean_passed or changed_passed
        if mean_diff < min_mean:
            reasons.append(f"mean_abs_diff_norm {mean_diff:.5f} < {min_mean:.5f}")
        if mean_excess < min_mean_excess:
            reasons.append(
                f"mean_abs_diff_norm excess {mean_excess:.5f} < {min_mean_excess:.5f}",
            )
        if changed_ratio < min_changed:
            reasons.append(f"changed_pixel_ratio {changed_ratio:.5f} < {min_changed:.5f}")
        if changed_excess < min_changed_excess:
            reasons.append(
                f"changed_pixel_ratio excess {changed_excess:.5f} < {min_changed_excess:.5f}",
            )
        return passed, [] if passed else reasons

    max_mean = instruction.max_mean_abs_diff_norm
    if max_mean is None:
        max_mean = plan.default_max_still_mean_abs_diff_norm
    passed = mean_diff <= max_mean
    if not passed:
        reasons.append(f"still mean_abs_diff_norm {mean_diff:.5f} > {max_mean:.5f}")
    return passed, reasons
