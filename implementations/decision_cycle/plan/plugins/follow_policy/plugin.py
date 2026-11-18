"""Follow the published policy; arm with the plan/follow_policy preset.

The collection supplies a policy cycler and tagged executor proposals. Policy
selection and exit handling live in shared.policy. Applied history belongs to
the separately armed memory/control_runs plugin; selection writes no state.
"""

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.decision_cycle.proposal.values import ActionProposal
from implementations.decision_cycle.shared.policy import follow_policy_plan


class FollowPolicy:
    plugin_id = "follow_policy"

    def plan(self, context: DecisionFrameContext, candidates: tuple[ActionProposal, ...]) -> ActionPlan:
        return follow_policy_plan(context, candidates, selector_id=self.plugin_id)

    def reset(self) -> None:
        pass  # the host clears shared memory between runs
