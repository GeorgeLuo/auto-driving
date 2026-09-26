from __future__ import annotations
import json
import unittest
from copy import deepcopy
from autonomy.decision.shadow_authority import AUTHORIZED_IDLE_REASON
from cli.automa_cli.decision import (
    ADAPTER_ENGINE_SPEC,
    DECISION_ENGINES,
    ENGINE_ID,
    accept_decision_stream_frame,
    build_decision_stream_frame,
    strict_decode_apply_memory,
    strict_decode_apply_observation,
)
from implementations.decision.catalog import create_shadow_proposals_engine
from tests.cli.decision.shadow_decision_surfaces_fixtures import (
    ACTIVE_RUN,
    ShadowDecisionSurfaceFixture,
)


class ShadowDecisionSurfaceTests(ShadowDecisionSurfaceFixture, unittest.TestCase):
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
        for key, value in (
            ("vehicle_id", None),
            ("run_id", None),
            ("frame_index", "x"),
        ):
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
