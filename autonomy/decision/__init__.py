"""Cycle records and re-exports of retained-evidence values.

``Observation`` is the current-frame record produced by ``observe``. Names
re-exported from ``autonomy.memory`` are retained evidence and the
``remember`` operation.
"""

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.errors import MemoryUpdateError
from autonomy.decision_cycle.action_identifiers import ShadowCycleInputError
from autonomy.decision_cycle.proposal.values import (
    ACTION_PROPOSAL_SCHEMA,
    ActionProposal,
    ProposedVehicleCommand,
    SourceRef,
)
from autonomy.decision_cycle.planning.selector import select_action_plan
from autonomy.decision_cycle.planning.values import ACTION_PLAN_SCHEMA, SELECTOR_ID, ActionPlan
from .cycle import (
    DECISION_CYCLE_RESULT_SCHEMA,
    DecisionCycle,
    DecisionCycleResult,
    DecisionSteps,
)
from .memory import (
    DEFAULT_MAX_DIAGNOSTIC_CHARS,
    DEFAULT_MAX_PROPERTY_BYTES,
    DEFAULT_MAX_SERIALIZED_BYTES,
    MEMORY_HEALTH_VALUES,
    MEMORY_SNAPSHOT_SCHEMA,
    MIN_MAX_SERIALIZED_BYTES,
    MemoryBounds,
    MemoryProvenance,
    MemorySnapshot,
    RetainedEvidence,
    canonical_json_bytes,
    canonical_json_utf8,
    detach_memory_snapshot,
    empty_memory_snapshot,
    ensure_strict_json_value,
    error_memory_snapshot,
    serialized_mapping_bytes,
    serialized_memory_snapshot_bytes,
    unavailable_memory_snapshot,
)
from autonomy.decision_cycle.observation.step import observation_from_perception
from autonomy.decision_cycle.observation.values import OBSERVATION_SCHEMA, Observation
from autonomy.decision_cycle.proposal.inputs import (
    DECISION_DATA_SOURCE_SCHEMA,
    ComponentEnvelope,
    DecisionDataSource,
    build_decision_data_source,
)
from .shadow_authority import (
    SHADOW_AUTHORITY_RESULT_SCHEMA,
    SHADOW_DECISION_CYCLE_RESULT_SCHEMA,
    ShadowAuthorityResult,
    ShadowDecisionCycleResult,
)
from .shadow_runner import ENGINE_ID, ShadowProposalsConfig, ShadowProposalsEngine
from autonomy.memory.activation import (
    MEMORY_ACTIVATION_SCHEMA,
    ActivatedMemoryStep,
    MemoryActivation,
    instantiate_memory_implementation,
    load_memory_implementation,
    load_memory_step_if_present,
    read_memory_activation,
)
from autonomy.memory.plugin import MemoryImplementation

__all__ = [
    "DECISION_CYCLE_RESULT_SCHEMA",
    "DEFAULT_MAX_DIAGNOSTIC_CHARS",
    "DEFAULT_MAX_PROPERTY_BYTES",
    "DEFAULT_MAX_SERIALIZED_BYTES",
    "MEMORY_ACTIVATION_SCHEMA",
    "MEMORY_HEALTH_VALUES",
    "MEMORY_SNAPSHOT_SCHEMA",
    "MIN_MAX_SERIALIZED_BYTES",
    "OBSERVATION_SCHEMA",
    "ActivatedMemoryStep",
    "DecisionCycle",
    "DecisionCycleResult",
    "DecisionFrameContext",
    "DecisionSteps",
    "MemoryUpdateError",
    "MemoryActivation",
    "MemoryBounds",
    "MemoryImplementation",
    "MemoryProvenance",
    "MemorySnapshot",
    "Observation",
    "RetainedEvidence",
    "canonical_json_bytes",
    "canonical_json_utf8",
    "detach_memory_snapshot",
    "empty_memory_snapshot",
    "ensure_strict_json_value",
    "error_memory_snapshot",
    "instantiate_memory_implementation",
    "load_memory_implementation",
    "load_memory_step_if_present",
    "observation_from_perception",
    "read_memory_activation",
    "serialized_mapping_bytes",
    "serialized_memory_snapshot_bytes",
    "unavailable_memory_snapshot",
    "ShadowCycleInputError",
    "DECISION_DATA_SOURCE_SCHEMA",
    "ComponentEnvelope",
    "DecisionDataSource",
    "build_decision_data_source",
    "ACTION_PROPOSAL_SCHEMA",
    "ActionProposal",
    "ProposedVehicleCommand",
    "SourceRef",
    "ACTION_PLAN_SCHEMA",
    "ActionPlan",
    "SELECTOR_ID",
    "select_action_plan",
    "SHADOW_AUTHORITY_RESULT_SCHEMA",
    "SHADOW_DECISION_CYCLE_RESULT_SCHEMA",
    "ShadowAuthorityResult",
    "ShadowDecisionCycleResult",
    "ENGINE_ID",
    "ShadowProposalsConfig",
    "ShadowProposalsEngine",
]
