"""Reasons a cycle's proposal, plan, or action record can carry status ``error``.

Any of these makes the action step decide without a plan; the selected action
plugin still chooses the control, so the cycle fails closed.
"""

from __future__ import annotations

CYCLE_ERROR_REASONS = frozenset(
    {
        "decision_data_source_invalid",
        "action_plan_invariant_violated",
        "action_proposal_matrix_violated",
        "synthetic_error_proposal_failed",
        "step_internal_error",
    }
)

__all__ = ["CYCLE_ERROR_REASONS"]
