"""The plan step's contract declarations.

``FAILURE_POLICY`` and ``composition_declaration`` are the values
``PlanRunner`` follows and ``describe_schema`` reports, as the perception,
memory, and proposal interfaces declare theirs. The step's record for one
cycle is ``ActionPlan`` in ``values``; the plugin protocol is ``PlanPlugin``
in ``plugin``.
"""

from __future__ import annotations

from autonomy.decision_cycle.runner import FailurePolicy

PLAN_SCHEMA = "plan_schema_v1"

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


def composition_declaration() -> dict[str, str]:
    """How the one plan plugin uses the proposal step's candidates.

    The plugin reads those candidates, a detached copy of the proposal source,
    the host shared-memory map, the cycle's ``frame_id``, and its
    ``timestamp_ms``, and returns one ``ActionPlan``. The plan's
    ``selector_id`` is the plugin ID.
    """

    return {
        "order": "one_plugin",
        "inputs": "proposal candidates, the proposal source, shared memory, frame_id, and timestamp_ms",
        "output": "one ActionPlan; selector_id is the plugin ID",
    }
