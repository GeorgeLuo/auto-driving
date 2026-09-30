"""Reasons the action composition can end a cycle with ``engine_error``."""

from __future__ import annotations

ENGINE_ERROR_REASONS = frozenset(
    {
        "decision_data_source_invalid",
        "action_plan_invariant_violated",
        "action_proposal_matrix_violated",
        "synthetic_error_proposal_failed",
        "engine_internal_error",
    }
)

__all__ = ["ENGINE_ERROR_REASONS"]
