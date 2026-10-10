"""The proposal step.

``inputs.DecisionDataSource`` is the detached input view a proposal reads.
``values.ActionProposal`` is one plugin's candidate command for one cycle.
``plugin`` is the proposal plugin protocol, ``result.ProposalResult`` the
step's record, and ``runner`` the step runner that admits one candidate per
selected plugin.
"""
