from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from cli.automa_cli.memory_check import (
    currently_refreshed_memory_keys,
    observation_evidence_keys,
    run_vehicle_memory_check,
    score_live_reset,
)
from tests.cli.memory.check_fixtures import (
    _live_publication,
    _memory_record,
)


class MemoryCheckTests(unittest.TestCase):
    def test_physical_pi_path_scores_live_onboard_memory(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.test:8887"},
        }
        present_pub = _live_publication(
            frame_id="present_frame", frame_index=0, with_boundary=True
        )
        dropout_pub = _live_publication(
            frame_id="dropout_frame", frame_index=1, with_boundary=False
        )
        # Expiry: always-on keys remain; only dropped boundary evidence expires.
        expired_pub = _live_publication(
            frame_id="expired_frame",
            frame_index=2,
            with_boundary=False,
            memory_records=[
                _memory_record(
                    "thing:front_camera_frame",
                    frame_id="expired_frame",
                    kind="camera_frame",
                ),
                _memory_record(
                    "thing:traversable_floor", frame_id="expired_frame", kind="floor"
                ),
                _memory_record(
                    "signal:floor_visible", frame_id="expired_frame", kind="signal"
                ),
                _memory_record(
                    "signal:front_camera_available",
                    frame_id="expired_frame",
                    kind="signal",
                ),
            ],
            memory_health="healthy",
        )
        pair_calls = {"n": 0}
        pair_pubs = [present_pub, dropout_pub]
        poll_pubs = [expired_pub]

        def fake_matched_pair(_url: str, **kwargs) -> dict:
            after = kwargs.get("after_frame_id")
            while pair_calls["n"] < len(pair_pubs):
                pub = pair_pubs[pair_calls["n"]]
                pair_calls["n"] += 1
                frame_id = pub["frame"]["frame_id"]
                if after is not None and frame_id == after:
                    continue
                return {
                    "publication": pub,
                    "frame_bytes": b"\xff\xd8jpeg-bytes\xff\xd9",
                    "frame_headers": {"x-frame-id": frame_id},
                    "frame_id": frame_id,
                    "matched": True,
                    "attempts": 1,
                    "image_required": True,
                }
            raise TimeoutError("no newer matched pair")

        def fake_pub(_url: str) -> dict:
            return poll_pubs[0]

        def fake_reset() -> dict:
            return {
                "ok": True,
                "status": "reset",
                "snapshot": {
                    "health": "empty",
                    "record_count": 0,
                    "records": [],
                    "epoch_id": "epoch-2",
                },
            }

        def fake_probe() -> dict:
            # First probe before reset; second after — cycle may already
            # repopulate always-on evidence in the new epoch.
            if not hasattr(fake_probe, "n"):
                fake_probe.n = 0  # type: ignore[attr-defined]
            fake_probe.n += 1  # type: ignore[attr-defined]
            if fake_probe.n == 1:  # type: ignore[attr-defined]
                return {
                    "status": "live",
                    "last_health": "healthy",
                    "last_record_count": 5,
                    "last_epoch_id": "epoch-1",
                    "reset_count": 1,
                    "implementation_id": "bounded_evidence",
                }
            return {
                "status": "live",
                "last_health": "healthy",
                "last_record_count": 4,
                "last_epoch_id": "epoch-2",
                "reset_count": 2,
                "implementation_id": "bounded_evidence",
            }

        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "memory-check"
            with mock.patch(
                "cli.automa_cli.memory_check.discover_active_vehicles",
                return_value={"vehicles": [vehicle]},
            ), mock.patch(
                "cli.automa_cli.memory_check.find_vehicle_by_id",
                return_value=(vehicle, None),
            ):
                result = run_vehicle_memory_check(
                    vehicle_id="piracer",
                    record=True,
                    json_output=True,
                    auto=True,
                    fetch_matched_pair=fake_matched_pair,
                    fetch_publication=fake_pub,
                    reset_fn=fake_reset,
                    probe_fn=fake_probe,
                    expiry_timeout_s=1.0,
                    output_root=output_root,
                )
            self.assertEqual(result.exit_code, 0, result.message)
            payload = json.loads(result.message)
            self.assertTrue(payload["passed"])
            self.assertEqual(payload["provider"], "picar")
            self.assertEqual(
                payload["safety"]["lifecycle_source"], "live_onboard_stage"
            )
            self.assertFalse(payload["safety"]["forced_dropout"])
            self.assertFalse(payload["safety"]["ephemeral_local_reducer"])
            sources = {item["lifecycle_source"] for item in payload["phase_results"]}
            self.assertIn("live_onboard_publication.memory", sources)
            self.assertIn("live_onboard_reset+probe", sources)
            present = next(
                item for item in payload["phase_results"] if item["phase"] == "present"
            )
            self.assertTrue(present["live_control_zero"])
            self.assertIn("present_frame", present["live_frame_ids"])
            dropout = next(
                item for item in payload["phase_results"] if item["phase"] == "dropout"
            )
            self.assertEqual(
                dropout["score"]["lifecycle_keys"],
                ["thing:floor_boundary_000"],
            )
            expiry = next(
                item for item in payload["phase_results"] if item["phase"] == "expiry"
            )
            self.assertEqual(
                expiry["score"]["lifecycle_keys"], ["thing:floor_boundary_000"]
            )
            self.assertNotIn("thing:floor_boundary_000", expiry["record_ids"])
            self.assertIn("thing:front_camera_frame", expiry["record_ids"])
            self.assertTrue(payload["recorded"])
            run_dir = next(output_root.iterdir())
            self.assertTrue((run_dir / "frames").is_dir())
            extract = (run_dir / "provenance_extract.html").read_text(encoding="utf-8")
            self.assertIn("present_frame", extract)
            self.assertIn('<img src="frames/present_frame.jpg"', extract)
            self.assertIn('<img src="frames/dropout_frame.jpg"', extract)

    def test_wait_for_fresh_publication_fails_closed_on_stale_frame(self) -> None:
        from cli.automa_cli.memory_check import _wait_for_fresh_publication

        stale = _live_publication(frame_id="same", frame_index=0, with_boundary=True)
        with self.assertRaises(TimeoutError) as ctx:
            _wait_for_fresh_publication(
                base_url="http://piracer.test:8887",
                get_publication=lambda _url: stale,
                previous_frame_id="same",
                timeout_s=0.4,
            )
        self.assertIn("same", str(ctx.exception))

    def test_score_live_reset_uses_snapshot_empty_not_probe(self) -> None:
        """Empty-state comes from reset snapshot; probe may already be repopulated."""
        reset_snapshot = {
            "health": "empty",
            "record_count": 0,
            "records": [],
            "epoch_id": "epoch-2",
        }
        # Always-on cycle repopulated memory before the post-reset probe.
        after_probe = {
            "status": "live",
            "last_health": "healthy",
            "last_record_count": 4,
            "last_epoch_id": "epoch-2",
            "reset_count": 2,
        }
        score = score_live_reset(
            reset_snapshot=reset_snapshot,
            prior_epoch="epoch-1",
            prior_reset_count=1,
            after_probe=after_probe,
        )
        self.assertTrue(score["passed"], score.get("reason"))
        self.assertEqual(score["epoch_id"], "epoch-2")
        self.assertEqual(score["post_reset_probe_record_count"], 4)
        self.assertEqual(score["post_reset_probe_health"], "healthy")

    def test_score_live_reset_accepts_reset_count_only_transition(self) -> None:
        """OR contract: reset_count bump alone is enough when epoch is stable."""
        reset_snapshot = {
            "health": "empty",
            "record_count": 0,
            "records": [],
            "epoch_id": "epoch-1",
        }
        after_probe = {
            "last_health": "healthy",
            "last_record_count": 3,
            "last_epoch_id": "epoch-1",
            "reset_count": 5,
        }
        score = score_live_reset(
            reset_snapshot=reset_snapshot,
            prior_epoch="epoch-1",
            prior_reset_count=4,
            after_probe=after_probe,
        )
        self.assertTrue(score["passed"], score.get("reason"))

    def test_score_live_reset_rejects_nonempty_snapshot(self) -> None:
        score = score_live_reset(
            reset_snapshot={
                "health": "healthy",
                "record_count": 2,
                "records": [{"record_id": "thing:front_camera_frame"}],
                "epoch_id": "epoch-2",
            },
            prior_epoch="epoch-1",
            prior_reset_count=1,
            after_probe={"last_epoch_id": "epoch-2", "reset_count": 2},
        )
        self.assertFalse(score["passed"])
        self.assertIn("not empty", score["reason"])

    def test_observation_evidence_keys_skips_explicit_false_signals(self) -> None:
        pub = {
            "observation": {
                "things": [{"thing_id": "traversable_floor", "confidence": 0.9}],
                "signals": [
                    {"signal_id": "floor_visible", "value": True, "confidence": 0.9},
                    {
                        "signal_id": "floor_boundary_available",
                        "value": False,
                        "confidence": 0.9,
                    },
                ],
            }
        }
        keys = observation_evidence_keys(pub)
        self.assertIn("thing:traversable_floor", keys)
        self.assertIn("signal:floor_visible", keys)
        self.assertNotIn("signal:floor_boundary_available", keys)

    def test_true_to_false_signal_produces_lifecycle_key(self) -> None:
        """true→false signal drop must appear as disappeared evidence."""
        present = {
            "frame": {"frame_id": "f0"},
            "observation": {
                "things": [],
                "signals": [
                    {
                        "signal_id": "floor_boundary_available",
                        "value": True,
                        "confidence": 0.9,
                    },
                    {"signal_id": "floor_visible", "value": True, "confidence": 0.9},
                ],
            },
            "memory": {
                "health": "healthy",
                "records": [
                    _memory_record(
                        "signal:floor_boundary_available", frame_id="f0", kind="signal"
                    ),
                    _memory_record(
                        "signal:floor_visible", frame_id="f0", kind="signal"
                    ),
                ],
            },
        }
        # Ledger skips False: no matching-frame signal record for boundary.
        dropout = {
            "frame": {"frame_id": "f1"},
            "observation": {
                "things": [],
                "signals": [
                    {
                        "signal_id": "floor_boundary_available",
                        "value": False,
                        "confidence": 0.9,
                    },
                    {"signal_id": "floor_visible", "value": True, "confidence": 0.9},
                ],
            },
            "memory": {
                "health": "healthy",
                "records": [
                    # Stale retention of prior frame is not currently refreshed.
                    _memory_record(
                        "signal:floor_boundary_available", frame_id="f0", kind="signal"
                    ),
                    _memory_record(
                        "signal:floor_visible", frame_id="f1", kind="signal"
                    ),
                ],
            },
        }
        present_keys = currently_refreshed_memory_keys(present)
        dropout_keys = currently_refreshed_memory_keys(dropout)
        lifecycle = present_keys - dropout_keys
        self.assertIn("signal:floor_boundary_available", lifecycle)
        self.assertNotIn("signal:floor_visible", lifecycle)

        # Fallback path (no memory records) must also skip False.
        present_obs_only = {
            "frame": {"frame_id": "f0"},
            "observation": present["observation"],
        }
        dropout_obs_only = {
            "frame": {"frame_id": "f1"},
            "observation": dropout["observation"],
        }
        obs_lifecycle = observation_evidence_keys(
            present_obs_only
        ) - observation_evidence_keys(dropout_obs_only)
        self.assertIn("signal:floor_boundary_available", obs_lifecycle)

    def test_current_memory_records_preserve_an_authoritative_empty_key_set(
        self,
    ) -> None:
        publication = {
            "frame": {"frame_id": "current"},
            "observation": {
                "things": [{"thing_id": "candidate", "confidence": 0.1}],
                "signals": [],
            },
            "memory": {
                "health": "healthy",
                "records": [_memory_record("thing:candidate", frame_id="older")],
            },
        }

        self.assertEqual(currently_refreshed_memory_keys(publication), set())

    def test_physical_pi_record_fails_when_pair_unavailable(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.test:8887"},
        }

        def fail_pair(_url: str, **_kwargs) -> dict:
            raise TimeoutError("Timed out waiting for a matched publication/JPEG pair")

        with mock.patch(
            "cli.automa_cli.memory_check.discover_active_vehicles",
            return_value={"vehicles": [vehicle]},
        ), mock.patch(
            "cli.automa_cli.memory_check.find_vehicle_by_id",
            return_value=(vehicle, None),
        ):
            result = run_vehicle_memory_check(
                vehicle_id="piracer",
                record=True,
                auto=True,
                json_output=True,
                fetch_matched_pair=fail_pair,
            )
        self.assertEqual(result.exit_code, 2)
        self.assertIn("matched", result.message.lower())

    def test_physical_pi_rejects_non_zero_control(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.test:8887"},
        }
        bad = _live_publication(
            frame_id="moving",
            frame_index=0,
            with_boundary=True,
            steering=0.2,
            throttle=0.0,
        )
        with mock.patch(
            "cli.automa_cli.memory_check.discover_active_vehicles",
            return_value={"vehicles": [vehicle]},
        ), mock.patch(
            "cli.automa_cli.memory_check.find_vehicle_by_id",
            return_value=(vehicle, None),
        ):
            result = run_vehicle_memory_check(
                vehicle_id="piracer",
                auto=True,
                json_output=True,
                fetch_publication=lambda _url: bad,
            )
        self.assertEqual(result.exit_code, 2)
        self.assertIn("non-zero", result.message)
