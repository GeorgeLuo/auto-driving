"""Identifier grammar for action inputs, proposals, and plans.

Frame, plugin, proposal, and plan identifiers follow ``autonomy.fields.require_ascii_id``.
``ActionInputError`` rejects an invalid cycle identity before the action
composition promises a result; ``ActionProposalMatrixError`` rejects an
admitted candidate that fails lifecycle re-validation.
"""

from __future__ import annotations

MAX_ID_LEN = 64


class ActionInputError(ValueError):
    """Raised when cycle identity is invalid before a cycle result is promised."""


class ActionProposalMatrixError(ValueError):
    """Admitted candidate fails lifecycle/bounds matrix re-validation."""


def proposal_id_for(plugin_id: str, frame_id: str) -> str:
    return f"{plugin_id}:{frame_id}"


def plan_id_for(frame_id: str) -> str:
    return f"action-plan:{frame_id}"


def require_code_point_len(value: str, *, field_name: str, max_len: int) -> str:
    if len(value) > max_len:
        raise ValueError(f"{field_name} exceeds {max_len} code points")
    return value
