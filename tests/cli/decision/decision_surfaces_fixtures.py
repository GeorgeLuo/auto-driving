from __future__ import annotations
import json
import os
import tempfile
from autonomy.decision_cycle.memory.publication import EVIDENCE_KEY
from pathlib import Path
from unittest.mock import patch
from autonomy.decision_cycle.action.hold import HOLD_IDLE_REASON
from autonomy.decision_cycle.activation import DECISION_STEPS
from implementations.decision_cycle.catalog import packaged_activation
from cli.automa_cli.decision import (
    strict_decode_apply_evidence,
    strict_decode_apply_observation,
)
from cli.automa_cli.decision_records import DecisionRecords, DecisionRunners
from cli.automa_cli.step_activations import (
    decision_generation_id,
    decision_identity,
    update_vehicle_step,
    vehicle_bundle,
)


SOURCES = Path(__file__).resolve().parents[1] / "sources" / "json"
ACTIVE_RUN = SOURCES / "apply_active_left"
NO_MEM_RUN = SOURCES / "apply_no_memory"
TWO_FRAME_RUN = SOURCES / "apply_two_frames"


def packaged_decision_steps(action: str = "hold") -> dict:
    """Step payloads for the packaged proposal, the built-in plan, and ``action``."""

    return {
        step: packaged_activation(step, [action] if step == "action" else None).to_payload()
        for step in DECISION_STEPS
    }


def packaged_identity(action: str = "hold") -> dict:
    steps = packaged_decision_steps(action)
    return {
        "generation_id": decision_generation_id(
            {step: packaged_activation(step, [action] if step == "action" else None) for step in DECISION_STEPS}
        ),
        "steps": steps,
    }


def sample_records(action: str = "hold") -> DecisionRecords:
    """One frame of the packaged decision steps over the recorded left evidence."""

    frame = json.loads((ACTIVE_RUN / "sequence.json").read_text())["frames"][0]
    return DecisionRunners.from_payloads(packaged_decision_steps(action)).run(
        frame_id="frame_001",
        frame_index=1,
        timestamp_ms=1000,
        observation=strict_decode_apply_observation(frame["observation"]),
        shared_memory={EVIDENCE_KEY: strict_decode_apply_evidence(frame["evidence"])},
    )


class DecisionSurfaceFixture:
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self._tmp.name) / "vehicles"
        self.runtime_root.mkdir(parents=True)
        self._env_patch = patch.dict(
            os.environ,
            {"AUTOMA_RUNTIME_ROOT": str(self.runtime_root)},
        )
        self._env_patch.start()
        # decision module reads RUNTIME_ROOT at import time; rebind for tests.
        import cli.automa_cli.decision as decision_mod

        self._decision_mod = decision_mod
        self._old_runtime = decision_mod.RUNTIME_ROOT
        decision_mod.RUNTIME_ROOT = self.runtime_root

    def tearDown(self) -> None:
        self._decision_mod.RUNTIME_ROOT = self._old_runtime
        self._env_patch.stop()
        self._tmp.cleanup()

    def _stage(
        self,
        *,
        action: str = "hold",
        proposal: bool = True,
        vehicle_id: str = "chase-sim-chaser",
    ) -> dict:
        """Stage the packaged proposals (unless ``proposal`` is False) and ``action``."""

        steps = [("action", [action])]
        if proposal:
            steps.insert(0, ("proposal", None))
        for step, plugins in steps:
            code, message = update_vehicle_step(
                vehicle_id=vehicle_id,
                step=step,
                plugins=plugins,
                runtime_root=self.runtime_root,
                json_output=True,
            )
            self.assertEqual(code, 0, message)
        return self._identity(vehicle_id)

    def _identity(self, vehicle_id: str = "chase-sim-chaser") -> dict:
        return decision_identity(vehicle_bundle(vehicle_id, self.runtime_root))

    def _sample_cycle(self, action: str = "hold") -> DecisionRecords:
        cycle = sample_records(action)
        if action == "hold":
            self.assertEqual(cycle.control.reason, HOLD_IDLE_REASON)
        return cycle

    def _physical_publication(
        self,
        *,
        published_at_ms: int = 2_000,
        action: str = "hold",
    ) -> dict:
        cycle = self._sample_cycle(action).to_dict()
        identity = packaged_identity(action)
        return {
            "schema": "automa_physical_decision_publication_v0",
            "ok": True,
            "status": "ready",
            "reason": "",
            "read_at_ms": published_at_ms,
            "result_age_ms": 0,
            "stale_after_ms": 1_000,
            "decision": {
                "vehicle_id": "piracer",
                "source_id": "donkeycar:piracer",
                "run_id": "donkey-run-fixture",
                "generation_id": identity["generation_id"],
                "frame_id": "frame_001",
                "frame_index": 1,
                "timestamp_ms": 1_000,
                "published_at_ms": published_at_ms,
                "activation": identity,
                "cycle": cycle,
            },
        }
