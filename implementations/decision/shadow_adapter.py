"""AutonomyManager adapter for the PR #74 shadow-proposals engine.

Loads via ``engine_cls(**engine_config)`` and exposes ``reset`` / ``describe_schema`` /
``step`` while delegating proposal logic to ``create_shadow_proposals_engine`` /
``ShadowProposalsEngine.run_cycle``. Does not invent decision policy.
"""

from __future__ import annotations

from typing import Any

from autonomy.decision.decision_data import ComponentEnvelope
from autonomy.decision.memory import MemorySnapshot
from autonomy.decision.observation import Observation
from autonomy.decision.shadow_authority import AUTHORIZED_IDLE_REASON, authorized_idle_control
from autonomy.decision.shadow_ids import ShadowCycleInputError
from autonomy.decision.shadow_runner import ENGINE_ID, ShadowProposalsConfig
from autonomy.runtime.engine import AutonomyControl, AutonomySnapshot
from implementations.decision.catalog import create_shadow_proposals_engine

ADAPTER_ENGINE_SPEC = (
    "implementations.decision.shadow_adapter:ShadowProposalsAutonomyEngine"
)
ENTRY_ERROR_REASON = "shadow-adapter-entry-error"
STEP_ERROR_REASON = "shadow-adapter-step-error"


class ShadowProposalsAutonomyEngine:
    """Thin AutonomyManager-facing wrapper around ``ShadowProposalsEngine``."""

    def __init__(self, **engine_config: Any) -> None:
        # engine_config is only ShadowProposalsConfig fields; missing → defaults.
        # Invalid values or unknown keys fail closed at construction.
        if engine_config:
            self._config = ShadowProposalsConfig(**engine_config)
        else:
            self._config = ShadowProposalsConfig()
        self._engine = create_shadow_proposals_engine(self._config)
        self.last_cycle_result = None

    def reset(self) -> None:
        self.last_cycle_result = None

    def describe_schema(self) -> dict[str, Any]:
        return {
            "schema": "autonomy_engine_schema_v0",
            "engine_id": ENGINE_ID,
            "engine_spec": ADAPTER_ENGINE_SPEC,
            "purpose": (
                "Shadow-only decision engine: proposals may be nonzero while "
                f"authorized AutonomyControl remains idle ({AUTHORIZED_IDLE_REASON})."
            ),
            "inputs": [
                "sensor_snapshot",
                "perception",
                "observation",
                "memory",
                "cycle",
                "mode",
                "user_steering",
                "user_throttle",
            ],
            "output": {
                "type": "AutonomyControl",
                "movement": "always idle",
            },
            "stages": {
                "action": "shadow_proposals_run_cycle",
                "memory": "inspectable_snapshot",
                "patterns": None,
                "projections": None,
            },
        }

    def step(self, snapshot: AutonomySnapshot) -> AutonomyControl:
        # Always clear before entry so a failed step never republishes a prior cycle.
        self.last_cycle_result = None
        try:
            kwargs = self._map_snapshot(snapshot)
        except (TypeError, ValueError, ShadowCycleInputError) as exc:
            return AutonomyControl(
                steering=0.0,
                throttle=0.0,
                confidence=1.0,
                reason=ENTRY_ERROR_REASON,
                metadata={
                    "error": f"{type(exc).__name__}: {exc}",
                    "engine_id": ENGINE_ID,
                },
            )
        except Exception as exc:  # noqa: BLE001 - fail closed at adapter boundary
            return AutonomyControl(
                steering=0.0,
                throttle=0.0,
                confidence=1.0,
                reason=ENTRY_ERROR_REASON,
                metadata={
                    "error": f"{type(exc).__name__}: {exc}",
                    "engine_id": ENGINE_ID,
                },
            )

        try:
            cycle_result, control = self._engine.run_cycle(**kwargs)
        except ShadowCycleInputError as exc:
            return AutonomyControl(
                steering=0.0,
                throttle=0.0,
                confidence=1.0,
                reason=ENTRY_ERROR_REASON,
                metadata={
                    "error": f"{type(exc).__name__}: {exc}",
                    "engine_id": ENGINE_ID,
                },
            )
        except Exception as exc:  # noqa: BLE001 - unexpected after entry
            return AutonomyControl(
                steering=0.0,
                throttle=0.0,
                confidence=1.0,
                reason=STEP_ERROR_REASON,
                metadata={
                    "error": f"{type(exc).__name__}: {exc}",
                    "engine_id": ENGINE_ID,
                },
            )

        self.last_cycle_result = cycle_result
        # Always authorized idle; proposed intent lives only on last_cycle_result.
        if isinstance(control, AutonomyControl):
            return control
        return authorized_idle_control()

    def _map_snapshot(self, snapshot: AutonomySnapshot) -> dict[str, Any]:
        if not isinstance(snapshot, AutonomySnapshot):
            raise TypeError(
                f"snapshot must be AutonomySnapshot; got {type(snapshot).__name__}"
            )
        cycle = snapshot.cycle if isinstance(snapshot.cycle, dict) else {}
        if "frame_id" not in cycle:
            raise ValueError("snapshot.cycle.frame_id is required")
        frame_id = cycle["frame_id"]
        if "frame_index" not in cycle:
            raise ValueError("snapshot.cycle.frame_index is required")
        frame_index = cycle["frame_index"]
        if type(frame_index) is not int:
            raise ValueError("snapshot.cycle.frame_index must be a non-bool int")
        timestamp_ms = snapshot.timestamp_ms
        if type(timestamp_ms) is not int:
            raise ValueError("snapshot.timestamp_ms must be a non-bool int")

        observation: Observation | dict[str, Any] | None
        if snapshot.observation is None:
            observation = None
        elif isinstance(snapshot.observation, (Observation, dict)):
            observation = snapshot.observation
        else:
            observation = None

        metadata = snapshot.metadata if isinstance(snapshot.metadata, dict) else {}
        observation_error = metadata.get("observation_error")
        if type(observation_error) is not str:
            observation_error = None

        memory = snapshot.memory if isinstance(snapshot.memory, MemorySnapshot) else None

        host_application = metadata.get("host_application")
        if not isinstance(host_application, ComponentEnvelope):
            host_application = None

        prior = metadata.get("prior_host_applied_command")
        if not isinstance(prior, ComponentEnvelope):
            prior = None

        capabilities = metadata.get("capabilities")
        if not isinstance(capabilities, ComponentEnvelope):
            capabilities = None

        drive_mode_gate = snapshot.mode if isinstance(snapshot.mode, str) else "unknown"

        return {
            "frame_id": frame_id,
            "frame_index": frame_index,
            "timestamp_ms": timestamp_ms,
            "observation": observation,
            "observation_error": observation_error,
            "memory": memory,
            "host_application": host_application,
            "prior_host_applied_command": prior,
            "drive_mode_gate": drive_mode_gate,
            "capabilities": capabilities,
        }
