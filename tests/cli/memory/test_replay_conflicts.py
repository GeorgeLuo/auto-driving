from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from cli.automa_cli.memory import replay_vehicle_memory
from tests.cli.memory.replay_fixtures import (
    CONFLICT_SOURCE,
    MemoryReplayFixture,
)


class MemoryReplayTests(MemoryReplayFixture, unittest.TestCase):
    def test_replay_conflict_sequence_asserts_policy_and_counters(self) -> None:
        """Offline update-frame sequence for bounded_evidence conflict policy.

        Replay only exposes a final snapshot, so intermediate invalidation and
        re-admission are asserted by replaying successive prefixes of the same
        fixture (no schema widening).
        """

        full = json.loads(CONFLICT_SOURCE.read_text(encoding="utf-8"))
        frames = full["frames"]
        self.assertEqual(len(frames), 4)

        # Expected end-state after each prefix length (1-based frame count).
        expected_by_prefix = {
            1: {
                "record_count": 1,
                "kind": "floor_boundary",
                "conflict_count": 0,
                "last_update_conflict_count": 0,
            },
            2: {
                "record_count": 0,
                "kind": None,
                "conflict_count": 1,
                "last_update_conflict_count": 1,
            },
            3: {
                "record_count": 1,
                "kind": "obstacle",
                "conflict_count": 1,
                "last_update_conflict_count": 0,
            },
            4: {
                "record_count": 1,
                "kind": "obstacle",
                "conflict_count": 1,
                "last_update_conflict_count": 0,
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            for prefix_len, expected in expected_by_prefix.items():
                with self.subTest(prefix_len=prefix_len):
                    prefix_path = Path(tmp) / f"conflict_prefix_{prefix_len}.json"
                    prefix_path.write_text(
                        json.dumps(
                            {
                                "schema": full["schema"],
                                "description": f"prefix {prefix_len} of conflict_sequence",
                                "frames": frames[:prefix_len],
                            }
                        ),
                        encoding="utf-8",
                    )
                    result = replay_vehicle_memory(
                        vehicle_id="chase-sim-chaser",
                        sequence=prefix_path,
                        implementation_id="bounded_evidence",
                        json_output=True,
                        verify_twice=True,
                    )
                    self.assertEqual(result.exit_code, 0, result.message)
                    payload = json.loads(result.message)
                    self.assertEqual(payload["frame_count"], prefix_len)
                    final = payload["final"]
                    metadata = final.get("metadata") or {}
                    self.assertEqual(
                        metadata.get("conflict_policy"),
                        "bounded_evidence_structural_v1",
                    )
                    self.assertEqual(final["record_count"], expected["record_count"])
                    self.assertEqual(
                        metadata.get("conflict_count"), expected["conflict_count"]
                    )
                    self.assertEqual(
                        metadata.get("last_update_conflict_count"),
                        expected["last_update_conflict_count"],
                    )
                    if expected["kind"] is None:
                        self.assertEqual(final["records"], [])
                    else:
                        self.assertEqual(final["records"][0]["kind"], expected["kind"])
                        record_ids = {item["record_id"] for item in final["records"]}
                        self.assertTrue(
                            any(i.endswith(":6:slot_a") for i in record_ids)
                        )
