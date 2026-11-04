"""The plan step's contract declarations.

``FAILURE_POLICY`` is what ``PlanRunner`` does when a plugin fails, as the
perception and memory interfaces declare theirs. The step's record for one
cycle is ``ActionPlan`` in ``values``; the plugin protocol is ``PlanPlugin``
in ``plugin``.
"""

from __future__ import annotations

from autonomy.decision_cycle.runner import FailurePolicy

# A plan plugin that raises, or returns a plan that fails the runner's check
# (frame, selector_id, candidates), is isolated. With one plugin that means
# the step returns no plan, the cycle continues, and the action holds. A reset
# error propagates: StepRunner.reset does not catch it. missing_input is a
# failed proposal: the plugin is skipped and its record stays null. An absent
# proposal step is not a missing input; the plugin is called with no candidates.
FAILURE_POLICY = FailurePolicy(
    update="isolate_plugin",
    reset="propagate",
    missing_input="skip_plugin",
)
