"""Decision-cycle contracts used by the step modules.

``context.DecisionFrameContext`` is the input for one cycle tick.
``memory.errors.MemoryUpdateError`` is the failure raised when remember
cannot continue. ``proposal.values`` holds candidate commands, and
``action_identifiers`` the identifier grammar for action inputs, proposals,
and plans. ``planning`` holds plan values and the built-in selector. Ordering of perceive, observe, remember, and choose_action
stays in ``autonomy.decision.cycle``.
"""
