from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cli.automa_cli.chase_max_age import (
    extract_chase_lifecycle_keys,
    frame_control_is_strict_zero,
    parse_required_max_age_ms,
    score_chase_max_age_expiry,
    wait_for_chase_memory_key_expiry,
)
from cli.automa_cli.memory_check import run_vehicle_memory_check


def _chase_frame(
    index: int,
    records: list[dict],
    *,
    timestamp_ms: int | None = None,
    control: dict | None = None,
) -> dict:
    return {
        "frame_id": f"chase_frame_{index:06d}",
        "frame_index": index,
        "simulator_frame_index": index,
        "timestamp_ms": timestamp_ms if timestamp_ms is not None else 1_000 + index,
        "simulation_epoch": "chase-run:test",
        "control_source": "simulator",
        "control_application": "not_applied",
        "action_policy": "observe_only",
        "control": control
        if control is not None
        else {
            "applied": False,
            "reason": "idle",
            "steering": 0.0,
            "throttle": 0.0,
        },
        "shadow_reference": {
            "schema": "chase_shadow_reference_v1",
            "evaluator_only": True,
            "simulator_frame_index": index,
            "simulation_epoch": "chase-run:test",
            "game_id": "chase",
            "scenario": "chaser-depth-obstacles",
            "chaser_control_source": "programmatic",
        },
        "observation": {
            "observation_id": f"obs-{index}",
            "things": [{"thing_id": "front_camera_frame"}],
            "signals": [],
            "sensor_snapshot": {
                "metadata": {
                    "simulator_frame_index": index,
                    "simulation_epoch": "chase-run:test",
                }
            },
        },
        "memory": {
            "health": "healthy" if records else "empty",
            "record_count": len(records),
            "records": records,
        },
    }


class ChaseMaxAgeUnitTests(unittest.TestCase):
    def test_lifecycle_keys_exclude_always_on_and_require_retained_prior(self) -> None:
        frames = [
            _chase_frame(
                10,
                [
                    {
                        "record_id": "thing:front_camera_frame",
                        "provenance": {
                            "frame_id": "chase_frame_000010",
                            "updated_at_ms": 100,
                        },
                    },
                    {
                        "record_id": "thing:obstacle_000",
                        "provenance": {
                            "frame_id": "chase_frame_000010",
                            "updated_at_ms": 100,
                        },
                    },
                ],
            ),
            _chase_frame(
                11,
                [
                    {
                        "record_id": "thing:obstacle_000",
                        "provenance": {
                            "frame_id": "chase_frame_000010",
                            "updated_at_ms": 100,
                        },
                    },
                    {
                        "record_id": "thing:front_camera_frame",
                        "provenance": {
                            "frame_id": "chase_frame_000011",
                            "updated_at_ms": 200,
                        },
                    },
                    {
                        "record_id": "thing:traversable_floor",
                        "provenance": {
                            "frame_id": "chase_frame_000010",
                            "updated_at_ms": 100,
                        },
                    },
                ],
            ),
        ]
        self.assertEqual(extract_chase_lifecycle_keys(frames), {"thing:obstacle_000"})

    def test_parse_required_max_age_rejects_missing(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing"):
            parse_required_max_age_ms(None)
        with self.assertRaisesRegex(ValueError, "missing"):
            parse_required_max_age_ms({})
        with self.assertRaisesRegex(ValueError, "positive"):
            parse_required_max_age_ms({"max_age_ms": 0})
        self.assertEqual(parse_required_max_age_ms({"max_age_ms": 2500}), 2500)

    def test_control_missing_is_not_zero(self) -> None:
        ok, reason = frame_control_is_strict_zero({"control": {"applied": False}})
        self.assertFalse(ok)
        self.assertIn("steering", str(reason))
        ok2, _ = frame_control_is_strict_zero(
            {"control": {"applied": False, "steering": 0.0, "throttle": 0.0}}
        )
        self.assertTrue(ok2)

    def test_score_requires_age_and_advancement(self) -> None:
        base = dict(
            lifecycle_keys={"thing:obstacle_000"},
            final_memory={"health": "empty", "records": []},
            control_ok=True,
            reset_used=False,
            max_age_ms=1000,
            age_elapsed_ms=1000,
            reset_count_stable=True,
            epoch_stable=True,
            frames_advanced=True,
            capacity_eviction_ambiguous=False,
        )
        self.assertTrue(score_chase_max_age_expiry(**base)["passed"])
        too_young = score_chase_max_age_expiry(**{**base, "age_elapsed_ms": 10})
        self.assertFalse(too_young["passed"])
        self.assertIn("before max-age", too_young["reason"])
        no_advance = score_chase_max_age_expiry(**{**base, "frames_advanced": False})
        self.assertFalse(no_advance["passed"])
        reset_used = score_chase_max_age_expiry(**{**base, "reset_used": True})
        self.assertFalse(reset_used["passed"])


class ChaseMaxAgeIntegrationTests(unittest.TestCase):
    def test_chase_shadow_path_includes_max_age_expiry_and_record(self) -> None:
        vehicle = {
            "vehicle_id": "chase-sim-chaser",
            "provider": "chase-sim",
            "connection": {"ws_url": "ws://chase.test/ws"},
        }
        now = int(time.time() * 1000)
        old = now - 5_000
        frames = [
            _chase_frame(9, []),
            _chase_frame(
                10,
                [
                    {
                        "record_id": "thing:front_camera_frame",
                        "provenance": {
                            "frame_id": "chase_frame_000010",
                            "updated_at_ms": old,
                        },
                    }
                ],
                timestamp_ms=old,
            ),
            _chase_frame(
                11,
                [
                    {
                        "record_id": "thing:obstacle_000",
                        "provenance": {
                            "frame_id": "chase_frame_000010",
                            "updated_at_ms": old,
                        },
                    },
                    {
                        "record_id": "thing:front_camera_frame",
                        "provenance": {
                            "frame_id": "chase_frame_000011",
                            "updated_at_ms": old + 100,
                        },
                    },
                ],
                timestamp_ms=old + 100,
            ),
        ]
        present_wait = _chase_frame(
            12,
            [
                {
                    "record_id": "thing:obstacle_000",
                    "provenance": {
                        "frame_id": "chase_frame_000010",
                        "updated_at_ms": old,
                    },
                },
                {
                    "record_id": "thing:front_camera_frame",
                    "provenance": {
                        "frame_id": "chase_frame_000012",
                        "updated_at_ms": now,
                    },
                },
            ],
            timestamp_ms=now,
        )
        expired = _chase_frame(
            20,
            [
                {
                    "record_id": "thing:front_camera_frame",
                    "provenance": {
                        "frame_id": "chase_frame_000020",
                        "updated_at_ms": now,
                    },
                }
            ],
            timestamp_ms=now,
        )
        cursor = {"n": 0}
        wait_polls = {"n": 0}

        def load_latest() -> dict:
            # Collection phase walks boundary + post-boundary frames first.
            if cursor["n"] < len(frames):
                frame = frames[cursor["n"]]
                cursor["n"] += 1
                return frame
            # Max-age wait: first prove keys still present, then advance and drop.
            wait_polls["n"] += 1
            if wait_polls["n"] == 1:
                return present_wait
            return expired

        # After history_boundary reset, keep reset_count/epoch stable through max-age wait.
        worker = {"reset_count": 0, "epoch": "memory-epoch-0", "count": 1, "health": "healthy"}

        def probe() -> dict:
            return {
                "status": "live",
                "last_health": worker["health"],
                "last_record_count": worker["count"],
                "last_epoch_id": worker["epoch"],
                "reset_count": worker["reset_count"],
                "bounds": {"max_age_ms": 1000, "max_records": 32},
                "implementation_id": "bounded_evidence",
                "activation": "runtime/memory/active.json",
            }

        def reset() -> dict:
            worker["reset_count"] += 1
            worker["epoch"] = f"memory-epoch-{worker['reset_count']}"
            worker["count"] = 0
            worker["health"] = "empty"
            return {
                "ok": True,
                "status": "reset",
                "snapshot": {
                    "health": "empty",
                    "record_count": 0,
                    "records": [],
                    "epoch_id": worker["epoch"],
                },
            }

        recorded_images: list[str] = []

        def load_frame_image(frame_id: str) -> bytes:
            recorded_images.append(frame_id)
            return b"\x89PNG\r\n\x1a\nexact-frame"

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        output_root = Path(temporary.name) / "memory-check"
        with mock.patch(
            "cli.automa_cli.memory_check.discover_active_vehicles",
            return_value={"vehicles": [vehicle]},
        ), mock.patch(
            "cli.automa_cli.memory_check.find_vehicle_by_id",
            return_value=(vehicle, None),
        ):
            result = run_vehicle_memory_check(
                vehicle_id="chase-sim-chaser",
                record=True,
                json_output=True,
                load_latest_frame=load_latest,
                load_frame_image=load_frame_image,
                probe_fn=probe,
                reset_fn=reset,
                fresh_timeout_s=1.0,
                expiry_timeout_s=2.0,
                output_root=output_root,
            )
        self.assertEqual(result.exit_code, 0, result.message)
        payload = json.loads(result.message)
        self.assertTrue(payload["passed"])
        phases = {item["phase"] for item in payload["phase_results"]}
        self.assertIn("max_age_expiry", phases)
        expiry = next(
            item for item in payload["phase_results"] if item["phase"] == "max_age_expiry"
        )
        self.assertTrue(expiry["passed"])
        self.assertEqual(expiry["score"]["lifecycle_keys"], ["thing:obstacle_000"])
        self.assertFalse(expiry["score"].get("reset_used", False))
        self.assertGreaterEqual(int(expiry["score"]["age_elapsed_ms"]), 1000)
        self.assertTrue(expiry["score"]["frames_advanced"])
        record_dir = next(output_root.iterdir())
        sequence = json.loads((record_dir / "sequence.json").read_text(encoding="utf-8"))
        frame_ids = [frame["frame_id"] for frame in sequence["frames"]]
        self.assertIn("chase_frame_000020", frame_ids)
        manifest = json.loads((record_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(any("max-age expiry without reset" in note for note in manifest["notes"]))
        self.assertIn("chase_frame_000020", recorded_images)

    def test_missing_max_age_bounds_fail_closed(self) -> None:
        vehicle = {
            "vehicle_id": "chase-sim-chaser",
            "provider": "chase-sim",
            "connection": {"ws_url": "ws://chase.test/ws"},
        }
        frames = [
            _chase_frame(9, []),
            _chase_frame(
                10,
                [
                    {
                        "record_id": "thing:obstacle_000",
                        "provenance": {"frame_id": "chase_frame_000010", "updated_at_ms": 1},
                    }
                ],
            ),
            _chase_frame(
                11,
                [
                    {
                        "record_id": "thing:obstacle_000",
                        "provenance": {"frame_id": "chase_frame_000010", "updated_at_ms": 1},
                    }
                ],
            ),
        ]
        cursor = {"n": 0}

        def load_latest() -> dict:
            idx = min(cursor["n"], len(frames) - 1)
            cursor["n"] += 1
            return frames[idx]

        def probe() -> dict:
            return {
                "status": "live",
                "last_health": "healthy",
                "last_record_count": 1,
                "last_epoch_id": "e1",
                "reset_count": 1,
                "implementation_id": "bounded_evidence",
            }

        def reset() -> dict:
            return {
                "ok": True,
                "status": "reset",
                "snapshot": {
                    "health": "empty",
                    "record_count": 0,
                    "records": [],
                    "epoch_id": "e2",
                },
            }

        with mock.patch(
            "cli.automa_cli.memory_check.discover_active_vehicles",
            return_value={"vehicles": [vehicle]},
        ), mock.patch(
            "cli.automa_cli.memory_check.find_vehicle_by_id",
            return_value=(vehicle, None),
        ):
            result = run_vehicle_memory_check(
                vehicle_id="chase-sim-chaser",
                json_output=True,
                load_latest_frame=load_latest,
                probe_fn=probe,
                reset_fn=reset,
                fresh_timeout_s=1.0,
            )
        self.assertEqual(result.exit_code, 1, result.message)
        payload = json.loads(result.message)
        self.assertFalse(payload["passed"])
        expiry = next(
            item for item in payload["phase_results"] if item["phase"] == "max_age_expiry"
        )
        self.assertIn("max_age_ms", expiry["score"]["reason"])

    def test_nonzero_control_during_wait_fails(self) -> None:
        now = int(time.time() * 1000)
        old = now - 10_000
        present = _chase_frame(
            12,
            [
                {
                    "record_id": "thing:obstacle_000",
                    "provenance": {
                        "frame_id": "chase_frame_000010",
                        "updated_at_ms": old,
                    },
                }
            ],
            timestamp_ms=now,
            control={"applied": False, "steering": 0.5, "throttle": 0.0},
        )
        result = wait_for_chase_memory_key_expiry(
            load_latest_frame=lambda: present,
            probe_fn=lambda: {
                "status": "live",
                "reset_count": 1,
                "last_epoch_id": "e1",
            },
            present_keys={"thing:obstacle_000"},
            max_age_ms=1000,
            timeout_s=1.0,
            key_anchors_ms={"thing:obstacle_000": old},
            baseline_reset_count=1,
            baseline_epoch_id="e1",
        )
        self.assertFalse(result.passed)
        self.assertIn("control", result.reason)

    def test_malformed_memory_does_not_pass_immediately(self) -> None:
        bad = _chase_frame(12, [])
        del bad["memory"]
        with self.assertRaises(TimeoutError):
            wait_for_chase_memory_key_expiry(
                load_latest_frame=lambda: bad,
                probe_fn=lambda: {
                    "status": "live",
                    "reset_count": 1,
                    "last_epoch_id": "e1",
                },
                present_keys={"thing:obstacle_000"},
                max_age_ms=1000,
                timeout_s=0.6,
                key_anchors_ms={"thing:obstacle_000": 1},
                baseline_reset_count=1,
                baseline_epoch_id="e1",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
