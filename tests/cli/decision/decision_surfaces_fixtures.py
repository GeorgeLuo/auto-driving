from __future__ import annotations
import json
import os
import tempfile
from autonomy.decision_cycle.memory.publication import EVIDENCE_KEY
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from autonomy.decision_cycle.action.hold import HOLD_IDLE_REASON
from autonomy.decision_cycle.activation import DECISION_STEPS
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.execution import ControlApplication
from autonomy.runtime.report import report_from_host_result
from implementations.decision_cycle.catalog import packaged_activation
from cli.automa_cli.decision import (
    DECISION_STREAM_MAX_AGE_MS,
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


def host_result_for_records(
    records: DecisionRecords,
    *,
    frame_id: str,
    frame_index: int,
    timestamp_ms: int,
    mode: str = "observe_only",
) -> SimpleNamespace:
    """A host cycle result whose application the shared report can publish."""

    control = records.control or AutonomyControl()
    return SimpleNamespace(
        context=DecisionFrameContext(
            frame_id=frame_id,
            frame_index=frame_index,
            timestamp_ms=timestamp_ms,
            mode=mode,
        ),
        proposal=records.proposal,
        plan=records.plan,
        action=records.action,
        application=ControlApplication(
            frame_id=frame_id,
            mode=mode,
            applied=False,
            reason=control.reason or "not-applied",
            control=control,
        ),
    )


def vehicle_report_for_records(
    records: DecisionRecords,
    *,
    vehicle_id: str,
    run_id: str,
    generation_id: str,
    frame_id: str,
    frame_index: int,
    timestamp_ms: int,
    published_at_ms: int | None = None,
    mode: str = "observe_only",
    values: dict | None = None,
) -> dict:
    """The ``vehicle_report_v0`` both viewers accept for these decision records."""

    report_values = {"stale_after_ms": DECISION_STREAM_MAX_AGE_MS}
    if values:
        report_values.update(values)
    return report_from_host_result(
        host_result_for_records(
            records,
            frame_id=frame_id,
            frame_index=frame_index,
            timestamp_ms=timestamp_ms,
            mode=mode,
        ),
        vehicle_id=vehicle_id,
        run_id=run_id,
        generation_id=generation_id,
        published_at_ms=timestamp_ms if published_at_ms is None else published_at_ms,
        values=report_values,
    ).to_dict()


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
        import cli.automa_cli.chase_observation as chase_observation_mod
        import cli.automa_cli.decision as decision_mod
        import cli.automa_cli.proposal as proposal_mod

        self._decision_mod = decision_mod
        self._old_runtime = decision_mod.RUNTIME_ROOT
        decision_mod.RUNTIME_ROOT = self.runtime_root
        self._chase_observation_mod = chase_observation_mod
        self._old_chase_runtime = chase_observation_mod.RUNTIME_ROOT
        chase_observation_mod.RUNTIME_ROOT = self.runtime_root
        self._proposal_root_patch = patch.object(proposal_mod, "RUNTIME_ROOT", self.runtime_root)
        self._proposal_root_patch.start()

    def tearDown(self) -> None:
        self._proposal_root_patch.stop()
        self._chase_observation_mod.RUNTIME_ROOT = self._old_chase_runtime
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
        records = self._sample_cycle(action)
        identity = packaged_identity(action)
        report = vehicle_report_for_records(
            records,
            vehicle_id="piracer",
            run_id="donkey-run-fixture",
            generation_id=identity["generation_id"],
            frame_id="frame_001",
            frame_index=1,
            timestamp_ms=1_000,
            published_at_ms=published_at_ms,
            values={
                "source_id": "donkeycar:piracer",
                "activation": identity,
                "source_frame": {
                    "frame_id": "frame_001",
                    "frame_index": 1,
                    "captured_at_ms": 1_000,
                    "completed_at_ms": published_at_ms,
                },
                "stale_after_ms": 1_000,
            },
        )
        return {
            "schema": "automa_physical_decision_publication_v0",
            "ok": True,
            "status": "ready",
            "reason": "",
            "read_at_ms": published_at_ms,
            "result_age_ms": 0,
            "stale_after_ms": 1_000,
            "decision": report,
        }
