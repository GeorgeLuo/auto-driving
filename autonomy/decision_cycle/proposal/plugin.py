"""Proposal plugin protocol.

A proposal plugin reads the cycle context and shared memory and returns one
candidate command per cycle. Its candidate is admitted only under the plugin
ID it was selected by. Plugins choose which shared-memory keys they read.

An optional ``reset()`` or ``reset(shared_memory)`` starts a new epoch and also
runs when the plugin leaves the selection. Reset errors propagate. Return a
valid ``ActionProposal`` for this plugin and frame; wrong types or identities
become synthetic error candidates, while lifecycle or size violations fail
the whole step. ``ProposalRunner.plugin_report`` reports each plugin's last
invocation timing and error, separately from step failures in ``status``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.proposal.values import ActionProposal


@runtime_checkable
class ProposalPlugin(Protocol):
    plugin_id: str

    def propose(self, context: DecisionFrameContext) -> ActionProposal:
        """Return this plugin's candidate for ``context.frame_id``."""
