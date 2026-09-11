"""Automa shadow decision surfaces tests (M006-05)."""

from __future__ import annotations

import json
import os
import io
import re
import shutil
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from autonomy.decision.memory import (
    MemoryBounds,
    MemoryProvenance,
    MemorySnapshot,
    RetainedEvidence,
    canonical_json_bytes,
    canonical_json_utf8,
)
from autonomy.decision.observation import Observation
from autonomy.decision.shadow_authority import AUTHORIZED_IDLE_REASON
from autonomy.perception import ViewLocation
from autonomy.runtime.manager import AutonomyManager
from cli.automa_cli.automation import _record_decision_publish_skip
from cli.automa_cli.decision import (
    ADAPTER_ENGINE_SPEC,
    DECISION_ENGINES,
    ENGINE_ID,
    accept_decision_stream_frame,
    apply_vehicle_decision,
    build_decision_stream_frame,
    get_vehicle_decision_info,
    _format_stream_frame,
    latest_decision_path,
    publish_shadow_decision_frame,
    strict_decode_apply_memory,
    strict_decode_apply_observation,
    stream_vehicle_decision,
    update_vehicle_decision,
    write_latest_decision_frame,
)
import threading
from implementations.decision.catalog import create_shadow_proposals_engine
from implementations.decision.shadow_adapter import ShadowProposalsAutonomyEngine
from tests.support.cli_runner import run_automa

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ACTIVE_RUN = FIXTURES / "apply_active_left"
NO_MEM_RUN = FIXTURES / "apply_no_memory"
TWO_FRAME_RUN = FIXTURES / "apply_two_frames"


def _stage_shadow(runtime_root: Path, vehicle_id: str = "chase-sim-chaser") -> None:
    result = update_vehicle_decision(
        vehicle_id=vehicle_id,
        engine_id=ENGINE_ID,
        json_output=True,
    )
    # update uses global RUNTIME_ROOT; patch env via process isolation for CLI.
    # For in-process calls, temporarily set AUTOMA_RUNTIME_ROOT via module path.
    del result


class ShadowDecisionSurfaceTests(unittest.TestCase):
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

    def _stage(self, engine_id: str = ENGINE_ID, vehicle_id: str = "chase-sim-chaser"):
        return update_vehicle_decision(
            vehicle_id=vehicle_id,
            engine_id=engine_id,
            json_output=True,
        )

    # --- stage / info -------------------------------------------------

    def test_stage_shadow_proposals_and_info_contract(self) -> None:
        update = self._stage()
        self.assertEqual(update.exit_code, 0, update.message)
        payload = json.loads(update.message)
        self.assertEqual(payload["schema"], "vehicle_decision_update_v0")
        self.assertEqual(payload["engine_id"], ENGINE_ID)
        self.assertEqual(
            payload["manifest"]["decision"]["engine_spec"],
            ADAPTER_ENGINE_SPEC,
        )
        self.assertEqual(
            set(payload["manifest"]["decision"]["engine_config"].keys()),
            {
                "enabled_plugins",
                "accepted_kinds",
                "retained_max_age_ms",
                "steer_magnitude",
            },
        )

        info = get_vehicle_decision_info(vehicle_id="chase-sim-chaser", json_output=True)
        self.assertEqual(info.exit_code, 0, info.message)
        info_payload = json.loads(info.message)
        self.assertEqual(info_payload["schema"], "vehicle_decision_info_v0")
        self.assertIsNotNone(info_payload["shadow"])
        shadow = info_payload["shadow"]
        self.assertEqual(
            shadow["decision_inputs"],
            [
                "observation",
                "memory",
                "patterns",
                "projections",
                "capabilities",
                "prior_host_applied_command",
            ],
        )
        self.assertEqual(shadow["enabled_plugins"], ["avoid_recent_obstruction"])
        self.assertEqual(shadow["selector_id"], "deterministic_first_active")
        self.assertEqual(shadow["authority"]["proposed_applied"], False)
        self.assertEqual(
            shadow["authority"]["authorized_idle_reason"],
            AUTHORIZED_IDLE_REASON,
        )
        self.assertEqual(info_payload["combined_view"]["view_id"], "decision-combined-v0")
        self.assertIn("path_template", info_payload["combined_view"])

        human = get_vehicle_decision_info(vehicle_id="chase-sim-chaser", json_output=False)
        self.assertEqual(human.exit_code, 0)
        self.assertIn("avoid_recent_obstruction", human.message)
        self.assertIn(AUTHORIZED_IDLE_REASON, human.message)
        self.assertIn("decision-combined-v0", human.message)

    def test_stage_unknown_engine_and_invalid_config(self) -> None:
        result = update_vehicle_decision(
            vehicle_id="chase-sim-chaser",
            engine_id="ghost",
            json_output=True,
        )
        self.assertEqual(result.exit_code, 2)
        payload = json.loads(result.message)
        self.assertEqual(payload["error"], "unknown_engine")
        self.assertIn("shadow-proposals", payload["message"])

        # Invalid catalog config fails closed before write.
        original = dict(DECISION_ENGINES[ENGINE_ID]["engine_config"])
        try:
            DECISION_ENGINES[ENGINE_ID]["engine_config"] = {
                **original,
                "steer_magnitude": 0.0,
            }
            bad = update_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                engine_id=ENGINE_ID,
                json_output=True,
            )
            self.assertEqual(bad.exit_code, 2)
            self.assertEqual(json.loads(bad.message)["error"], "invalid_engine_config")
            activation = (
                self.runtime_root
                / "chase-sim-chaser"
                / "bundle"
                / "runtime"
                / "decision"
                / "active.json"
            )
            self.assertFalse(activation.exists())
        finally:
            DECISION_ENGINES[ENGINE_ID]["engine_config"] = original

    def test_info_missing_activation(self) -> None:
        result = get_vehicle_decision_info(vehicle_id="missing", json_output=True)
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(json.loads(result.message)["error"], "activation_missing")

    def test_staged_adapter_loads_via_autonomy_manager(self) -> None:
        self._stage()
        entry = DECISION_ENGINES[ENGINE_ID]
        manager = AutonomyManager(
            default_engine_spec=entry["engine_spec"],
            default_engine_config=dict(entry["engine_config"]),
        )
        self.assertIsInstance(manager.engine, ShadowProposalsAutonomyEngine)

    # --- stream acceptance --------------------------------------------

    def _sample_cycle(self):
        engine = create_shadow_proposals_engine()
        obs = strict_decode_apply_observation(
            json.loads((ACTIVE_RUN / "sequence.json").read_text())["frames"][0][
                "observation"
            ]
        )
        mem = strict_decode_apply_memory(
            json.loads((ACTIVE_RUN / "sequence.json").read_text())["frames"][0]["memory"]
        )
        cycle, control = engine.run_cycle(
            frame_id="frame_001",
            frame_index=1,
            timestamp_ms=1000,
            observation=obs,
            memory=mem,
        )
        self.assertEqual(control.reason, AUTHORIZED_IDLE_REASON)
        return cycle

    def test_build_stream_frame_no_applied_control(self) -> None:
        cycle = self._sample_cycle()
        frame = build_decision_stream_frame(
            cycle,
            vehicle_id="chase-sim-chaser",
            run_id="run-1",
            worker_pid=12345,
            activation_engine_id=ENGINE_ID,
            activation_activated_at_ms=1000,
            published_at_ms=2000,
        )
        self.assertEqual(frame["schema"], "vehicle_decision_stream_frame_v0")
        self.assertNotIn("applied_control", frame)
        self.assertFalse(frame["authority_summary"]["proposed_applied"])
        self.assertEqual(
            frame["authority_summary"]["authorized_output"]["reason"],
            AUTHORIZED_IDLE_REASON,
        )
        self.assertIsNotNone(frame["authority_summary"]["proposed"])
        self.assertNotEqual(frame["authority_summary"]["proposed"]["steering"], 0.0)
        # selected candidate carries source_refs in plan_summary
        plan = frame["plan_summary"]
        self.assertEqual(plan["status"], "selected")
        selected = next(
            c
            for c in plan["candidates"]
            if c["proposal_id"] == plan["selected_proposal_id"]
        )
        self.assertTrue(selected["source_refs"])

    def test_stream_acceptance_production_predicate(self) -> None:
        cycle = self._sample_cycle()
        frame = build_decision_stream_frame(
            cycle,
            vehicle_id="chase-sim-chaser",
            run_id="run-1",
            worker_pid=42,
            activation_engine_id=ENGINE_ID,
            activation_activated_at_ms=1000,
            published_at_ms=5000,
        )
        activation = {
            "schema": "automa_decision_activation_v0",
            "activated_at_ms": 1000,
            "decision": {
                "engine_id": ENGINE_ID,
                "engine_spec": ADAPTER_ENGINE_SPEC,
                "engine_config": dict(DECISION_ENGINES[ENGINE_ID]["engine_config"]),
            },
        }
        state = {"run_id": "run-1", "status": "running", "pid": 42}

        accept_decision_stream_frame(
            frame,
            activation=activation,
            automation_state=state,
            now_ms=6000,
            is_pid_alive=lambda pid: True,
        )

        # wrong engine
        with self.assertRaises(Exception) as ctx:
            accept_decision_stream_frame(
                frame,
                activation={
                    "activated_at_ms": 1000,
                    "decision": {"engine_id": "idle", "engine_config": {}},
                },
                automation_state=state,
                now_ms=6000,
                is_pid_alive=lambda pid: True,
            )
        self.assertEqual(ctx.exception.error, "wrong_engine")

        # dead worker
        with self.assertRaises(Exception) as ctx:
            accept_decision_stream_frame(
                frame,
                activation=activation,
                automation_state=state,
                now_ms=6000,
                is_pid_alive=lambda pid: False,
            )
        self.assertEqual(ctx.exception.error, "latest_frame_stale")

        # completed status
        with self.assertRaises(Exception) as ctx:
            accept_decision_stream_frame(
                frame,
                activation=activation,
                automation_state={**state, "status": "completed"},
                now_ms=6000,
                is_pid_alive=lambda pid: True,
            )
        self.assertEqual(ctx.exception.error, "latest_frame_stale")

        # future published_at
        future = dict(frame)
        future["published_at_ms"] = 9000
        with self.assertRaises(Exception) as ctx:
            accept_decision_stream_frame(
                future,
                activation=activation,
                automation_state=state,
                now_ms=6000,
                is_pid_alive=lambda pid: True,
            )
        self.assertEqual(ctx.exception.error, "latest_frame_stale")

        # over age
        old = dict(frame)
        old["published_at_ms"] = 0
        with self.assertRaises(Exception) as ctx:
            accept_decision_stream_frame(
                old,
                activation=activation,
                automation_state=state,
                now_ms=60_000,
                is_pid_alive=lambda pid: True,
                max_age_ms=30_000,
            )
        self.assertEqual(ctx.exception.error, "latest_frame_stale")

        # run_id mismatch
        with self.assertRaises(Exception) as ctx:
            accept_decision_stream_frame(
                frame,
                activation=activation,
                automation_state={**state, "run_id": "other"},
                now_ms=6000,
                is_pid_alive=lambda pid: True,
            )
        self.assertEqual(ctx.exception.error, "latest_frame_stale")

        # generation / activated_at_ms mismatch (restage)
        with self.assertRaises(Exception) as ctx:
            accept_decision_stream_frame(
                frame,
                activation={**activation, "activated_at_ms": 9999},
                automation_state=state,
                now_ms=6000,
                is_pid_alive=lambda pid: True,
            )
        self.assertEqual(ctx.exception.error, "latest_frame_stale")

        # tampered plan_summary must not pass check #10
        tampered = dict(frame)
        tampered["plan_summary"] = {
            "status": "selected",
            "selected_proposal_id": "liar",
            "candidates": [],
            "contributions": [],
        }
        with self.assertRaises(Exception) as ctx:
            accept_decision_stream_frame(
                tampered,
                activation=activation,
                automation_state=state,
                now_ms=6000,
                is_pid_alive=lambda pid: True,
            )
        self.assertEqual(ctx.exception.error, "latest_frame_invalid")

        # Runner-owned selector contract: selected must be the actual active
        # winner, idle cannot hide an active candidate, and candidates must
        # match the activation enabled-plugin set.
        from cli.automa_cli.decision import _authority_summary, _plan_summary

        def _selector_error(mutator) -> str:
            mutated = deepcopy(frame)
            mutator(mutated)
            cycle_mut = mutated["cycle"]
            mutated["plan_summary"] = _plan_summary(cycle_mut["plan"])
            mutated["authority_summary"] = _authority_summary(
                cycle_mut["authority"], cycle_mut
            )
            with self.assertRaises(Exception) as raised:
                accept_decision_stream_frame(
                    mutated,
                    activation=activation,
                    automation_state=state,
                    now_ms=6000,
                    is_pid_alive=lambda pid: True,
                )
            return raised.exception.error

        def selected_inactive(mutated):
            candidate = mutated["cycle"]["plan"]["candidates"][0]
            candidate.update(
                {
                    "lifecycle": "inactive",
                    "freshness": "none",
                    "available": False,
                    "command": None,
                    "source_refs": [],
                }
            )
            mutated["cycle"]["authority"]["proposed"] = None
            mutated["cycle"]["authority"]["proposed_equals_authorized"] = True

        self.assertEqual(
            _selector_error(selected_inactive),
            "latest_frame_invalid",
        )

        def idle_with_active(mutated):
            plan_mut = mutated["cycle"]["plan"]
            plan_mut["status"] = "idle"
            plan_mut["selected_proposal_id"] = None
            plan_mut["contributions"] = []
            mutated["cycle"]["authority"]["proposed"] = None
            mutated["cycle"]["authority"]["proposed_equals_authorized"] = True

        self.assertEqual(_selector_error(idle_with_active), "latest_frame_invalid")

        def ghost_plugin(mutated):
            candidate = mutated["cycle"]["plan"]["candidates"][0]
            candidate["plugin_id"] = "ghost"
            candidate["proposal_id"] = "ghost:frame_001"
            contribution = mutated["cycle"]["plan"]["contributions"][0]
            contribution["plugin_id"] = "ghost"
            contribution["proposal_id"] = "ghost:frame_001"
            mutated["cycle"]["plan"]["selected_proposal_id"] = "ghost:frame_001"

        self.assertEqual(_selector_error(ghost_plugin), "latest_frame_invalid")

        # envelope: bool worker_pid must not match int pid via truthiness
        bool_pid = dict(frame)
        bool_pid["worker_pid"] = True
        with self.assertRaises(Exception) as ctx:
            accept_decision_stream_frame(
                bool_pid,
                activation=activation,
                automation_state={"run_id": "run-1", "status": "running", "pid": 1},
                now_ms=6000,
                is_pid_alive=lambda pid: True,
            )
        self.assertEqual(ctx.exception.error, "latest_frame_invalid")

        # envelope: vehicle_id / run_id must be non-empty strings
        for key, value in (("vehicle_id", None), ("run_id", None), ("frame_index", "x")):
            bad = dict(frame)
            bad[key] = value
            with self.assertRaises(Exception) as ctx:
                accept_decision_stream_frame(
                    bad,
                    activation=activation,
                    automation_state=state,
                    now_ms=6000,
                    is_pid_alive=lambda pid: True,
                )
            self.assertEqual(ctx.exception.error, "latest_frame_invalid")

        # envelope: cycle.schema must be exact shadow_decision_cycle_result_v0
        bad_cycle = dict(frame)
        bad_cycle["cycle"] = dict(frame["cycle"])
        bad_cycle["cycle"]["schema"] = "bogus_cycle_v0"
        with self.assertRaises(Exception) as ctx:
            accept_decision_stream_frame(
                bad_cycle,
                activation=activation,
                automation_state=state,
                now_ms=6000,
                is_pid_alive=lambda pid: True,
            )
        self.assertEqual(ctx.exception.error, "latest_frame_invalid")

        # forbidden applied_control / arbitrary top-level extra key
        for extra_key in ("applied_control", "extra_top_level"):
            extra = dict(frame)
            extra[extra_key] = {"steering": 0.0, "throttle": 0.0}
            with self.assertRaises(Exception) as ctx:
                accept_decision_stream_frame(
                    extra,
                    activation=activation,
                    automation_state=state,
                    now_ms=6000,
                    is_pid_alive=lambda pid: True,
                )
            self.assertEqual(ctx.exception.error, "latest_frame_invalid")

        # arbitrary cycle key / omitted required nullable source
        cycle_extra = dict(frame)
        cycle_extra["cycle"] = dict(frame["cycle"])
        cycle_extra["cycle"]["extra_cycle_key"] = True
        with self.assertRaises(Exception) as ctx:
            accept_decision_stream_frame(
                cycle_extra,
                activation=activation,
                automation_state=state,
                now_ms=6000,
                is_pid_alive=lambda pid: True,
            )
        self.assertEqual(ctx.exception.error, "latest_frame_invalid")

        cycle_no_source = dict(frame)
        cycle_no_source["cycle"] = dict(frame["cycle"])
        del cycle_no_source["cycle"]["source"]
        # rebuild summaries as if source were absent so only omission is tested
        from cli.automa_cli.decision import (
            _authority_summary,
            _memory_summary,
            _observation_summary,
            _plan_summary,
        )

        cycle_no_source["observation_summary"] = _observation_summary(None)
        cycle_no_source["memory_summary"] = _memory_summary(None)
        cycle_no_source["plan_summary"] = _plan_summary(
            cycle_no_source["cycle"].get("plan")
            if isinstance(cycle_no_source["cycle"].get("plan"), dict)
            else None
        )
        cycle_no_source["authority_summary"] = _authority_summary(
            cycle_no_source["cycle"].get("authority")
            if isinstance(cycle_no_source["cycle"].get("authority"), dict)
            else {},
            cycle_no_source["cycle"],
        )
        with self.assertRaises(Exception) as ctx:
            accept_decision_stream_frame(
                cycle_no_source,
                activation=activation,
                automation_state=state,
                now_ms=6000,
                is_pid_alive=lambda pid: True,
            )
        self.assertEqual(ctx.exception.error, "latest_frame_invalid")

        # invalid PR #74 frame-id grammar
        bad_id = dict(frame)
        bad_id["frame_id"] = "bad frame!"
        bad_id["cycle"] = dict(frame["cycle"])
        bad_id["cycle"]["frame_id"] = "bad frame!"
        # keep summaries consistent with cycle frame_id field only via plan rebuild
        # (frame_id grammar fails before summary compare)
        with self.assertRaises(Exception) as ctx:
            accept_decision_stream_frame(
                bad_id,
                activation=activation,
                automation_state=state,
                now_ms=6000,
                is_pid_alive=lambda pid: True,
            )
        self.assertEqual(ctx.exception.error, "latest_frame_invalid")

        # Nested authority/command semantics (consistent summary+cycle tamper rejected)
        from cli.automa_cli.decision import (
            _authority_summary,
            _memory_summary,
            _observation_summary,
            _plan_summary,
        )

        def _accept_with_cycle(mutated_cycle: dict) -> str:
            mutated = dict(frame)
            mutated["cycle"] = mutated_cycle
            plan = (
                mutated_cycle.get("plan")
                if isinstance(mutated_cycle.get("plan"), dict)
                else None
            )
            authority = (
                mutated_cycle.get("authority")
                if isinstance(mutated_cycle.get("authority"), dict)
                else {}
            )
            source = (
                mutated_cycle.get("source")
                if isinstance(mutated_cycle.get("source"), dict)
                else None
            )
            mutated["observation_summary"] = _observation_summary(source)
            mutated["memory_summary"] = _memory_summary(source)
            mutated["plan_summary"] = _plan_summary(plan)
            mutated["authority_summary"] = _authority_summary(authority, mutated_cycle)
            with self.assertRaises(Exception) as raised:
                accept_decision_stream_frame(
                    mutated,
                    activation=activation,
                    automation_state=state,
                    now_ms=6000,
                    is_pid_alive=lambda pid: True,
                )
            return raised.exception.error

        # (1) non-idle authorized_output in cycle + matching summary
        non_idle = dict(frame["cycle"])
        non_idle["authority"] = dict(frame["cycle"]["authority"])
        non_idle["authority"]["authorized_output"] = dict(
            frame["cycle"]["authority"]["authorized_output"]
        )
        non_idle["authority"]["authorized_output"]["steering"] = 0.9
        self.assertEqual(_accept_with_cycle(non_idle), "latest_frame_invalid")

        # (2) live authority_mode
        live_mode = dict(frame["cycle"])
        live_mode["authority"] = dict(frame["cycle"]["authority"])
        live_mode["authority"]["authority_mode"] = "live_control"
        self.assertEqual(_accept_with_cycle(live_mode), "latest_frame_invalid")

        # (3) extra key on candidate command
        cmd_extra = dict(frame["cycle"])
        cmd_extra["plan"] = dict(frame["cycle"]["plan"])
        cmd_extra["plan"]["candidates"] = [
            dict(c) for c in frame["cycle"]["plan"]["candidates"]
        ]
        cmd0 = dict(cmd_extra["plan"]["candidates"][0])
        command = dict(cmd0["command"])
        command["extra_cmd_key"] = True
        cmd0["command"] = command
        cmd_extra["plan"]["candidates"][0] = cmd0
        self.assertEqual(_accept_with_cycle(cmd_extra), "latest_frame_invalid")

        # (4) bogus candidate command schema
        cmd_schema = dict(frame["cycle"])
        cmd_schema["plan"] = dict(frame["cycle"]["plan"])
        cmd_schema["plan"]["candidates"] = [
            dict(c) for c in frame["cycle"]["plan"]["candidates"]
        ]
        cmd0b = dict(cmd_schema["plan"]["candidates"][0])
        command_b = dict(cmd0b["command"])
        command_b["schema"] = "bogus"
        cmd0b["command"] = command_b
        cmd_schema["plan"]["candidates"][0] = cmd0b
        self.assertEqual(_accept_with_cycle(cmd_schema), "latest_frame_invalid")

        # Aggregate cycle alignment: valid nested objects that do not form one cycle.
        from autonomy.decision.action_proposal import ProposedVehicleCommand
        from implementations.decision.catalog import create_shadow_proposals_engine

        engine = create_shadow_proposals_engine()
        cycle2, _ = engine.run_cycle(
            frame_id="frame_002",
            frame_index=2,
            timestamp_ms=2000,
            observation=strict_decode_apply_observation(
                json.loads((ACTIVE_RUN / "sequence.json").read_text())["frames"][0][
                    "observation"
                ]
            ),
            memory=strict_decode_apply_memory(
                json.loads((ACTIVE_RUN / "sequence.json").read_text())["frames"][0][
                    "memory"
                ]
            ),
        )
        cycle2_dict = cycle2.to_dict()

        # (A) authority.proposed idle zeros while selected plan command is nonzero
        idle_proposed = dict(frame["cycle"])
        idle_proposed["authority"] = dict(frame["cycle"]["authority"])
        idle_proposed["authority"]["proposed"] = ProposedVehicleCommand(
            steering=0.0, throttle=0.0
        ).to_dict()
        idle_proposed["authority"]["proposed_equals_authorized"] = True
        self.assertEqual(_accept_with_cycle(idle_proposed), "latest_frame_invalid")

        # (B) replace plan with a valid plan from another frame_id
        other_plan = dict(frame["cycle"])
        other_plan["plan"] = cycle2_dict["plan"]
        self.assertEqual(_accept_with_cycle(other_plan), "latest_frame_invalid")

        # (C) replace source with another frame's source and retarget envelope timing
        other_source = dict(frame)
        other_source["cycle"] = dict(frame["cycle"])
        other_source["cycle"]["source"] = cycle2_dict["source"]
        other_source["frame_index"] = 2
        other_source["timestamp_ms"] = 2000
        other_source["observation_summary"] = _observation_summary(
            other_source["cycle"]["source"]
        )
        other_source["memory_summary"] = _memory_summary(
            other_source["cycle"]["source"]
        )
        other_source["plan_summary"] = _plan_summary(other_source["cycle"]["plan"])
        other_source["authority_summary"] = _authority_summary(
            other_source["cycle"]["authority"], other_source["cycle"]
        )
        with self.assertRaises(Exception) as ctx:
            accept_decision_stream_frame(
                other_source,
                activation=activation,
                automation_state=state,
                now_ms=6000,
                is_pid_alive=lambda pid: True,
            )
        self.assertEqual(ctx.exception.error, "latest_frame_invalid")

        # (D) only top-level frame_index/timestamp_ms diverge from source
        bad_timing = dict(frame)
        bad_timing["frame_index"] = 99
        bad_timing["timestamp_ms"] = 99999
        with self.assertRaises(Exception) as ctx:
            accept_decision_stream_frame(
                bad_timing,
                activation=activation,
                automation_state=state,
                now_ms=6000,
                is_pid_alive=lambda pid: True,
            )
        self.assertEqual(ctx.exception.error, "latest_frame_invalid")

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
        state_path = vehicle_runtime / "bundle" / "runtime" / "automation" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        # rewrite frame with published_at now and pid
        frame_path = vehicle_runtime / "bundle" / "runtime" / "automation" / "latest_decision.json"
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
            vehicle_runtime_dir
            / "bundle"
            / "runtime"
            / "automation"
            / "state.json"
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
        self.assertFalse(frame_path.exists() or (
            frame_path.exists()
            and json.loads(frame_path.read_text()).get("schema")
            == "vehicle_decision_stream_frame_v0"
        ))

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
            self.assertNotEqual(payload.get("schema"), "vehicle_decision_stream_frame_v0")

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
            __import__("autonomy.runtime.engine", fromlist=["AutonomySnapshot"]).AutonomySnapshot(
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
            __import__("autonomy.runtime.engine", fromlist=["AutonomySnapshot"]).AutonomySnapshot(
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

    # --- apply --------------------------------------------------------

    def test_apply_requires_id_and_shadow_engine(self) -> None:
        missing = apply_vehicle_decision(
            vehicle_id=None,
            from_run=ACTIVE_RUN,
            json_output=True,
        )
        self.assertEqual(missing.exit_code, 2)
        self.assertEqual(json.loads(missing.message)["error"], "missing_vehicle_id")

        self._stage(engine_id="idle")
        wrong = apply_vehicle_decision(
            vehicle_id="chase-sim-chaser",
            from_run=ACTIVE_RUN,
            json_output=True,
        )
        self.assertEqual(wrong.exit_code, 2)
        self.assertEqual(json.loads(wrong.message)["error"], "wrong_engine")

    def test_apply_digest_determinism_byte_equality(self) -> None:
        self._stage()
        first = apply_vehicle_decision(
            vehicle_id="chase-sim-chaser",
            from_run=ACTIVE_RUN,
            json_output=True,
        )
        second = apply_vehicle_decision(
            vehicle_id="chase-sim-chaser",
            from_run=ACTIVE_RUN,
            json_output=True,
        )
        self.assertEqual(first.exit_code, 0, first.message)
        self.assertEqual(second.exit_code, 0, second.message)
        a = json.loads(first.message)
        b = json.loads(second.message)
        self.assertTrue(a["deterministic"])
        self.assertEqual(a["digest_sha256"], b["digest_sha256"])
        self.assertEqual(
            canonical_json_utf8(a["digest"]),
            canonical_json_utf8(b["digest"]),
        )
        # length-only equality is not the success criterion used
        self.assertEqual(
            canonical_json_bytes(a["digest"]),
            len(canonical_json_utf8(a["digest"])),
        )
        self.assertFalse(a["recorded"])
        self.assertIsNone(a["record_dir"])
        frame0 = a["digest"]["frames"][0]
        self.assertFalse(frame0["proposed_applied"])
        self.assertEqual(
            frame0["authorized_output"]["reason"],
            AUTHORIZED_IDLE_REASON,
        )
        self.assertIsNotNone(frame0["proposed"])
        self.assertNotEqual(frame0["proposed"]["steering"], 0.0)

    def test_apply_default_writes_nothing_record_writes_html(self) -> None:
        self._stage()
        with tempfile.TemporaryDirectory() as out:
            out_root = Path(out)
            before = list(out_root.iterdir()) if out_root.exists() else []
            default = apply_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                from_run=ACTIVE_RUN,
                json_output=True,
                record=False,
                output_root=out_root,
            )
            self.assertEqual(default.exit_code, 0, default.message)
            self.assertEqual(list(out_root.iterdir()) if out_root.exists() else [], before)

            recorded = apply_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                from_run=ACTIVE_RUN,
                json_output=True,
                record=True,
                output_root=out_root,
            )
            self.assertEqual(recorded.exit_code, 0, recorded.message)
            payload = json.loads(recorded.message)
            self.assertTrue(payload["recorded"])
            record_dir = Path(payload["record_dir"])
            # display_path may be relative; resolve via out_root children
            dirs = [p for p in out_root.iterdir() if p.is_dir()]
            self.assertEqual(len(dirs), 1)
            record_dir = dirs[0]
            self.assertTrue((record_dir / "manifest.json").exists())
            self.assertTrue((record_dir / "digest.json").exists())
            self.assertTrue((record_dir / "result.json").exists())
            html_files = list((record_dir / "frames").glob("*.html"))
            self.assertEqual(len(html_files), 1)
            html_text = html_files[0].read_text(encoding="utf-8")
            self.assertIn('id="source_refs"', html_text)
            self.assertIn("source_refs", html_text)
            self.assertIn("proposed_applied=false", html_text)
            self.assertIn("memory_record", html_text)
            self.assertIn(
                "contribution_plugins=avoid_recent_obstruction",
                html_text,
            )

            before_idle = set(out_root.iterdir())
            idle_recorded = apply_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                from_run=NO_MEM_RUN,
                json_output=True,
                record=True,
                output_root=out_root,
            )
            self.assertEqual(idle_recorded.exit_code, 0, idle_recorded.message)
            idle_dir = next(iter(set(out_root.iterdir()) - before_idle))
            idle_html = next((idle_dir / "frames").glob("*.html")).read_text(
                encoding="utf-8"
            )
            self.assertIn("status=idle", idle_html)
            self.assertIn("contribution_plugins=(none)", idle_html)

    def test_apply_record_source_image_paths_and_symlink_rejection(self) -> None:
        self._stage()
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out:
            run_dir = Path(tmp) / "run"
            shutil.copytree(ACTIVE_RUN, run_dir)
            source_frames = run_dir / "frames"
            source_frames.mkdir()
            source_image = source_frames / "frame_001.png"
            source_image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")

            recorded = apply_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                from_run=run_dir,
                json_output=True,
                record=True,
                output_root=Path(out),
            )
            self.assertEqual(recorded.exit_code, 0, recorded.message)
            record_dir = next(Path(out).iterdir())
            manifest = json.loads((record_dir / "manifest.json").read_text())
            self.assertEqual(
                manifest["frames"][0]["source_image"],
                "source_frames/frame_001.png",
            )
            html_path = record_dir / manifest["frames"][0]["html"]
            html_text = html_path.read_text(encoding="utf-8")
            match = re.search(r'<img src="([^"]+)"', html_text)
            self.assertIsNotNone(match)
            self.assertEqual(match.group(1), "../source_frames/frame_001.png")
            self.assertTrue((html_path.parent / match.group(1)).resolve().is_file())

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out:
            run_dir = Path(tmp) / "run"
            shutil.copytree(ACTIVE_RUN, run_dir)
            source_frames = run_dir / "frames"
            source_frames.mkdir()
            sibling = source_frames / "sibling.png"
            sibling.write_bytes(b"fixture")
            (source_frames / "frame_001.png").symlink_to(sibling.name)
            rejected = apply_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                from_run=run_dir,
                json_output=True,
                record=True,
                output_root=Path(out),
            )
            self.assertEqual(rejected.exit_code, 2)
            self.assertEqual(json.loads(rejected.message)["error"], "run_invalid")
            self.assertEqual(list(Path(out).iterdir()), [])

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out:
            temp_root = Path(tmp)
            real_run = temp_root / "real-run"
            shutil.copytree(ACTIVE_RUN, real_run)
            source_frames = real_run / "frames"
            source_frames.mkdir()
            (source_frames / "frame_001.png").write_bytes(b"fixture")
            linked_run = temp_root / "linked-run"
            linked_run.symlink_to(real_run, target_is_directory=True)
            rejected_root = apply_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                from_run=linked_run,
                json_output=True,
                record=True,
                output_root=Path(out),
            )
            self.assertEqual(rejected_root.exit_code, 2)
            self.assertEqual(
                json.loads(rejected_root.message)["error"],
                "run_invalid",
            )
            self.assertEqual(list(Path(out).iterdir()), [])

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out:
            temp_root = Path(tmp)
            real_parent = temp_root / "real-parent"
            real_run = real_parent / "run"
            shutil.copytree(ACTIVE_RUN, real_run)
            source_frames = real_run / "frames"
            source_frames.mkdir()
            (source_frames / "frame_001.png").write_bytes(b"fixture")
            linked_parent = temp_root / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            nested_under_link = linked_parent / "run"
            self.assertFalse(nested_under_link.is_symlink())
            rejected_ancestor = apply_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                from_run=nested_under_link,
                json_output=True,
                record=True,
                output_root=Path(out),
            )
            self.assertEqual(rejected_ancestor.exit_code, 2)
            self.assertEqual(
                json.loads(rejected_ancestor.message)["error"],
                "run_invalid",
            )
            self.assertEqual(list(Path(out).iterdir()), [])

    def test_apply_cli_json(self) -> None:
        self._stage()
        missing_id = run_automa(
            "vehicles",
            "decision",
            "apply",
            "--from-run",
            str(ACTIVE_RUN),
            "--json",
            runtime_root=self.runtime_root,
            check=False,
        )
        self.assertEqual(missing_id.returncode, 2)
        self.assertEqual(json.loads(missing_id.stdout)["error"], "missing_vehicle_id")
        self.assertEqual(missing_id.stderr, "")

        result = run_automa(
            "vehicles",
            "decision",
            "apply",
            "--id",
            "chase-sim-chaser",
            "--from-run",
            str(ACTIVE_RUN),
            "--json",
            runtime_root=self.runtime_root,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "vehicle_decision_apply_result_v0")
        self.assertEqual(payload["engine_id"], ENGINE_ID)

    def test_apply_record_root_setup_failure_uses_stable_cli_error(self) -> None:
        self._stage()
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "record-root-is-file"
            output_root.write_text("not a directory", encoding="utf-8")
            result = run_automa(
                "vehicles",
                "decision",
                "apply",
                "--id",
                "chase-sim-chaser",
                "--from-run",
                str(ACTIVE_RUN),
                "--record",
                "--json",
                runtime_root=self.runtime_root,
                extra_env={
                    "AUTOMA_DECISION_APPLY_OUTPUT_ROOT": str(output_root),
                },
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stderr, "")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema"], "vehicle_decision_error_v0")
            self.assertEqual(payload["error"], "record_write_failed")

    def test_apply_record_name_collisions_preserve_preexisting_paths(self) -> None:
        self._stage()
        with tempfile.TemporaryDirectory() as out:
            out_root = Path(out)
            final_name = "chase-sim-chaser-STAMP-abc123"
            partial_name = f".{final_name}.partial"
            for collision_name in (final_name, partial_name):
                with self.subTest(collision_name=collision_name):
                    collision = out_root / collision_name
                    collision.mkdir()
                    sentinel = collision / "sentinel.txt"
                    sentinel.write_text("keep me", encoding="utf-8")
                    with (
                        patch(
                            "cli.automa_cli.decision.time.strftime",
                            return_value="STAMP",
                        ),
                        patch(
                            "cli.automa_cli.decision.secrets.token_hex",
                            return_value="abc123",
                        ),
                    ):
                        result = apply_vehicle_decision(
                            vehicle_id="chase-sim-chaser",
                            from_run=ACTIVE_RUN,
                            json_output=True,
                            record=True,
                            output_root=out_root,
                        )
                    self.assertEqual(result.exit_code, 2)
                    self.assertEqual(
                        json.loads(result.message)["error"],
                        "record_write_failed",
                    )
                    self.assertTrue(collision.is_dir())
                    self.assertEqual(
                        sentinel.read_text(encoding="utf-8"),
                        "keep me",
                    )
                    shutil.rmtree(collision)

    def test_apply_no_memory_frame(self) -> None:
        self._stage()
        result = apply_vehicle_decision(
            vehicle_id="chase-sim-chaser",
            from_run=NO_MEM_RUN,
            json_output=True,
        )
        self.assertEqual(result.exit_code, 0, result.message)
        payload = json.loads(result.message)
        frame0 = payload["digest"]["frames"][0]
        self.assertFalse(frame0["proposed_applied"])
        self.assertEqual(frame0["authorized_output"]["reason"], AUTHORIZED_IDLE_REASON)

    def test_apply_duplicate_frame_id_and_vehicle_mismatch(self) -> None:
        self._stage()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            seq = json.loads((ACTIVE_RUN / "sequence.json").read_text())
            seq["frames"] = [seq["frames"][0], dict(seq["frames"][0])]
            (run_dir / "sequence.json").write_text(json.dumps(seq), encoding="utf-8")
            dup = apply_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                from_run=run_dir,
                json_output=True,
            )
            self.assertEqual(dup.exit_code, 2)
            self.assertEqual(json.loads(dup.message)["error"], "run_invalid")

            seq2 = json.loads((ACTIVE_RUN / "sequence.json").read_text())
            seq2["vehicle_id"] = "other-vehicle"
            (run_dir / "sequence.json").write_text(json.dumps(seq2), encoding="utf-8")
            mismatch = apply_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                from_run=run_dir,
                json_output=True,
            )
            self.assertEqual(mismatch.exit_code, 2)
            self.assertEqual(json.loads(mismatch.message)["error"], "run_invalid")

    def test_apply_bounds_max_frames(self) -> None:
        self._stage()
        with patch.object(self._decision_mod, "DECISION_APPLY_MAX_FRAMES", 1):
            result = apply_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                from_run=TWO_FRAME_RUN,
                json_output=True,
            )
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(json.loads(result.message)["error"], "run_bounds_exceeded")

    def test_apply_record_oversize_cleans_partial(self) -> None:
        self._stage()
        with tempfile.TemporaryDirectory() as out:
            out_root = Path(out)
            with patch.object(self._decision_mod, "DECISION_APPLY_MAX_RECORD_BYTES", 50):
                result = apply_vehicle_decision(
                    vehicle_id="chase-sim-chaser",
                    from_run=ACTIVE_RUN,
                    json_output=True,
                    record=True,
                    output_root=out_root,
                )
            self.assertEqual(result.exit_code, 2)
            self.assertEqual(json.loads(result.message)["error"], "record_bounds_exceeded")
            # no leftover partial or final dirs
            leftovers = list(out_root.iterdir()) if out_root.exists() else []
            self.assertEqual(leftovers, [])

    def test_apply_record_result_failure_occurs_before_final_rename(self) -> None:
        self._stage()
        original_write_text = Path.write_text
        observed_final_dirs: list[Path] = []

        def fail_result(path: Path, data: str, *args, **kwargs):
            if path.name == "result.json":
                observed_final_dirs.extend(
                    item
                    for item in path.parent.parent.iterdir()
                    if item.is_dir() and not item.name.endswith(".partial")
                )
                raise OSError("injected result write failure")
            return original_write_text(path, data, *args, **kwargs)

        with tempfile.TemporaryDirectory() as out:
            out_root = Path(out)
            with patch.object(Path, "write_text", new=fail_result):
                result = apply_vehicle_decision(
                    vehicle_id="chase-sim-chaser",
                    from_run=ACTIVE_RUN,
                    json_output=True,
                    record=True,
                    output_root=out_root,
                )
            self.assertEqual(result.exit_code, 2)
            self.assertEqual(json.loads(result.message)["error"], "record_write_failed")
            self.assertEqual(observed_final_dirs, [])
            self.assertEqual(list(out_root.iterdir()), [])

    def test_apply_record_cleanup_failure_preserves_both_errors(self) -> None:
        self._stage()
        with tempfile.TemporaryDirectory() as out:
            out_root = Path(out)
            with (
                patch.object(
                    self._decision_mod,
                    "DECISION_APPLY_MAX_RECORD_BYTES",
                    50,
                ),
                patch(
                    "cli.automa_cli.decision._remove_tree_strict",
                    side_effect=OSError("injected cleanup failure"),
                ),
            ):
                result = apply_vehicle_decision(
                    vehicle_id="chase-sim-chaser",
                    from_run=ACTIVE_RUN,
                    json_output=True,
                    record=True,
                    output_root=out_root,
                )
            self.assertEqual(result.exit_code, 2)
            payload = json.loads(result.message)
            self.assertEqual(payload["error"], "record_bounds_exceeded")
            self.assertIn("Record artifacts are", payload["details"]["original_error"])
            self.assertTrue(payload["details"]["cleanup_errors"])

    def test_apply_record_measurement_failure_cleans_partial(self) -> None:
        self._stage()
        real_lstat = Path.lstat

        def flaky_lstat(path: Path, *args, **kwargs):
            if path.name == "result.json":
                raise OSError("injected measurement failure")
            return real_lstat(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as out:
            out_root = Path(out)
            with patch.object(Path, "lstat", new=flaky_lstat):
                result = apply_vehicle_decision(
                    vehicle_id="chase-sim-chaser",
                    from_run=ACTIVE_RUN,
                    json_output=True,
                    record=True,
                    output_root=out_root,
                )
            self.assertEqual(result.exit_code, 2)
            payload = json.loads(result.message)
            self.assertEqual(payload["error"], "record_write_failed")
            self.assertIn(
                "could not measure record artifact",
                payload["details"]["original_error"],
            )
            self.assertEqual(list(out_root.iterdir()), [])

    # --- strict decode regressions ------------------------------------

    def test_strict_decode_rejects_malformations(self) -> None:
        from cli.automa_cli.decision import DecisionSurfaceError

        with self.assertRaises(DecisionSurfaceError) as ctx:
            strict_decode_apply_observation({"observation_id": "only"})
        self.assertEqual(ctx.exception.error, "run_invalid")

        good_obs = json.loads((ACTIVE_RUN / "sequence.json").read_text())["frames"][0][
            "observation"
        ]
        coerced = dict(good_obs)
        coerced["created_at_ms"] = "123"
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_observation(coerced)

        artifacts_bad = dict(good_obs)
        artifacts_bad["artifacts"] = {"k": 7}
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_observation(artifacts_bad)

        things_bad = dict(good_obs)
        things_bad["things"] = ["not-a-dict"]
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_observation(things_bad)

        extra = dict(good_obs)
        extra["extra_key"] = True
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_observation(extra)

        good_mem = json.loads((ACTIVE_RUN / "sequence.json").read_text())["frames"][0][
            "memory"
        ]
        missing_created = dict(good_mem)
        del missing_created["created_at_ms"]
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_memory(missing_created)

        bounds_incomplete = dict(good_mem)
        bounds_incomplete["bounds"] = {
            k: v
            for k, v in good_mem["bounds"].items()
            if k != "max_serialized_bytes"
        }
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_memory(bounds_incomplete)

        bounds_str = dict(good_mem)
        bounds_str["bounds"] = dict(good_mem["bounds"])
        bounds_str["bounds"]["max_records"] = "2"
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_memory(bounds_str)

        conf_str = dict(good_mem)
        conf_str["records"] = [dict(good_mem["records"][0])]
        conf_str["records"][0] = dict(conf_str["records"][0])
        conf_str["records"][0]["confidence"] = "0.8"
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_memory(conf_str)

        non_dict_record = dict(good_mem)
        non_dict_record["records"] = ["nope"]
        non_dict_record["record_count"] = 1
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_memory(non_dict_record)

        bad_location = dict(good_mem)
        bad_location["records"] = [dict(good_mem["records"][0])]
        bad_location["records"][0] = dict(bad_location["records"][0])
        bad_location["records"][0]["location"] = "left"
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_memory(bad_location)

        count_mismatch = dict(good_mem)
        count_mismatch["record_count"] = 99
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_memory(count_mismatch)

        # complete export accepted
        strict_decode_apply_observation(good_obs)
        strict_decode_apply_memory(good_mem)

    def test_canonical_json_utf8_not_length_only(self) -> None:
        a = {"a": 1, "b": 2}
        b = {"a": 2, "b": 1}
        # same length different content is possible
        self.assertEqual(canonical_json_bytes(a), canonical_json_bytes(b))
        self.assertNotEqual(canonical_json_utf8(a), canonical_json_utf8(b))

    def test_cli_update_shadow_engine_choice(self) -> None:
        result = run_automa(
            "vehicles",
            "update",
            "decision",
            "--id",
            "chase-sim-chaser",
            "--engine",
            "shadow-proposals",
            "--json",
            runtime_root=self.runtime_root,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["manifest"]["decision"]["engine_spec"], ADAPTER_ENGINE_SPEC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
