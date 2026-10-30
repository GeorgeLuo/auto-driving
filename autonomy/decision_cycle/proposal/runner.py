"""Proposal step runner.

``ProposalRunner`` calls each selected proposal plugin's ``propose`` with the
cycle context and admits one candidate per plugin. A plugin
that raises, returns a different type, or returns another identity is replaced
by a synthetic error candidate under its selected ID. A candidate that fails
lifecycle or size validation ends the step with status ``error`` and no
candidates. There is no plugin-count limit; with no plugins selected the step
returns no candidates and the plan is idle. A plugin reset error propagates.
"""

from __future__ import annotations

from typing import ClassVar

from autonomy.decision_cycle.action_identifiers import (
    ActionInputError,
    ActionProposalMatrixError,
    proposal_id_for,
    require_ascii_id,
    require_safe_int,
)
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.proposal.plugin import ProposalPlugin
from autonomy.decision_cycle.proposal.result import ProposalResult
from autonomy.decision_cycle.proposal.values import (
    ActionProposal,
    synthetic_error_proposal,
)
from autonomy.decision_cycle.runner import (
    StepRunner,
    describe_exception,
)
from autonomy.decision_cycle.timing import clock, elapsed_ms
from autonomy.plugins import PluginDefinition


def _admit_candidate(
    *,
    returned: object,
    invoked_plugin_id: str,
    frame_id: str,
    raised: BaseException | None = None,
) -> ActionProposal:
    if raised is not None:
        return synthetic_error_proposal(
            plugin_id=invoked_plugin_id,
            frame_id=frame_id,
            reason="plugin_exception",
        )
    if not isinstance(returned, ActionProposal):
        return synthetic_error_proposal(
            plugin_id=invoked_plugin_id,
            frame_id=frame_id,
            reason="plugin_invalid_return",
        )
    expected_id = proposal_id_for(invoked_plugin_id, frame_id)
    if (
        returned.plugin_id != invoked_plugin_id
        or returned.proposal_id != expected_id
    ):
        return synthetic_error_proposal(
            plugin_id=invoked_plugin_id,
            frame_id=frame_id,
            reason="plugin_invalid_return",
        )
    # Re-validate the lifecycle matrix and admit a reconstructed copy so
    # post-construction mutation of nested storage cannot change the candidate.
    try:
        plain = returned.to_dict()
        validated = ActionProposal.from_dict(plain)
    except ActionProposalMatrixError:
        raise
    except Exception as exc:
        raise ActionProposalMatrixError(
            f"candidate fails lifecycle/bounds matrix: {exc}"
        ) from exc
    if (
        validated.plugin_id != invoked_plugin_id
        or validated.proposal_id != expected_id
        or validated.frame_id != frame_id
    ):
        return synthetic_error_proposal(
            plugin_id=invoked_plugin_id,
            frame_id=frame_id,
            reason="plugin_invalid_return",
        )
    return validated


class ProposalRunner(StepRunner[ProposalPlugin]):
    """Run every selected proposal plugin once per cycle."""

    step: ClassVar[str] = "proposal"
    plugin_id: ClassVar[str] = "autonomy.proposal.plugin-runner-v0"

    def validate_plugin(self, plugin: ProposalPlugin, definition: PluginDefinition) -> None:
        require_ascii_id(definition.plugin_id, field_name="plugin_id")
        if not callable(getattr(plugin, "propose", None)):
            raise TypeError(f"proposal plugin {definition.entrypoint} must implement propose()")

    def __call__(self, context: DecisionFrameContext) -> ProposalResult:
        frame_id = context.frame_id
        # Entry precondition: invalid identity never yields a record.
        try:
            frame_id = require_ascii_id(frame_id, field_name="frame_id")
            require_safe_int(context.frame_index, field_name="frame_index")
            require_safe_int(context.timestamp_ms, field_name="timestamp_ms")
        except ValueError as exc:
            raise ActionInputError(str(exc)) from exc

        def fail(reason: str) -> ProposalResult:
            self.failure_count += 1
            self.last_error = reason
            return ProposalResult(frame_id=frame_id, status="error", reason=reason)

        with self._runtime_lock:
            self.run_count += 1
            self._clear_plugin_records()
            applied = self.applied
            candidates: list[ActionProposal] = []
            try:
                for definition, plugin in applied:
                    try:
                        candidates.append(
                            self._execute_plugin(definition, plugin, context)
                        )
                    except ActionProposalMatrixError:
                        return fail("action_proposal_matrix_violated")
                    except Exception:
                        return fail("synthetic_error_proposal_failed")
                if len(candidates) != len(applied):
                    return fail("action_plan_invariant_violated")
                result = ProposalResult(
                    frame_id=frame_id, status="ok", candidates=tuple(candidates)
                )
            except Exception:
                return fail("step_internal_error")
            self.last_error = None
            return result

    def _execute_plugin(
        self,
        definition: PluginDefinition,
        plugin: ProposalPlugin,
        context: DecisionFrameContext,
    ) -> ActionProposal:
        started = clock()
        error: str | None = None
        raised: BaseException | None = None
        returned: object = None
        try:
            returned = plugin.propose(context)
        except BaseException as exc:  # noqa: BLE001 - fail closed per proposal
            raised = exc
            error = describe_exception(exc)
        try:
            candidate = _admit_candidate(
                returned=returned,
                invoked_plugin_id=definition.plugin_id,
                frame_id=context.frame_id,
                raised=raised,
            )
            if candidate.lifecycle == "error" and error is None:
                error = candidate.reason
        except Exception as exc:
            error = describe_exception(exc)
            raise
        finally:
            # An isolated candidate error belongs to the plugin record; step
            # failures stay on ``status``.
            self._record_plugin(definition.plugin_id, elapsed_ms(started), error)
        return candidate
