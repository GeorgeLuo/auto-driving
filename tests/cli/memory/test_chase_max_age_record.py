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
    def test_record_fails_closed_when_expiry_image_missing(self) -> None:
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
            "pid": 4242,
            "run_id": "automation-run-1",
            "capacity_eviction_count": 0,
        }

        def _live_frame(index: int, records: list[dict], **kwargs) -> dict:
            return _chase_frame(
                index,
                records,
                memory_epoch_id=worker["epoch"],
                run_id=worker["run_id"],
                worker_pid=worker["pid"],
                capacity_eviction_count=worker["capacity_eviction_count"],
                **kwargs,
            )

        collection = [
            (9, [], {}),
            (
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
                {"timestamp_ms": old},
            ),
            (
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
                {"timestamp_ms": old + 100},
            ),
        ]
        cursor = {"n": 0}
        wait_polls = {"n": 0}

        def load_latest() -> dict:
            if cursor["n"] < len(collection):
                index, records, kwargs = collection[cursor["n"]]
                cursor["n"] += 1
                return _live_frame(index, records, **kwargs)
            wait_polls["n"] += 1
            if wait_polls["n"] <= 2:
                return _live_frame(
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
                )
            return _live_frame(20, [], timestamp_ms=now)

        def load_frame_image(frame_id: str) -> bytes:
            if frame_id == "chase_frame_000020":
                raise ConnectionError("image endpoint unavailable")
            return b"\x89PNG\r\n\x1a\nexact-frame"

        def probe() -> dict:
            return _live_probe(
                reset_count=worker["reset_count"],
                epoch=worker["epoch"],
                pid=worker["pid"],
                run_id=worker["run_id"],
                capacity_eviction_count=worker["capacity_eviction_count"],
            )

        def reset() -> dict:
            worker["reset_count"] += 1
            worker["epoch"] = f"memory-epoch-{worker['reset_count']}"
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

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
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
                output_root=Path(temporary.name) / "memory-check",
            )
        self.assertEqual(result.exit_code, 1, result.message)
        payload = json.loads(result.message)
        self.assertFalse(payload["passed"])
        self.assertIn("image", payload["error"].lower())
