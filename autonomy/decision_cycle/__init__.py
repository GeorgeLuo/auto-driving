"""The decision cycle and its steps.

``cycle`` orders the six steps (perception, observation, memory, proposal,
plan, action) and records one output per step. Each step is a package of the
same name holding its plugin protocol (``plugin``), its runner (``runner``),
and its record values; ``runner`` at this level is the shape those runners
share. ``activation`` is the per-step activation document and ``steps`` builds
runners from activations. ``context.DecisionFrameContext`` is the input for
one cycle tick, ``action_identifiers`` the identifier grammar for proposals and
plans, and ``errors`` the reasons a cycle fails closed.
"""
