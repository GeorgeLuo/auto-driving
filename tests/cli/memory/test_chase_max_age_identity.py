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
    def test_stale_frame_memory_epoch_does_not_count_as_present(self) -> None:
        """Pre-reset frame must not supply keys-present for a current probe."""

        now = int(time.time() * 1000)
        old = now - 10_000
        stale_present = _chase_frame(
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
            memory_epoch_id="pre-reset-epoch",
        )
        current_empty = _chase_frame(
            20,
            [],
            timestamp_ms=now,
            memory_epoch_id="memory-epoch-0",
        )
        polls = {"n": 0}

        def load_latest() -> dict:
            polls["n"] += 1
            return stale_present if polls["n"] == 1 else current_empty

        result = wait_for_chase_memory_key_expiry(
            load_latest_frame=load_latest,
            probe_fn=lambda: _live_probe(reset_count=1, epoch="memory-epoch-0"),
            present_keys={"thing:obstacle_000"},
            max_age_ms=1000,
            timeout_s=1.0,
            key_anchors_ms={"thing:obstacle_000": old},
            identity=_identity(),
            max_records=32,
        )
        self.assertFalse(result.passed)
        self.assertIn("epoch_id", result.reason)

    def test_restarted_worker_reusing_epoch_string_fails_on_run_id(self) -> None:
        """Same epoch-1 after restart must not pass without matching run_id."""

        now = int(time.time() * 1000)
        old = now - 10_000
        # Old worker frame: same epoch string, different run_id/pid, key present.
        old_present = _chase_frame(
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
            memory_epoch_id="epoch-1",
            run_id="automation-run-old",
            worker_pid=111,
        )
        new_empty = _chase_frame(
            20,
            [],
            timestamp_ms=now,
            memory_epoch_id="epoch-1",
            run_id="automation-run-new",
            worker_pid=222,
        )
        polls = {"n": 0}

        def load_latest() -> dict:
            polls["n"] += 1
            return old_present if polls["n"] == 1 else new_empty

        result = wait_for_chase_memory_key_expiry(
            load_latest_frame=load_latest,
            probe_fn=lambda: _live_probe(
                reset_count=1,
                epoch="epoch-1",
                pid=222,
                run_id="automation-run-new",
            ),
            present_keys={"thing:obstacle_000"},
            max_age_ms=1000,
            timeout_s=1.0,
            key_anchors_ms={"thing:obstacle_000": old},
            identity=_identity(
                worker_pid=222,
                run_id="automation-run-new",
                memory_epoch_id="epoch-1",
            ),
            max_records=32,
        )
        self.assertFalse(result.passed)
        self.assertTrue(
            "run_id" in result.reason or "worker_pid" in result.reason,
            result.reason,
        )
