"""The proposal step's contract declarations.

``FAILURE_POLICY`` and ``composition_declaration`` are the values
``ProposalRunner`` follows and ``describe_schema`` reports, as the perception
and memory interfaces declare theirs. The step's record for one cycle is
``ProposalResult`` in ``result``; the plugin protocol is ``ProposalPlugin`` in
``plugin``.
"""

from __future__ import annotations

from autonomy.decision_cycle.runner import FailurePolicy

PROPOSAL_SCHEMA = "proposal_schema_v1"

# A proposal plugin that raises is isolated: its candidate becomes a synthetic
# error candidate and the other plugins still propose. A reset error
# propagates. A missing observation is still passed to the plugin, marked
# unavailable or error in the source.
FAILURE_POLICY = FailurePolicy(
    update="isolate_plugin",
    reset="propagate",
    missing_input="invoke",
)


def composition_declaration() -> dict[str, str]:
    """How several proposal plugins share one cycle.

    Each plugin reads its own copy of the source and the one shared host map,
    and adds one candidate. The plan step chooses among the candidates.
    """

    return {
        "order": "selection_order",
        "source": "detached_copy_per_plugin",
        "candidates": "one_per_plugin_all_kept",
    }
