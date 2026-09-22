from __future__ import annotations
import json
import time
import unittest
from unittest import mock
from cli.automa_cli.memory_check import run_vehicle_memory_check
from tests.cli.memory.chase_max_age_fixtures import (
    _chase_frame,
    _live_probe,
)


class ChaseMaxAgeIntegrationTests(unittest.TestCase):
    def test_chase_max_age_expiry_timeout_is_fail_closed(self) -> None:
        vehicle = {
            "vehicle_id": "chase-sim-chaser",
            "provider": "chase-sim",
            "connection": {"ws_url": "ws://chase.test/ws"},
        }
        now = int(time.time() * 1000)
        old = now - 5_000
        # Same retained-prior setup as the happy path, but wait never drops the key.
        collection = [
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
        cursor = {"n": 0}
        wait_n = {"n": 0}

        def load_latest() -> dict:
            if cursor["n"] < len(collection):
                frame = collection[cursor["n"]]
                cursor["n"] += 1
                return frame
            # Persistent lifecycle key: advance frames but never drop obstacle.
            wait_n["n"] += 1
            index = 11 + wait_n["n"]
            return _chase_frame(
                index,
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
                            "frame_id": f"chase_frame_{index:06d}",
                            "updated_at_ms": now,
                        },
                    },
                ],
                timestamp_ms=now,
            )

        def probe() -> dict:
            return _live_probe(reset_count=1, epoch="memory-epoch-1")

        def reset() -> dict:
            return {
                "ok": True,
                "status": "reset",
                "snapshot": {
                    "health": "empty",
                    "record_count": 0,
                    "records": [],
                    "epoch_id": "memory-epoch-2",
                },
            }

        # Collection + wait frames must share the probed memory epoch.
        def load_latest_with_epoch() -> dict:
            frame = load_latest()
            memory = dict(frame.get("memory") or {})
            memory["epoch_id"] = "memory-epoch-1"
            frame = dict(frame)
            frame["memory"] = memory
            return frame

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
                load_latest_frame=load_latest_with_epoch,
                probe_fn=probe,
                reset_fn=reset,
                fresh_timeout_s=1.0,
                expiry_timeout_s=0.6,
            )
        self.assertEqual(result.exit_code, 2, result.message)
        payload = json.loads(result.message)
        self.assertFalse(payload["passed"])
        self.assertIn("max_age_expiry", payload["phases"])
        self.assertIn("did not drop", payload["error"].lower())
