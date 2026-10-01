"""Proposal plugin protocol.

A proposal plugin returns one candidate command per cycle from the detached
``DecisionDataSource`` and the host map. Its candidate is admitted only under
the plugin ID it was selected by. A plugin that reads retained evidence from
the host map may declare that key as ``evidence_key``; the step records an
audit copy of it in the source.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from autonomy.decision_cycle.proposal.inputs import DecisionDataSource
from autonomy.decision_cycle.proposal.values import ActionProposal
from autonomy.shared_memory import SharedMemory


@runtime_checkable
class ProposalPlugin(Protocol):
    plugin_id: str

    def propose(self, source: DecisionDataSource, shared_memory: SharedMemory) -> ActionProposal:
        """Return this plugin's candidate for ``source.frame_id``."""
