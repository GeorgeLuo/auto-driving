"""The plan step.

``values.ActionPlan`` records the candidates and the selected proposal.
``plugin`` is the plan plugin protocol, ``interface`` the step's schema,
failure policy, and composition, and ``runner`` the step runner.
Packaged plan plugins live in ``implementations/decision_cycle/plan/``.
"""
