from __future__ import annotations
import unittest
from unittest import mock


class MemoryCheckTests(unittest.TestCase):
    def test_chase_shadow_path_scores_live_alignment(self) -> None:
        # Full Chase max-age path lives in tests/cli/memory/test_chase_max_age.py.
        # Keep provenance scoring smoke here without cross-TestCase invocation.
        from cli.automa_cli.memory_check import score_chase_memory_provenance

        frames = [
            {
                "frame_id": "chase_frame_000010",
                "simulator_frame_index": 10,
                "simulation_epoch": "chase-run:test",
                "memory": {
                    "records": [
                        {
                            "record_id": "thing:obstacle_000",
                            "provenance": {"frame_id": "chase_frame_000010"},
                        }
                    ]
                },
                "observation": {
                    "things": [{"thing_id": "obstacle_000"}],
                    "signals": [],
                    "sensor_snapshot": {
                        "metadata": {
                            "simulator_frame_index": 10,
                            "simulation_epoch": "chase-run:test",
                        }
                    },
                },
            },
            {
                "frame_id": "chase_frame_000011",
                "simulator_frame_index": 11,
                "simulation_epoch": "chase-run:test",
                "memory": {
                    "records": [
                        {
                            "record_id": "thing:obstacle_000",
                            "provenance": {"frame_id": "chase_frame_000010"},
                        }
                    ]
                },
                "observation": {
                    "things": [{"thing_id": "obstacle_000"}],
                    "signals": [],
                    "sensor_snapshot": {
                        "metadata": {
                            "simulator_frame_index": 11,
                            "simulation_epoch": "chase-run:test",
                        }
                    },
                },
            },
        ]
        score = score_chase_memory_provenance(frames)
        self.assertIsInstance(score, dict)
        self.assertIn("passed", score)

    def test_chase_check_internal_probe_bypasses_vehicle_discovery(self) -> None:
        from cli.automa_cli.memory_check import run_chase_shadow_memory_check

        with mock.patch(
            "cli.automa_cli.memory_check.probe_live_memory",
            return_value={"status": "unavailable", "error": "test stop"},
        ) as probe:
            result = run_chase_shadow_memory_check(
                vehicle_id="chase-sim-chaser",
                load_latest_frame=lambda: None,
                reset_fn=lambda: {"ok": False, "error": "fixture worker unavailable"},
            )

        self.assertEqual(result.exit_code, 2)
        self.assertGreaterEqual(probe.call_count, 1)
        for call in probe.call_args_list:
            self.assertEqual(
                call,
                mock.call(
                    vehicle_id="chase-sim-chaser",
                    vehicle={"provider": "chase-sim"},
                    timeout_s=3.0,
                ),
            )

    def test_chase_observe_only_rejects_external_ws_authority(self) -> None:
        from cli.automa_cli.memory_check import score_chase_observe_only

        frame = {
            "frame_id": "chase_frame_000001",
            "control_source": "external_ws",
            "action_policy": "engine_idle",
            "control_application": "stop_only_safety_gate",
            "control": {"applied": False, "steering": 0.0, "throttle": 0.0},
            "shadow_reference": {"chaser_control_source": "ws"},
        }
        score = score_chase_observe_only([frame])
        self.assertFalse(score["passed"])
        self.assertTrue(any("external_ws" in v for v in score["violations"]))
        self.assertTrue(
            any("shadow.chaser_control_source=ws" in v for v in score["violations"])
        )

    def test_chase_observe_only_requires_explicit_complete_zero_control(self) -> None:
        from cli.automa_cli.memory_check import (
            derive_chase_safety_from_frames,
            score_chase_observe_only,
        )

        frame = {
            "frame_id": "chase_frame_000001",
            "control_source": "simulator",
            "action_policy": "observe_only",
            "control_application": "not_applied",
            "control": {"applied": False, "steering": 0.0},
            "shadow_reference": {"chaser_control_source": "programmatic"},
        }
        score = score_chase_observe_only([frame])
        self.assertFalse(score["passed"])
        self.assertIn(
            "chase_frame_000001:control.throttle=missing",
            score["violations"],
        )

        safety = derive_chase_safety_from_frames(
            [frame],
            observe_score=score,
            alignment_score={"passed": True},
        )
        self.assertIsNone(safety["movement_commands_sent"])
        self.assertFalse(safety["control_evidence_complete"])

        moving = {
            **frame,
            "control": {"applied": False, "steering": 0.2, "throttle": 0.0},
        }
        moving_score = score_chase_observe_only([moving])
        moving_safety = derive_chase_safety_from_frames(
            [moving],
            observe_score=moving_score,
            alignment_score={"passed": True},
        )
        self.assertTrue(moving_safety["movement_commands_sent"])

    def test_chase_provenance_is_ordered_per_memory_snapshot(self) -> None:
        from cli.automa_cli.memory_check import score_chase_memory_provenance

        frames = [
            {
                "frame_id": "chase_frame_000010",
                "simulator_frame_index": 10,
                "memory": {
                    "records": [
                        {
                            "record_id": "thing:current",
                            "provenance": {"frame_id": "chase_frame_000010"},
                        }
                    ]
                },
            },
            {
                "frame_id": "chase_frame_000011",
                "simulator_frame_index": 11,
                "memory": {
                    "records": [
                        {
                            "record_id": "thing:retained",
                            "provenance": {"frame_id": "chase_frame_000010"},
                        }
                    ]
                },
            },
        ]
        score = score_chase_memory_provenance(frames)
        self.assertTrue(score["passed"], score)
        self.assertEqual(score["current_frame_matches"], 1)
        self.assertEqual(score["retained_prior_matches"], 1)

        future = [
            {
                **frames[0],
                "memory": {
                    "records": [
                        {
                            "record_id": "thing:future",
                            "provenance": {"frame_id": "chase_frame_000011"},
                        }
                    ]
                },
            },
            frames[1],
        ]
        future_score = score_chase_memory_provenance(future)
        self.assertFalse(future_score["passed"])
        self.assertTrue(future_score["future_provenance"])

        pre_boundary = [
            {
                **frames[0],
                "memory": {
                    "records": [
                        {
                            "record_id": "thing:stale",
                            "provenance": {"frame_id": "chase_frame_000009"},
                        }
                    ]
                },
            },
            frames[1],
        ]
        stale_score = score_chase_memory_provenance(pre_boundary)
        self.assertFalse(stale_score["passed"])
        self.assertTrue(stale_score["mismatched"])

    def test_chase_collection_discards_unobserved_warmup_lineage(self) -> None:
        from cli.automa_cli.memory_check import collect_chase_automation_frames

        def frame(index: int, source_index: int) -> dict:
            return {
                "frame_id": f"chase_frame_{index:06d}",
                "simulator_frame_index": index,
                "memory": {
                    "health": "healthy",
                    "records": [
                        {
                            "record_id": "thing:boundary",
                            "provenance": {
                                "frame_id": f"chase_frame_{source_index:06d}"
                            },
                        }
                    ],
                },
            }

        publications = [
            frame(10, 9),
            frame(11, 11),
            frame(12, 11),
        ]
        cursor = {"value": 0}

        def load_latest() -> dict:
            index = min(cursor["value"], len(publications) - 1)
            cursor["value"] += 1
            return publications[index]

        collected = collect_chase_automation_frames(
            load_latest_frame=load_latest,
            min_frames=2,
            timeout_s=1.0,
            after_simulator_frame_index=8,
        )

        self.assertEqual(
            [item["frame_id"] for item in collected],
            ["chase_frame_000011", "chase_frame_000012"],
        )

    def test_chase_provenance_rejects_empty_memory(self) -> None:
        from cli.automa_cli.memory_check import score_chase_memory_provenance

        frames = [
            {
                "frame_id": "chase_frame_000001",
                "simulator_frame_index": 1,
                "memory": {"health": "empty", "records": []},
            },
            {
                "frame_id": "chase_frame_000002",
                "simulator_frame_index": 2,
                "memory": {"health": "empty", "records": []},
            },
        ]
        score = score_chase_memory_provenance(frames)
        self.assertFalse(score["passed"])
        self.assertIn("empty", score["reason"])
