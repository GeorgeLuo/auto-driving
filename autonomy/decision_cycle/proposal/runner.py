"""Proposal step runner.

``ProposalRunner`` builds the detached ``DecisionDataSource`` for the cycle,
calls each selected proposal plugin's ``propose`` with its own copy of the
source and the shared host map, and admits one candidate per plugin. A plugin
that raises or returns an invalid candidate is replaced by a synthetic error
candidate under its selected ID. Failures that leave no trustworthy candidate
set end the step with status ``error``. There is no plugin-count limit; with
no plugins selected the step returns no candidates and the plan is idle.
Plugin failures and missing inputs follow ``FAILURE_POLICY`` in ``interface``;
``describe_schema`` reports the step's contract.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, ClassVar

from autonomy.decision_cycle.errors import CYCLE_ERROR_REASONS
from autonomy.decision_cycle.action_identifiers import (
    ActionInputError,
    ActionProposalMatrixError,
    proposal_id_for,
    require_ascii_id,
    require_safe_int,
)
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.proposal.inputs import (
    ComponentEnvelope,
    DecisionDataSource,
    build_decision_data_source,
    default_capabilities,
    omit_forbidden_channel_keys,
    ready_envelope,
)
from autonomy.decision_cycle.proposal.interface import (
    FAILURE_POLICY,
    PROPOSAL_SCHEMA,
    composition_declaration,
)
from autonomy.decision_cycle.proposal.plugin import ProposalPlugin
from autonomy.decision_cycle.proposal.result import PROPOSAL_RESULT_SCHEMA, ProposalResult
from autonomy.decision_cycle.proposal.values import (
    ACTION_PROPOSAL_SCHEMA,
    MAX_PROPOSAL_BYTES,
    ActionProposal,
    synthetic_error_proposal,
)
from autonomy.decision_cycle.runner import StepRunner, describe_configuration, describe_plugin
from autonomy.plugins import PluginDefinition
from autonomy.serialization import canonical_json_size_bytes
from autonomy.shared_memory import SharedMemory


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
    # Re-validate lifecycle matrix + byte bounds and admit a reconstructed copy
    # so post-construction mutation of nested storage cannot inflate candidates.
    try:
        plain = returned.to_dict()
        size = canonical_json_size_bytes(plain)
        if size > MAX_PROPOSAL_BYTES:
            raise ActionProposalMatrixError(
                f"admitted proposal serializes to {size} bytes; max {MAX_PROPOSAL_BYTES}"
            )
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


def _declared_evidence_key(plugin: ProposalPlugin) -> str | None:
    key = getattr(plugin, "evidence_key", None)
    return key if isinstance(key, str) and key else None


class ProposalRunner(StepRunner[ProposalPlugin]):
    """Run every selected proposal plugin once per cycle."""

    step: ClassVar[str] = "proposal"
    plugin_id: ClassVar[str] = "autonomy.proposal.plugin-runner-v0"

    def validate_plugin(self, plugin: ProposalPlugin, definition: PluginDefinition) -> None:
        require_ascii_id(definition.plugin_id, field_name="plugin_id")
        if not callable(getattr(plugin, "propose", None)):
            raise TypeError(f"proposal plugin {definition.entrypoint} must implement propose()")

    @property
    def evidence_key(self) -> str | None:
        """The first host-map evidence key a selected plugin declares, for the audit copy."""

        for _definition, plugin in self.applied:
            key = _declared_evidence_key(plugin)
            if key is not None:
                return key
        return None

    def describe_schema(self) -> dict[str, Any]:
        """The step's contract: configuration, inputs, plugins, output, composition, failure."""

        with self._runtime_lock:
            applied = self.applied
            return {
                "schema": PROPOSAL_SCHEMA,
                "plugin_id": self.plugin_id,
                "runner": f"{type(self).__module__}:{type(self).__name__}",
                "configuration": describe_configuration(self.plugin_manager, applied),
                "inputs": [
                    {
                        "name": "observation",
                        "required": False,
                        "source": "the cycle's observation, in the source",
                        "missing_behavior": (
                            "the source marks it unavailable, or error with the "
                            "observation step's reason; the plugin is still called"
                            if FAILURE_POLICY.missing_input == "invoke"
                            else "the plugin is not called"
                        ),
                    },
                    {
                        "name": "shared_memory",
                        "required": False,
                        "source": "the host map on the frame context, shared by every plugin",
                        "missing_behavior": "plugins get an empty map, as in offline replay",
                    },
                    {
                        "name": "evidence",
                        "required": False,
                        "source": (
                            "an audit copy, in the source, of the host-map value at the "
                            "evidence_key the first selected plugin declares"
                        ),
                        "missing_behavior": "the source marks it unavailable",
                    },
                    {
                        "name": "capabilities",
                        "required": False,
                        "source": "the host's capabilities envelope in context.metadata",
                        "missing_behavior": "the source holds the default capabilities",
                    },
                    {
                        "name": "prior_host_applied_command",
                        "required": False,
                        "source": "the host's last applied command in context.metadata",
                        "missing_behavior": "the source marks it unavailable",
                    },
                ],
                "plugins": [
                    {
                        **describe_plugin(definition),
                        "evidence_key": _declared_evidence_key(plugin),
                    }
                    for definition, plugin in applied
                ],
                "output": {
                    "schema": PROPOSAL_RESULT_SCHEMA,
                    "records": [
                        {
                            "record": "candidates[]",
                            "meaning": (
                                f"one {ACTION_PROPOSAL_SCHEMA} per selected plugin, in "
                                "selection order; a synthetic error candidate when the "
                                "plugin raises or returns an invalid candidate"
                            ),
                        },
                        {
                            "record": "source",
                            "meaning": "the detached decision data source the plugins read",
                        },
                    ],
                    "error_reasons": sorted(CYCLE_ERROR_REASONS),
                },
                "composition": composition_declaration(),
                "failure_policy": FAILURE_POLICY.to_dict(),
            }

    def __call__(
        self,
        context: DecisionFrameContext,
        observation: Observation | dict[str, Any] | None,
    ) -> ProposalResult:
        """Propose for the cycle.

        ``context.metadata`` may carry ``observation_error`` and the host-reported
        ``prior_host_applied_command`` and ``capabilities`` envelopes.
        """

        metadata = context.metadata if isinstance(context.metadata, dict) else {}
        observation_error = metadata.get("observation_error")

        def envelope(key: str) -> ComponentEnvelope | None:
            value = metadata.get(key)
            return value if isinstance(value, ComponentEnvelope) else None

        return self.run(
            frame_id=context.frame_id,
            frame_index=context.frame_index,
            timestamp_ms=context.timestamp_ms,
            observation=observation,
            observation_error=observation_error if type(observation_error) is str else None,
            shared_memory=context.shared_memory,
            prior_host_applied_command=envelope("prior_host_applied_command"),
            capabilities=envelope("capabilities"),
        )

    def run(
        self,
        *,
        frame_id: str,
        frame_index: int,
        timestamp_ms: int,
        observation: Observation | dict[str, Any] | None = None,
        observation_error: str | None = None,
        shared_memory: SharedMemory | None = None,
        prior_host_applied_command: ComponentEnvelope | None = None,
        capabilities: ComponentEnvelope | None = None,
    ) -> ProposalResult:
        # Entry precondition: invalid identity never yields a record.
        try:
            frame_id = require_ascii_id(frame_id, field_name="frame_id")
            frame_index = require_safe_int(frame_index, field_name="frame_index")
            timestamp_ms = require_safe_int(timestamp_ms, field_name="timestamp_ms")
        except ValueError as exc:
            raise ActionInputError(str(exc)) from exc

        def fail(reason: str, *, source: DecisionDataSource | None) -> ProposalResult:
            self.failure_count += 1
            self.last_error = reason
            return ProposalResult(frame_id=frame_id, status="error", reason=reason, source=source)

        with self._runtime_lock:
            self.refresh_selection(shared_memory)
            self.run_count += 1
            if isinstance(observation, Observation):
                observation = Observation.from_dict(
                    omit_forbidden_channel_keys(observation.to_dict())
                )
            elif isinstance(observation, dict):
                observation = omit_forbidden_channel_keys(observation)
            elif observation is not None:
                return fail("decision_data_source_invalid", source=None)
            if observation_error is not None and type(observation_error) is not str:
                return fail("decision_data_source_invalid", source=None)

            evidence_key = self.evidence_key
            try:
                source = build_decision_data_source(
                    frame_id=frame_id,
                    frame_index=frame_index,
                    timestamp_ms=timestamp_ms,
                    observation=observation,
                    observation_error=observation_error,
                    # Absent + no error: no observation step for this cycle.
                    observation_configured=False,
                    evidence=(
                        shared_memory.get(evidence_key)
                        if shared_memory is not None and evidence_key is not None
                        else None
                    ),
                    capabilities=capabilities
                    or ready_envelope(default_capabilities(), updated_at_ms=timestamp_ms),
                    prior_host_applied_command=prior_host_applied_command,
                )
            except Exception:
                return fail("decision_data_source_invalid", source=None)

            # Without a host map (offline replay), plugins still get a map to read.
            plugin_memory: SharedMemory = shared_memory if shared_memory is not None else {}
            applied = self.applied
            candidates: list[ActionProposal] = []
            try:
                for definition, plugin in applied:
                    raised: BaseException | None = None
                    returned: object = None
                    try:
                        # Isolate the source so one plugin cannot mutate another's view;
                        # the host map is shared on purpose, as for the other steps.
                        returned = plugin.propose(deepcopy(source), plugin_memory)
                    except BaseException as exc:  # noqa: BLE001 - fail closed per proposal
                        raised = exc
                    try:
                        candidates.append(
                            _admit_candidate(
                                returned=returned,
                                invoked_plugin_id=definition.plugin_id,
                                frame_id=frame_id,
                                raised=raised,
                            )
                        )
                    except ActionProposalMatrixError:
                        return fail("action_proposal_matrix_violated", source=source)
                    except Exception:
                        return fail("synthetic_error_proposal_failed", source=source)
                if len(candidates) != len(applied):
                    return fail("action_plan_invariant_violated", source=source)
                result = ProposalResult(
                    frame_id=frame_id, status="ok", candidates=tuple(candidates), source=source
                )
            except Exception:
                return fail("step_internal_error", source=source)
            self.last_error = None
            return result
