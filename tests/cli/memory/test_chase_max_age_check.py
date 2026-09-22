from __future__ import annotations
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from cli.automa_cli.memory_check import run_vehicle_memory_check
from tests.cli.memory.chase_max_age_fixtures import (
    _chase_frame,
    _live_probe,
)


class ChaseMaxAgeIntegrationTests(unittest.TestCase):
    def test_chase_shadow_path_includes_max_age_expiry_and_record(self) -> None:
        vehicle = {
            "vehicle_id": "chase-sim-chaser",
            "provider": "chase-sim",
            "connection": {"ws_url": "ws://chase.test/ws"},
        }
        now = int(time.time() * 1000)
        old = now - 5_000
        worker = {
            "reset_count": 0,
            "epoch": "memory-epoch-0",
            "count": 1,
            "health": "healthy",
            "pid": 4242,
            "run_id": "automation-run-1",
            "capacity_eviction_count": 0,
        }

        def _frame_with_current_epoch(
            index: int, records: list[dict], **kwargs
        ) -> dict:
            # After history_boundary reset the live worker epoch advances; frames
            # must publish that generation so probe/frame correlation holds.
            return _chase_frame(
                index,
                records,
                memory_epoch_id=worker["epoch"],
                run_id=worker["run_id"],
                worker_pid=worker["pid"],
                capacity_eviction_count=worker["capacity_eviction_count"],
                **kwargs,
            )

        frames = [
            _frame_with_current_epoch(9, []),
            _frame_with_current_epoch(
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
            _frame_with_current_epoch(
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
        cursor = {"n": 0}
        wait_polls = {"n": 0}

        def load_latest() -> dict:
            if cursor["n"] < len(frames):
                # Rebuild so collection frames pick up post-reset worker epoch.
                template = frames[cursor["n"]]
                cursor["n"] += 1
                records = list((template.get("memory") or {}).get("records") or [])
                return _frame_with_current_epoch(
                    int(template["simulator_frame_index"]),
                    records,
                    timestamp_ms=int(template.get("timestamp_ms") or now),
                )
            # Extra present samples cover baseline identity load + first wait poll.
            wait_polls["n"] += 1
            if wait_polls["n"] <= 2:
                return _frame_with_current_epoch(
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
            return _frame_with_current_epoch(
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

        def probe() -> dict:
            return _live_probe(
                reset_count=worker["reset_count"],
                epoch=worker["epoch"],
                pid=worker["pid"],
                run_id=worker["run_id"],
                count=worker["count"],
                capacity_eviction_count=worker["capacity_eviction_count"],
            )

        def reset() -> dict:
            worker["reset_count"] += 1
            worker["epoch"] = f"memory-epoch-{worker['reset_count']}"
            worker["count"] = 0
            worker["health"] = "empty"
            worker["capacity_eviction_count"] = 0
            return {
                "ok": True,
                "status": "reset",
                "snapshot": {
                    "health": "empty",
                    "record_count": 0,
                    "records": [],
                    "epoch_id": worker["epoch"],
                    "metadata": {
                        "capacity_eviction_count": worker["capacity_eviction_count"],
                    },
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
            item
            for item in payload["phase_results"]
            if item["phase"] == "max_age_expiry"
        )
        self.assertTrue(expiry["passed"])
        self.assertEqual(expiry["score"]["lifecycle_keys"], ["thing:obstacle_000"])
        self.assertFalse(expiry["score"].get("reset_used", False))
        self.assertGreaterEqual(int(expiry["score"]["age_elapsed_ms"]), 1000)
        self.assertTrue(expiry["score"]["frames_advanced"])
        self.assertTrue(expiry["score"]["identity_stable"])
        record_dir = next(output_root.iterdir())
        sequence = json.loads(
            (record_dir / "sequence.json").read_text(encoding="utf-8")
        )
        frame_ids = [frame["frame_id"] for frame in sequence["frames"]]
        self.assertIn("chase_frame_000020", frame_ids)
        manifest = json.loads(
            (record_dir / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertTrue(
            any("max-age expiry without reset" in note for note in manifest["notes"])
        )
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
                        "provenance": {
                            "frame_id": "chase_frame_000010",
                            "updated_at_ms": 1,
                        },
                    }
                ],
            ),
            _chase_frame(
                11,
                [
                    {
                        "record_id": "thing:obstacle_000",
                        "provenance": {
                            "frame_id": "chase_frame_000010",
                            "updated_at_ms": 1,
                        },
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
            return _live_probe(include_bounds=False, reset_count=1, epoch="e1")

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
            item
            for item in payload["phase_results"]
            if item["phase"] == "max_age_expiry"
        )
        self.assertIn("max_age_ms", expiry["score"]["reason"])
