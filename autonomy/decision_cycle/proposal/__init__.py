"""The proposal step.

Proposal plugins read the cycle context and its shared-memory map.
``values.ActionProposal`` is one plugin's candidate command for one cycle.
``plugin`` is the proposal plugin protocol, ``result.ProposalResult`` the
step's record, and ``runner`` the step runner that admits one candidate per
selected plugin.
"""
