from __future__ import annotations
import json
import os
import io
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from autonomy.decision.observation import Observation
from autonomy.decision.shadow_authority import AUTHORIZED_IDLE_REASON
from cli.automa_cli.automation import _record_decision_publish_skip
from cli.automa_cli.decision import (
    ENGINE_ID,
    apply_vehicle_decision,
    build_decision_stream_frame,
    get_vehicle_decision_info,
    _format_stream_frame,
    latest_decision_path,
    publish_shadow_decision_frame,
    strict_decode_apply_observation,
    stream_vehicle_decision,
    update_vehicle_decision,
    write_latest_decision_frame,
)
from implementations.decision.catalog import create_shadow_proposals_engine
from implementations.decision.shadow_adapter import ShadowProposalsAutonomyEngine
from tests.support.cli_runner import run_automa
from tests.cli.decision.shadow_decision_surfaces_fixtures import (
    ACTIVE_RUN,
    NO_MEM_RUN,
    ShadowDecisionSurfaceFixture,
)


class ShadowDecisionSurfaceTests(ShadowDecisionSurfaceFixture, unittest.TestCase):
    def test_publish_and_stream_once_cli(self) -> None:
        self._stage()
        cycle = self._sample_cycle()
        vehicle_runtime = self.runtime_root / "chase-sim-chaser"
        activation_path = (
            vehicle_runtime / "bundle" / "runtime" / "decision" / "active.json"
        )
        activation = json.loads(activation_path.read_text())
        published = publish_shadow_decision_frame(
            cycle_result=cycle,
            context_frame_id="frame_001",
            vehicle_id="chase-sim-chaser",
            vehicle_runtime_dir=vehicle_runtime,
            run_id="run-live",
            worker_pid=os.getpid(),
            activation=activation,
            staged_engine_id=ENGINE_ID,
        )
        self.assertTrue(published)
        # matching automation state for accept
        state_path = (
            vehicle_runtime / "bundle" / "runtime" / "automation" / "state.json"
        )
        state_path.parent.mkdir(parents=True, exist_ok=True)
        # rewrite frame with published_at now and pid
        frame_path = (
            vehicle_runtime
            / "bundle"
            / "runtime"
            / "automation"
            / "latest_decision.json"
        )
        frame = json.loads(frame_path.read_text())
        now_ms = int(__import__("time").time() * 1000)
        frame["published_at_ms"] = now_ms
        frame["run_id"] = "run-live"
        frame["worker_pid"] = os.getpid()
        write_latest_decision_frame(frame_path, frame)
        state_path.write_text(
            json.dumps(
                {
                    "schema": "automa_automation_run_state_v0",
                    "run_id": "run-live",
                    "status": "running",
                    "pid": os.getpid(),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        cli = run_automa(
            "vehicles",
            "stream",
            "decision",
            "--id",
            "chase-sim-chaser",
            "--once",
            "--json",
            runtime_root=self.runtime_root,
        )
        self.assertEqual(cli.returncode, 0, cli.stderr + cli.stdout)
        stream_payload = json.loads(cli.stdout)
        self.assertEqual(stream_payload["schema"], "vehicle_decision_stream_frame_v0")
        self.assertNotIn("applied_control", stream_payload)

    def test_continuous_json_emits_one_object_per_refresh_line(self) -> None:
        self._stage()
        cycle = self._sample_cycle()
        vehicle_runtime = self.runtime_root / "chase-sim-chaser"
        activation_path = (
            vehicle_runtime / "bundle" / "runtime" / "decision" / "active.json"
        )
        activation = json.loads(activation_path.read_text())
        now_ms = int(__import__("time").time() * 1000)
        frame = build_decision_stream_frame(
            cycle,
            vehicle_id="chase-sim-chaser",
            run_id="run-lines",
            worker_pid=os.getpid(),
            activation_engine_id=ENGINE_ID,
            activation_activated_at_ms=activation["activated_at_ms"],
            published_at_ms=now_ms,
        )
        vehicle_runtime_dir = self.runtime_root / "chase-sim-chaser"
        write_latest_decision_frame(latest_decision_path(vehicle_runtime_dir), frame)
        state_path = (
            vehicle_runtime_dir / "bundle" / "runtime" / "automation" / "state.json"
        )
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "run_id": "run-lines",
                    "status": "running",
                    "pid": os.getpid(),
                }
            ),
            encoding="utf-8",
        )
        output = io.StringIO()
        with patch(
            "cli.automa_cli.decision.time.sleep",
            side_effect=[None, KeyboardInterrupt],
        ):
            result = stream_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                refresh_s=0.05,
                json_output=True,
                output=output,
            )
        self.assertEqual(result.exit_code, 130)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertEqual(
                json.loads(line)["schema"],
                "vehicle_decision_stream_frame_v0",
            )

        latest_decision_path(vehicle_runtime_dir).unlink()
        error_output = io.StringIO()
        with patch(
            "cli.automa_cli.decision.time.sleep",
            side_effect=[None, KeyboardInterrupt],
        ):
            error_result = stream_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                refresh_s=0.05,
                json_output=True,
                output=error_output,
            )
        self.assertEqual(error_result.exit_code, 130)
        error_lines = error_output.getvalue().splitlines()
        self.assertEqual(len(error_lines), 2)
        for line in error_lines:
            payload = json.loads(line)
            self.assertEqual(payload["schema"], "vehicle_decision_error_v0")
            self.assertEqual(payload["error"], "latest_frame_missing")

    def test_human_stream_renders_combined_selected_and_idle_fields(self) -> None:
        selected = build_decision_stream_frame(
            self._sample_cycle(),
            vehicle_id="chase-sim-chaser",
            run_id="run-human",
            worker_pid=1,
            activation_engine_id=ENGINE_ID,
            activation_activated_at_ms=1000,
            published_at_ms=1000,
        )
        selected_text = _format_stream_frame(selected)
        for expected in (
            "Observation image: unavailable",
            "Retained: record_id=",
            "Candidate: plugin=avoid_recent_obstruction",
            "lifecycle=fresh",
            "freshness=fresh",
            "confidence=0.8",
            "reason=steer_away_left_obstruction",
            "source_refs=",
            "Selected contribution plugins: avoid_recent_obstruction",
            "proposed_applied=false",
            "Non-claims:",
        ):
            self.assertIn(expected, selected_text)

        engine = create_shadow_proposals_engine()
        no_memory_sequence = json.loads((NO_MEM_RUN / "sequence.json").read_text())
        raw = no_memory_sequence["frames"][0]
        idle_cycle, _ = engine.run_cycle(
            frame_id=raw["frame_id"],
            frame_index=raw["frame_index"],
            timestamp_ms=raw["timestamp_ms"],
            observation=strict_decode_apply_observation(raw["observation"]),
            memory=None,
        )
        idle = build_decision_stream_frame(
            idle_cycle,
            vehicle_id="chase-sim-chaser",
            run_id="run-human",
            worker_pid=1,
            activation_engine_id=ENGINE_ID,
            activation_activated_at_ms=1000,
            published_at_ms=1000,
        )
        idle_text = _format_stream_frame(idle)
        self.assertIn("Plan: status=idle selected=None", idle_text)
        self.assertIn("Selected contribution plugins: (none)", idle_text)
        self.assertIn("Candidate: plugin=avoid_recent_obstruction", idle_text)

    def test_invalid_activation_rejected_by_info_stream_and_apply(self) -> None:
        activation_path = (
            self.runtime_root
            / "chase-sim-chaser"
            / "bundle"
            / "runtime"
            / "decision"
            / "active.json"
        )
        mutations = (
            lambda payload: {**payload, "schema": "bogus_activation_v0"},
            lambda _payload: ["not", "an", "object"],
            lambda payload: {
                **payload,
                "decision": {
                    **payload["decision"],
                    "engine_spec": "autonomy.runtime.engine:IdleAutonomyEngine",
                },
            },
            lambda payload: {
                **payload,
                "decision": {
                    **payload["decision"],
                    "engine_config": {
                        **payload["decision"]["engine_config"],
                        "steer_magnitude": 0.0,
                    },
                },
            },
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self._stage()
                payload = json.loads(activation_path.read_text())
                mutated_activation = mutate(payload)
                activation_path.write_text(
                    json.dumps(mutated_activation),
                    encoding="utf-8",
                )
                info = get_vehicle_decision_info(
                    vehicle_id="chase-sim-chaser",
                    json_output=True,
                )
                stream = stream_vehicle_decision(
                    vehicle_id="chase-sim-chaser",
                    once=True,
                    json_output=True,
                )
                applied = apply_vehicle_decision(
                    vehicle_id="chase-sim-chaser",
                    from_run=ACTIVE_RUN,
                    json_output=True,
                )
                published = publish_shadow_decision_frame(
                    cycle_result=self._sample_cycle(),
                    context_frame_id="frame_001",
                    vehicle_id="chase-sim-chaser",
                    vehicle_runtime_dir=self.runtime_root / "chase-sim-chaser",
                    run_id="invalid-activation",
                    worker_pid=1,
                    activation=(
                        mutated_activation
                        if isinstance(mutated_activation, dict)
                        else None
                    ),
                    staged_engine_id=ENGINE_ID,
                )
                for result in (info, stream, applied):
                    self.assertEqual(result.exit_code, 2, result.message)
                    self.assertEqual(
                        json.loads(result.message)["error"],
                        "activation_invalid",
                    )
                self.assertFalse(published)

    def test_stream_wrong_engine_cli(self) -> None:
        self._stage(engine_id="idle")
        cli = run_automa(
            "vehicles",
            "stream",
            "decision",
            "--id",
            "chase-sim-chaser",
            "--once",
            "--json",
            runtime_root=self.runtime_root,
            check=False,
        )
        self.assertEqual(cli.returncode, 2)
        payload = json.loads(cli.stdout)
        self.assertEqual(payload["error"], "wrong_engine")

    def test_publish_rechecks_live_activation_after_restage(self) -> None:
        """Restage while a worker is 'running' must not republish an old generation."""

        self._stage()
        cycle = self._sample_cycle()
        vehicle_runtime = self.runtime_root / "chase-sim-chaser"
        activation_path = (
            vehicle_runtime / "bundle" / "runtime" / "decision" / "active.json"
        )
        activation_a = json.loads(activation_path.read_text())
        activated_a = activation_a["activated_at_ms"]
        self.assertTrue(
            publish_shadow_decision_frame(
                cycle_result=cycle,
                context_frame_id="frame_001",
                vehicle_id="chase-sim-chaser",
                vehicle_runtime_dir=vehicle_runtime,
                run_id="run-a",
                worker_pid=1,
                activation=activation_a,
                staged_engine_id=ENGINE_ID,
            )
        )
        frame_path = latest_decision_path(vehicle_runtime)
        self.assertTrue(frame_path.exists())
        first = json.loads(frame_path.read_text())
        self.assertEqual(first["activation_activated_at_ms"], activated_a)

        # Restage to idle invalidates latest and changes live activation.
        idle = update_vehicle_decision(
            vehicle_id="chase-sim-chaser",
            engine_id="idle",
            json_output=True,
        )
        self.assertEqual(idle.exit_code, 0, idle.message)
        self.assertFalse(
            frame_path.exists()
            or (
                frame_path.exists()
                and json.loads(frame_path.read_text()).get("schema")
                == "vehicle_decision_stream_frame_v0"
            )
        )

        # Startup-captured shadow activation must not allow republish after restage.
        republished = publish_shadow_decision_frame(
            cycle_result=cycle,
            context_frame_id="frame_001",
            vehicle_id="chase-sim-chaser",
            vehicle_runtime_dir=vehicle_runtime,
            run_id="run-a",
            worker_pid=1,
            activation=activation_a,
            staged_engine_id=ENGINE_ID,
        )
        self.assertFalse(republished)
        if frame_path.exists():
            payload = json.loads(frame_path.read_text())
            self.assertNotEqual(
                payload.get("schema"), "vehicle_decision_stream_frame_v0"
            )

        # Restage to a new shadow generation B: worker still holding A must not
        # publish a cycle labeled as B.
        self._stage()
        activation_b = json.loads(activation_path.read_text())
        self.assertNotEqual(activation_b["activated_at_ms"], activated_a)
        self.assertFalse(
            publish_shadow_decision_frame(
                cycle_result=cycle,
                context_frame_id="frame_001",
                vehicle_id="chase-sim-chaser",
                vehicle_runtime_dir=vehicle_runtime,
                run_id="run-b",
                worker_pid=1,
                activation=activation_a,  # generation-A worker capture
                staged_engine_id=ENGINE_ID,
            )
        )
        # Only a worker that reloads with generation B may publish under B.
        self.assertTrue(
            publish_shadow_decision_frame(
                cycle_result=cycle,
                context_frame_id="frame_001",
                vehicle_id="chase-sim-chaser",
                vehicle_runtime_dir=vehicle_runtime,
                run_id="run-b",
                worker_pid=1,
                activation=activation_b,
                staged_engine_id=ENGINE_ID,
            )
        )
        second = json.loads(frame_path.read_text())
        self.assertEqual(
            second["activation_activated_at_ms"],
            activation_b["activated_at_ms"],
        )

    def test_publish_skip_counter_write_failure_is_non_fatal(self) -> None:
        state: dict = {
            "decision": {
                "engine_id": ENGINE_ID,
                "latest_frame_publish_skips": 0,
                "latest_frame_publish_skip_reason": None,
            }
        }
        lock = threading.Lock()
        # Unwritable path: parent does not exist and cannot be created if we
        # force _write_json to raise.
        bad_path = Path("/nonexistent-automa-root-zzz/state.json")

        def boom(*_args, **_kwargs):
            raise OSError("disk full")

        with patch("cli.automa_cli.automation._write_json", side_effect=boom):
            # Must not raise even when persistence fails.
            _record_decision_publish_skip(
                state, bad_path, lock, reason="unit-test-write-fail"
            )
        self.assertEqual(state["decision"]["latest_frame_publish_skips"], 1)
        self.assertEqual(
            state["decision"]["latest_frame_publish_skip_reason"],
            "unit-test-write-fail",
        )

    def test_no_stale_republish_after_bad_step(self) -> None:
        self._stage()
        engine = ShadowProposalsAutonomyEngine()
        good = engine.step(
            __import__(
                "autonomy.runtime.engine", fromlist=["AutonomySnapshot"]
            ).AutonomySnapshot(
                observation=Observation(
                    observation_id="obs",
                    created_at_ms=1000,
                    sensor_snapshot={},
                ),
                memory=None,
                cycle={"frame_id": "frame_001", "frame_index": 1},
                timestamp_ms=1000,
            )
        )
        self.assertEqual(good.reason, AUTHORIZED_IDLE_REASON)
        first = engine.last_cycle_result
        self.assertIsNotNone(first)
        vehicle_runtime = self.runtime_root / "chase-sim-chaser"
        activation = json.loads(
            (
                vehicle_runtime / "bundle" / "runtime" / "decision" / "active.json"
            ).read_text()
        )
        self.assertTrue(
            publish_shadow_decision_frame(
                cycle_result=first,
                context_frame_id="frame_001",
                vehicle_id="chase-sim-chaser",
                vehicle_runtime_dir=vehicle_runtime,
                run_id="r1",
                worker_pid=1,
                activation=activation,
                staged_engine_id=ENGINE_ID,
            )
        )
        # bad step clears last_cycle_result; publish gate must not reuse prior
        engine.step(
            __import__(
                "autonomy.runtime.engine", fromlist=["AutonomySnapshot"]
            ).AutonomySnapshot(
                observation=None,
                memory=None,
                cycle={"frame_id": "!!!", "frame_index": 2},
                timestamp_ms=2000,
            )
        )
        self.assertIsNone(engine.last_cycle_result)
        self.assertFalse(
            publish_shadow_decision_frame(
                cycle_result=engine.last_cycle_result,
                context_frame_id="frame_bad",
                vehicle_id="chase-sim-chaser",
                vehicle_runtime_dir=vehicle_runtime,
                run_id="r1",
                worker_pid=1,
                activation=activation,
                staged_engine_id=ENGINE_ID,
            )
        )
