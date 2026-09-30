"""Decision-cycle contracts used by the step modules.

``context.DecisionFrameContext`` is the input for one cycle tick.
``memory.errors.MemoryUpdateError`` is the failure raised when remember
cannot continue. Ordering of perceive, observe, remember, and choose_action
stays in ``autonomy.decision.cycle``.
"""
