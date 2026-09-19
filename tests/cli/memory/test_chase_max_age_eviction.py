from __future__ import annotations
import time
import unittest
from cli.automa_cli.chase_max_age import wait_for_chase_memory_key_expiry
from tests.cli.memory.chase_max_age_fixtures import (
    _chase_frame,
    _identity,
    _live_probe,
)


class ChaseMaxAgeIntegrationTests(unittest.TestCase):
    def test_capacity_eviction_counter_increase_fails(self) -> None:
        """Unsampled full-ledger eviction is still visible via frame metadata."""

        now = int(time.time() * 1000)
        old = now - 10_000
        present = _chase_frame(
            20,
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
            capacity_eviction_count=0,
        )
        # Intermediate capacity eviction occurred (counter advanced on the
        # published snapshot) even though the sampled final frame has headroom
        # and no tracked key. Counter is read from frame metadata only.
        expired = _chase_frame(
            22,
            [
                {
                    "record_id": "thing:front_camera_frame",
                    "provenance": {
                        "frame_id": "chase_frame_000022",
                        "updated_at_ms": now,
                    },
                }
            ],
            timestamp_ms=now,
            capacity_eviction_count=1,
        )
        polls = {"n": 0}

        def load_latest() -> dict:
            polls["n"] += 1
            return present if polls["n"] == 1 else expired

        result = wait_for_chase_memory_key_expiry(
            load_latest_frame=load_latest,
            probe_fn=lambda: _live_probe(reset_count=1, epoch="memory-epoch-0"),
            present_keys={"thing:obstacle_000"},
            max_age_ms=1000,
            timeout_s=2.0,
            key_anchors_ms={"thing:obstacle_000": old},
            identity=_identity(capacity_eviction_count=0),
            max_records=32,
        )
        self.assertFalse(result.passed)
        self.assertIn("capacity eviction", result.reason.lower())

    def test_full_ledger_while_key_present_fails_headroom(self) -> None:
        now = int(time.time() * 1000)
        old = now - 10_000
        full = _chase_frame(
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
                    "record_id": "thing:other_000",
                    "provenance": {
                        "frame_id": "chase_frame_000012",
                        "updated_at_ms": now,
                    },
                },
            ],
            timestamp_ms=now,
        )
        result = wait_for_chase_memory_key_expiry(
            load_latest_frame=lambda: full,
            probe_fn=lambda: _live_probe(
                reset_count=1, epoch="memory-epoch-0", max_records=2
            ),
            present_keys={"thing:obstacle_000"},
            max_age_ms=1000,
            timeout_s=1.0,
            key_anchors_ms={"thing:obstacle_000": old},
            identity=_identity(),
            max_records=2,
        )
        self.assertFalse(result.passed)
        self.assertIn("headroom", result.reason.lower())
        self.assertFalse(result.score.get("headroom_proven", True))
