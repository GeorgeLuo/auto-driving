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
            probe_fn=lambda: _live_probe(reset_count=1, epoch="memory-epoch-0"),
            present_keys={"thing:obstacle_000"},
            max_age_ms=1000,
            timeout_s=1.0,
            key_anchors_ms={"thing:obstacle_000": old},
            identity=_identity(),
            max_records=32,
        )
        self.assertFalse(result.passed)
        self.assertIn("control", result.reason)

    def test_missing_observe_only_fields_fail(self) -> None:
        now = int(time.time() * 1000)
        old = now - 10_000
        frame = _chase_frame(
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
            omit_observe_only=True,
        )
        result = wait_for_chase_memory_key_expiry(
            load_latest_frame=lambda: frame,
            probe_fn=lambda: _live_probe(reset_count=1, epoch="memory-epoch-0"),
            present_keys={"thing:obstacle_000"},
            max_age_ms=1000,
            timeout_s=1.0,
            key_anchors_ms={"thing:obstacle_000": old},
            identity=_identity(),
            max_records=32,
        )
        self.assertFalse(result.passed)
        self.assertIn("action_policy", result.reason)

    def test_simulation_epoch_change_fails(self) -> None:
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
        )
        expired = _chase_frame(
            20,
            [],
            timestamp_ms=now,
            simulation_epoch="chase-run:restarted",
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
            identity=_identity(),
            max_records=32,
        )
        self.assertFalse(result.passed)
        self.assertIn("simulation_epoch", result.reason)

    def test_missing_worker_pid_during_wait_fails(self) -> None:
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
        )

        def probe() -> dict:
            payload = _live_probe(reset_count=1, epoch="memory-epoch-0")
            del payload["worker_pid"]
            return payload

        result = wait_for_chase_memory_key_expiry(
            load_latest_frame=lambda: present,
            probe_fn=probe,
            present_keys={"thing:obstacle_000"},
            max_age_ms=1000,
            timeout_s=1.0,
            key_anchors_ms={"thing:obstacle_000": old},
            identity=_identity(),
            max_records=32,
        )
        self.assertFalse(result.passed)
        self.assertIn("worker_pid", result.reason)

    def test_capacity_replacement_fails(self) -> None:
        now = int(time.time() * 1000)
        old = now - 10_000
        present = _chase_frame(
            12,
            [
                {
                    "record_id": "thing:obstacle_000",
                    "updated_at_ms": old,
                    "provenance": {
                        "frame_id": "chase_frame_000010",
                        "updated_at_ms": old,
                    },
                }
            ],
            timestamp_ms=now,
        )
        # Full one-record ledger replaces tracked key with new evidence.
        replaced = _chase_frame(
            13,
            [
                {
                    "record_id": "thing:new_obstacle",
                    "updated_at_ms": now,
                    "provenance": {
                        "frame_id": "chase_frame_000013",
                        "updated_at_ms": now,
                    },
                }
            ],
            timestamp_ms=now,
        )
        polls = {"n": 0}

        def load_latest() -> dict:
            polls["n"] += 1
            return present if polls["n"] == 1 else replaced

        result = wait_for_chase_memory_key_expiry(
            load_latest_frame=load_latest,
            probe_fn=lambda: _live_probe(
                reset_count=1, epoch="memory-epoch-0", max_records=1
            ),
            present_keys={"thing:obstacle_000"},
            max_age_ms=1000,
            timeout_s=2.0,
            key_anchors_ms={"thing:obstacle_000": old},
            identity=_identity(),
            max_records=1,
        )
        self.assertFalse(result.passed)
        # Full ledger while key present fails headroom; replacement also capacity-ambiguous.
        self.assertTrue(
            "headroom" in result.reason.lower() or "capacity" in result.reason.lower()
        )

    def test_malformed_memory_does_not_pass_immediately(self) -> None:
        bad = _chase_frame(12, [])
        del bad["memory"]
        with self.assertRaises(TimeoutError):
            wait_for_chase_memory_key_expiry(
                load_latest_frame=lambda: bad,
                probe_fn=lambda: _live_probe(reset_count=1, epoch="memory-epoch-0"),
                present_keys={"thing:obstacle_000"},
                max_age_ms=1000,
                timeout_s=0.6,
                key_anchors_ms={"thing:obstacle_000": 1},
                identity=_identity(),
                max_records=32,
            )
