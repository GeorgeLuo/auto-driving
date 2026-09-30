"""Decision-cycle contracts used by the step modules.

``context.DecisionFrameContext`` is the input for one cycle tick.
``memory.errors.MemoryUpdateError`` is the failure raised when remember
cannot continue. ``observation`` holds the current-frame record and the
default observe step. ``proposal.inputs`` holds the detached view a proposal
reads, ``proposal.values`` its candidate commands, and
``action_identifiers`` the identifier grammar for action inputs, proposals,
and plans. ``planning`` holds plan values and the built-in selector, and
``action_gate.hold`` the fixed idle gate. ``result`` is the aggregate action
result and ``errors`` its engine error reasons. ``action`` composes proposal,
planning, and the hold gate for one cycle.
``cycle`` orders perceive, observe, remember, and choose_action; its legacy path is
``autonomy.decision.cycle``.
"""
