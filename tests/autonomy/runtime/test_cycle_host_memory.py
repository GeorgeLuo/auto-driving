from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionSteps
from autonomy.decision_cycle.memory.errors import MemoryUpdateError
from autonomy.decision_cycle.memory.activation import (
    load_memory_step_if_present,
    read_memory_activation,
)
from autonomy.runtime import AutonomyControl, AutonomyManager
from autonomy.runtime.cycle_host import AutonomyCycleHost
from implementations.decision_cycle.memory.catalog import (
    build_memory_activation_payload,
)
from implementations.decision_cycle.memory.bounded_evidence.ledger import LEDGER_KEY
from tests.support.action_fixtures import fixed_control_composition


class _PushyEngine:
    def reset(self) -> None:
        return None

    def describe_schema(self) -> dict:
        return {
            "schema": "autonomy_engine_schema_v0",
            "engine_id": "pushy-test",
            "engine_spec": "tests:_PushyEngine",
        }

    def act(self, context, perception, observation):
        self.last_shared_memory = dict(context.shared_memory or {})
        self.last_observation = observation
        return fixed_control_composition(
            AutonomyControl(
                steering=0.7,
                throttle=0.4,
                confidence=1.0,
                reason="pushy-test-engine",
            )
        ).act(context, perception, observation)


RECORDING_SPEC = "tests.autonomy.decision_cycle.memory.activation_fixtures:_RecordingMemory"
STATE_KEY = "recording_test.state"


def _write_activation(root: Path, *, fail_on_update: bool = False) -> Path:
    path = root / "active.json"
    path.write_text(
        json.dumps(
            {
                "schema": "automa_memory_activation_v0",
                "memory": {
                    "implementation_id": "recording_test",
                    "implementation_spec": RECORDING_SPEC,
                    "implementation_config": {"fail_on_update": fail_on_update},
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _state(report: dict) -> dict:
    return report["plugins"][-1]["state"]


class CycleHostMemoryWiringTests(unittest.TestCase):
    def test_engine_cannot_mutate_step_owned_memory_through_cycle_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            step = load_memory_step_if_present(_write_activation(Path(tmp)))
            manager = AutonomyManager()
            manager.engine = _PushyEngine()
            from autonomy.decision_cycle.observation.values import Observation

            host = AutonomyCycleHost(
                manager=manager,
                steps=DecisionSteps(
                    observe=lambda context, perception: Observation(
                        observation_id="obs-1",
                        created_at_ms=1,
                        sensor_snapshot={},
                        summary=("test",),
                    ),
                    remember=step,
                ),
            )
            result = host.run(DecisionFrameContext("frame_1", 0, 1_000))
            owned = host.shared_memory[STATE_KEY]
            # Mutate the report handed to callers.
            _state(result.memory)["records"][0]["properties"]["tamper"] = True
            _state(result.memory)["record_count"] = 99
            self.assertEqual(owned["records"][0].properties, {})
            self.assertEqual(_state(step.report())["record_count"], 1)

    def test_host_shares_the_map_with_the_engine_and_reports_memory_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            step = load_memory_step_if_present(_write_activation(Path(tmp)))
            manager = AutonomyManager()
            manager.engine = _PushyEngine()
            host = AutonomyCycleHost(
                manager=manager,
                steps=DecisionSteps(remember=step),
            )

            result = host.run(
                DecisionFrameContext(
                    frame_id="frame_1",
                    frame_index=1,
                    timestamp_ms=100,
                )
            )
            # no observe step -> observation None; memory still runs
            self.assertEqual(_state(result.memory)["record_count"], 0)
            self.assertEqual(result.control.reason, "pushy-test-engine")
            self.assertTrue(result.control.steering > 0.0)
            self.assertIn(STATE_KEY, manager.engine.last_shared_memory)

            status = host.status()
            self.assertEqual(status["memory"]["implementation_id"], "recording_test")
            self.assertEqual(status["memory"]["plugins"][0]["state"]["epoch_id"], "epoch-1")
            self.assertIsNotNone(status["last_cycle"])

    def test_memory_failure_stops_before_engine_control(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            step = load_memory_step_if_present(
                _write_activation(Path(tmp), fail_on_update=True)
            )
            manager = AutonomyManager()
            manager.engine = _PushyEngine()
            host = AutonomyCycleHost(
                manager=manager,
                steps=DecisionSteps(remember=step),
            )
            with self.assertRaisesRegex(MemoryUpdateError, "forced-update-failure"):
                host.run(
                    DecisionFrameContext(frame_id="frame_x", frame_index=0, timestamp_ms=1)
                )
            self.assertFalse(hasattr(manager.engine, "last_shared_memory"))
            self.assertIsNone(host.last_result)

    def test_idle_engine_holds_while_memory_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            step = load_memory_step_if_present(_write_activation(Path(tmp)))
            host = AutonomyCycleHost(steps=DecisionSteps(remember=step))
            result = host.run(
                DecisionFrameContext(frame_id="frame_i", frame_index=0, timestamp_ms=1)
            )
            self.assertEqual(result.control.reason, "engine-idle")
            self.assertIsNone(result.action)
            self.assertIsNotNone(result.memory)

    def test_host_reset_memory_clears_the_map_but_keeps_fresh_plugin_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            step = load_memory_step_if_present(_write_activation(Path(tmp)))
            host = AutonomyCycleHost(steps=DecisionSteps(remember=step))
            from autonomy.decision_cycle.observation.values import Observation

            host.run(DecisionFrameContext("frame_fill", 1, 100))
            step.update(
                DecisionFrameContext("frame_fill", 1, 100, shared_memory=host.shared_memory),
                Observation("obs_fill", 90, {}),
            )
            host.shared_memory["perception.history"] = "from an earlier frame"
            self.assertEqual(_state(step.report())["record_count"], 1)

            report = host.reset_memory()

            self.assertEqual(_state(report)["record_count"], 0)
            self.assertEqual(_state(report)["epoch_id"], "epoch-2")
            self.assertEqual(set(host.shared_memory), {STATE_KEY})
            self.assertEqual(host.status()["memory"]["plugins"][0]["state"]["record_count"], 0)

    def test_host_reset_before_first_update_advances_the_ledger_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "active.json"
            path.write_text(json.dumps(build_memory_activation_payload()))
            step = load_memory_step_if_present(path)
            host = AutonomyCycleHost(steps=DecisionSteps(remember=step))
            self.assertEqual(_state(step.report())["epoch_id"], "epoch-1")
            self.assertEqual(_state(host.reset_memory())["epoch_id"], "epoch-2")
            self.assertEqual(_state(host.reset_memory())["epoch_id"], "epoch-3")
            result = host.run(DecisionFrameContext("f1", 1, 100))
            self.assertEqual(_state(result.memory)["epoch_id"], "epoch-3")
            self.assertEqual(host.shared_memory[LEDGER_KEY].epoch_id, "epoch-3")

    def test_host_shares_context_and_delivers_memory_updated_observation(self) -> None:
        from dataclasses import replace
        from autonomy.decision_cycle.observation.values import Observation
        seen = []

        def observe(context, perception):
            seen.append(context.shared_memory.get("test.previous"))
            return Observation(context.frame_id, context.timestamp_ms, {})

        def remember(context, observation):
            context.shared_memory["test.previous"] = context.frame_id
            context.shared_memory["decision.observation"] = replace(observation, summary=("updated",))
            return {"schema": "memory_report_v0", "plugins": []}

        manager = AutonomyManager()
        manager.engine = _PushyEngine()
        host = AutonomyCycleHost(manager=manager, steps=DecisionSteps(observe=observe, remember=remember))
        for index in range(2):
            result = host.run(DecisionFrameContext(f"frame-{index}", index, index))
            self.assertEqual(result.observation.summary, ("updated",))
            self.assertEqual(manager.engine.last_observation.summary, ("updated",))
            self.assertEqual(result.memory["plugins"], [])
        self.assertEqual(seen, [None, "frame-0"])

    def test_host_reset_memory_without_step_returns_none(self) -> None:
        host = AutonomyCycleHost()
        self.assertIsNone(host.reset_memory())

    def test_missing_memory_activation_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_memory_step_if_present(Path(tmp) / "active.json"))

    def test_activation_reader_used_by_loader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_activation(Path(tmp))
            activation = read_memory_activation(path)
            self.assertEqual(activation.implementation_id, "recording_test")


if __name__ == "__main__":
    unittest.main(verbosity=2)
