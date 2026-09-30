from __future__ import annotations
import json
from pathlib import Path
from autonomy.decision_cycle.memory.evidence import MemoryProvenance, RetainedEvidence
from autonomy.decision_cycle.memory.activation import MEMORY_ACTIVATION_SCHEMA
from autonomy.decision_cycle.perception.evidence.values import ViewLocation


class _RecordingMemory:
    """Test double used only by activation tests.

    Keeps its epoch and records at ``<implementation_id>.state`` in the host map.
    """

    def __init__(
        self,
        *,
        fail_on_update: bool = False,
        fail_on_reset: bool = False,
        implementation_id: str = "recording_test",
        **_ignored,
    ) -> None:
        self.implementation_id = implementation_id
        self.fail_on_update = fail_on_update
        self.fail_on_reset = fail_on_reset

    @property
    def state_key(self) -> str:
        return f"{self.implementation_id}.state"

    def state(self, shared_memory) -> dict:
        current = shared_memory.get(self.state_key) if shared_memory is not None else None
        return current if isinstance(current, dict) else {"epoch": 1, "records": ()}

    def update(self, context, observation):
        if self.fail_on_update:
            raise RuntimeError("forced-update-failure")
        if context.shared_memory is None:
            raise ValueError("recording memory requires a shared-memory map")
        records = ()
        if observation is not None:
            records = (
                RetainedEvidence(
                    record_id=f"rec-{observation.observation_id}",
                    kind="observation_presence",
                    label="observed",
                    confidence=1.0,
                    provenance=MemoryProvenance(
                        observation_id=observation.observation_id,
                        evidence_id="observation",
                        coordinate_frame="image",
                        observed_at_ms=observation.created_at_ms,
                        updated_at_ms=context.timestamp_ms,
                        frame_id=context.frame_id,
                    ),
                    location=ViewLocation(frame="image", zone="center"),
                ),
            )
        context.shared_memory[self.state_key] = {
            "epoch": self.state(context.shared_memory)["epoch"],
            "records": records,
        }

    def reset(self, shared_memory):
        if self.fail_on_reset:
            raise RuntimeError("reset exploded")
        shared_memory[self.state_key] = {
            "epoch": self.state(shared_memory)["epoch"] + 1,
            "records": (),
        }

    def status(self, shared_memory) -> dict:
        state = self.state(shared_memory)
        return {
            "epoch_id": f"epoch-{state['epoch']}",
            "record_count": len(state["records"]),
            "records": [record.to_dict() for record in state["records"]],
        }


class _ConfigurableIdMemory(_RecordingMemory):
    """Allows activation to declare a custom implementation_id (including multibyte)."""


class _BrokenStringError(RuntimeError):
    def __str__(self) -> str:
        raise RuntimeError("stringification failed")


class _BrokenStrMemory(_RecordingMemory):
    def update(self, context, observation):
        raise _BrokenStringError("payload")

    def reset(self, shared_memory):
        raise _BrokenStringError("payload")

    def status(self, shared_memory):
        raise _BrokenStringError("payload")


def _valid_payload() -> dict:
    return {
        "schema": MEMORY_ACTIVATION_SCHEMA,
        "memory": {
            "implementation_id": "recording_test",
            "implementation_spec": (
                "tests.autonomy.decision_cycle.memory.activation_fixtures:_RecordingMemory"
            ),
            "implementation_config": {},
        },
    }


def _write_payload(root: str, payload: object) -> Path:
    path = Path(root) / "active.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
