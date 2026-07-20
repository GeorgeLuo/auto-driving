from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli.automa_cli.memory_check import (
    build_default_memory_check_phases,
    currently_refreshed_memory_keys,
    observation_evidence_keys,
    publication_to_check_frame,
    run_vehicle_memory_check,
    score_live_reset,
    score_memory_check_phase,
)
from tests.support.cli_runner import run_automa


def _always_on_things_signals(*, with_boundary: bool) -> tuple[list[dict], list[dict]]:
    """Camera/floor evidence that remains present even after object removal."""
    things = [
        {
            "thing_id": "front_camera_frame",
            "kind": "camera_frame",
            "label": "camera",
            "confidence": 1.0,
            "location": {"frame": "image", "zone": "full"},
            "source_plugin_id": "lightweight_observer",
        },
        {
            "thing_id": "traversable_floor",
            "kind": "floor",
            "label": "floor",
            "confidence": 0.95,
            "location": {"frame": "image", "zone": "center"},
            "source_plugin_id": "lightweight_observer",
        },
    ]
    if with_boundary:
        things.append(
            {
                "thing_id": "floor_boundary_000",
                "kind": "floor_boundary",
                "label": "boundary",
                "confidence": 0.9,
                "location": {
                    "frame": "image",
                    "zone": "center",
                    "bbox_xyxy_norm": [0.3, 0.4, 0.7, 0.95],
                },
                "source_plugin_id": "floor-plane-v0",
            }
        )
    signals = [
        {"signal_id": "floor_visible", "value": True, "confidence": 0.95},
        {"signal_id": "front_camera_available", "value": True, "confidence": 1.0},
    ]
    return things, signals


def _memory_record(record_id: str, *, frame_id: str, kind: str = "floor_boundary") -> dict:
    return {
        "record_id": record_id,
        "kind": kind,
        "label": kind,
        "confidence": 0.9,
        "provenance": {
            "frame_id": frame_id,
            "observation_id": f"obs_{frame_id}",
            "evidence_id": record_id.split(":", 1)[-1],
        },
        "location": {"frame": "image", "zone": "center"},
    }


def _live_publication(
    *,
    frame_id: str,
    frame_index: int,
    with_boundary: bool,
    steering: float = 0.0,
    throttle: float = 0.0,
    memory_records: list[dict] | None = None,
    memory_health: str | None = None,
    epoch_id: str = "epoch-1",
    max_age_ms: int = 1_000,
) -> dict:
    things, signals = _always_on_things_signals(with_boundary=with_boundary)
    if memory_records is None:
        # Always-on keys stay in memory; boundary is the lifecycle target.
        memory_records = [
            _memory_record("thing:front_camera_frame", frame_id=frame_id, kind="camera_frame"),
            _memory_record("thing:traversable_floor", frame_id=frame_id, kind="floor"),
            _memory_record("signal:floor_visible", frame_id=frame_id, kind="signal"),
            _memory_record("signal:front_camera_available", frame_id=frame_id, kind="signal"),
        ]
        if with_boundary:
            memory_records.append(
                _memory_record("thing:floor_boundary_000", frame_id=frame_id)
            )
        else:
            # Dropout survival: boundary gone from observation but still retained in memory.
            memory_records.append(
                _memory_record("thing:floor_boundary_000", frame_id="present_frame")
            )
    if memory_health is None:
        memory_health = "healthy" if memory_records else "empty"
    return {
        "health": "healthy",
        "drive_mode": "user",
        "control": {"steering": steering, "throttle": throttle},
        "frame": {
            "frame_id": frame_id,
            "frame_index": frame_index,
            "captured_at_ms": 1_000 + frame_index * 100,
            "completed_at_ms": 1_010 + frame_index * 100,
            "has_image": True,
        },
        "perception": {
            "plugin_id": "lightweight_observer",
            "status": "ok",
            "things": things,
            "signals": signals,
            "lines": ["live test"],
        },
        "observation": {
            "observation_id": f"obs_{frame_id}",
            "created_at_ms": 1_000 + frame_index * 100,
            "sensor_snapshot": {},
            "perception_plugin_id": "lightweight_observer",
            "things": things,
            "signals": signals,
        },
        "memory": {
            "health": memory_health,
            "record_count": len(memory_records),
            "records": memory_records,
            "epoch_id": epoch_id,
            "implementation_id": "bounded_evidence",
            "bounds": {"max_records": 32, "max_age_ms": max_age_ms, "eviction_policy": "oldest_first"},
        },
    }


class MemoryCheckTests(unittest.TestCase):
    def test_check_help_is_registered(self) -> None:
        result = run_automa("vehicles", "memory", "help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("check", result.stdout)

    def test_default_phases_cover_lifecycle(self) -> None:
        phases = build_default_memory_check_phases()
        names = [phase["name"] for phase in phases]
        self.assertEqual(names, ["present", "dropout", "expiry", "reset"])

    def test_score_helpers(self) -> None:
        present = score_memory_check_phase(
            phase_name="present",
            final={
                "health": "healthy",
                "record_count": 1,
                "epoch_id": "epoch-1",
                "records": [{"record_id": "thing:floor_boundary_000"}],
            },
            present_keys=set(),
            prior_epoch=None,
        )
        self.assertTrue(present["passed"])
        dropout = score_memory_check_phase(
            phase_name="dropout",
            final={
                "health": "healthy",
                "record_count": 1,
                "records": [{"record_id": "thing:floor_boundary_000"}],
            },
            present_keys={"thing:floor_boundary_000"},
            prior_epoch="epoch-1",
        )
        self.assertTrue(dropout["passed"])
        expiry = score_memory_check_phase(
            phase_name="expiry",
            final={"health": "empty", "record_count": 0, "records": []},
            present_keys={"thing:floor_boundary_000"},
            prior_epoch="epoch-1",
        )
        self.assertTrue(expiry["passed"])
        reset = score_memory_check_phase(
            phase_name="reset",
            final={"health": "empty", "record_count": 0, "epoch_id": "epoch-2", "records": []},
            present_keys=set(),
            prior_epoch="epoch-1",
        )
        self.assertTrue(reset["passed"])

    def test_run_memory_check_passes_offline(self) -> None:
        result = run_vehicle_memory_check(
            vehicle_id="chase-sim-chaser",
            implementation_id="bounded_evidence",
            json_output=True,
            skip_discovery=True,
        )
        self.assertEqual(result.exit_code, 0, result.message)
        payload = json.loads(result.message)
        self.assertEqual(payload["schema"], "vehicle_memory_check_v0")
        self.assertTrue(payload["passed"])
        self.assertEqual(
            [item["phase"] for item in payload["phase_results"]],
            ["present", "dropout", "expiry", "reset"],
        )
        self.assertTrue(all(item["passed"] for item in payload["phase_results"]))
        self.assertFalse(payload["safety"]["movement_commands_sent"])
        self.assertGreaterEqual(len(payload["provenance_rows"]), 1)

    def test_run_memory_check_record_writes_extract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "memory-check"
            result = run_vehicle_memory_check(
                vehicle_id="chase-sim-chaser",
                implementation_id="bounded_evidence",
                record=True,
                json_output=True,
                skip_discovery=True,
                output_root=output_root,
            )
            self.assertEqual(result.exit_code, 0, result.message)
            payload = json.loads(result.message)
            self.assertTrue(payload["recorded"])
            run_dirs = list(output_root.iterdir())
            self.assertEqual(len(run_dirs), 1)
            record_dir = run_dirs[0]
            for name in (
                "manifest.json",
                "report.json",
                "sequence.json",
                "present_memory.json",
                "provenance_extract.html",
            ):
                self.assertTrue((record_dir / name).is_file(), name)
            persisted_report = json.loads(
                (record_dir / "report.json").read_text(encoding="utf-8")
            )
            self.assertTrue(persisted_report["recorded"])
            self.assertTrue(str(persisted_report["record_dir"]).endswith(record_dir.name))
            self.assertTrue(
                str(persisted_report["provenance_extract"]).endswith(
                    f"{record_dir.name}/provenance_extract.html"
                )
            )

    def test_cli_memory_check_json(self) -> None:
        result = run_automa(
            "vehicles",
            "memory",
            "check",
            "--id",
            "chase-sim-chaser",
            "--implementation",
            "bounded_evidence",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["passed"])

    def test_cli_memory_check_record_env_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "memory-check"
            env_key = "AUTOMA_MEMORY_CHECK_OUTPUT_ROOT"
            previous = os.environ.get(env_key)
            os.environ[env_key] = str(output_root)
            try:
                result = run_automa(
                    "vehicles",
                    "memory",
                    "check",
                    "--id",
                    "chase-sim-chaser",
                    "--implementation",
                    "bounded_evidence",
                    "--record",
                    "--json",
                )
            finally:
                if previous is None:
                    os.environ.pop(env_key, None)
                else:
                    os.environ[env_key] = previous
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["recorded"])
            self.assertTrue(output_root.exists())

    def test_publication_to_check_frame_force_empty(self) -> None:
        pub = _live_publication(frame_id="f1", frame_index=0, with_boundary=True)
        frame = publication_to_check_frame(pub, index=0, force_empty=True)
        self.assertEqual(frame["observation"]["things"], [])
        self.assertEqual(frame["frame_id"], "f1")

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
                _memory_record("thing:front_camera_frame", frame_id="expired_frame", kind="camera_frame"),
                _memory_record("thing:traversable_floor", frame_id="expired_frame", kind="floor"),
                _memory_record("signal:floor_visible", frame_id="expired_frame", kind="signal"),
                _memory_record(
                    "signal:front_camera_available", frame_id="expired_frame", kind="signal"
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
                    "frame_bytes": b"jpeg-bytes",
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
            return {"ok": True, "status": "reset", "snapshot": {
                "health": "empty",
                "record_count": 0,
                "records": [],
                "epoch_id": "epoch-2",
            }}

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
            self.assertEqual(payload["safety"]["lifecycle_source"], "live_onboard_stage")
            self.assertFalse(payload["safety"]["forced_dropout"])
            self.assertFalse(payload["safety"]["ephemeral_local_reducer"])
            sources = {item["lifecycle_source"] for item in payload["phase_results"]}
            self.assertIn("live_onboard_publication.memory", sources)
            self.assertIn("live_onboard_reset+probe", sources)
            present = next(item for item in payload["phase_results"] if item["phase"] == "present")
            self.assertTrue(present["live_control_zero"])
            self.assertIn("present_frame", present["live_frame_ids"])
            dropout = next(item for item in payload["phase_results"] if item["phase"] == "dropout")
            self.assertEqual(
                dropout["score"]["lifecycle_keys"],
                ["thing:floor_boundary_000"],
            )
            expiry = next(item for item in payload["phase_results"] if item["phase"] == "expiry")
            self.assertEqual(expiry["score"]["lifecycle_keys"], ["thing:floor_boundary_000"])
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
                    _memory_record("signal:floor_visible", frame_id="f0", kind="signal"),
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
                    _memory_record("signal:floor_visible", frame_id="f1", kind="signal"),
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
        obs_lifecycle = (
            observation_evidence_keys(present_obs_only)
            - observation_evidence_keys(dropout_obs_only)
        )
        self.assertIn("signal:floor_boundary_available", obs_lifecycle)

    def test_current_memory_records_preserve_an_authoritative_empty_key_set(self) -> None:
        publication = {
            "frame": {"frame_id": "current"},
            "observation": {
                "things": [{"thing_id": "candidate", "confidence": 0.1}],
                "signals": [],
            },
            "memory": {
                "health": "healthy",
                "records": [
                    _memory_record("thing:candidate", frame_id="older")
                ],
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

    def test_chase_shadow_path_scores_live_alignment(self) -> None:
        vehicle = {
            "vehicle_id": "chase-sim-chaser",
            "provider": "chase-sim",
            "connection": {"ws_url": "ws://chase.test/ws"},
        }
        frames = [
            {
                "frame_id": "chase_frame_000010",
                "frame_index": 10,
                "simulator_frame_index": 10,
                "control_source": "simulator",
                "control_application": "not_applied",
                "action_policy": "observe_only",
                "control": {
                    "applied": False,
                    "reason": "idle",
                    "steering": 0.0,
                    "throttle": 0.0,
                },
                "shadow_reference": {
                    "schema": "chase_shadow_reference_v0",
                    "evaluator_only": True,
                    "simulator_frame_index": 10,
                    "game_id": "chase",
                    "scenario": "chaser-depth-obstacles",
                    "chaser_control_source": "builtin",
                },
                "observation": {
                    "observation_id": "obs-10",
                    "things": [{"thing_id": "front_camera_frame"}],
                    "signals": [],
                    "sensor_snapshot": {"metadata": {"simulator_frame_index": 10}},
                },
                "memory": {
                    "health": "healthy",
                    "record_count": 1,
                    "records": [
                        {
                            "record_id": "thing:front_camera_frame",
                            "provenance": {"frame_id": "chase_frame_000010"},
                        }
                    ],
                },
            },
            {
                "frame_id": "chase_frame_000011",
                "frame_index": 11,
                "simulator_frame_index": 11,
                "control_source": "simulator",
                "control_application": "not_applied",
                "action_policy": "observe_only",
                "control": {
                    "applied": False,
                    "reason": "idle",
                    "steering": 0.0,
                    "throttle": 0.0,
                },
                "shadow_reference": {
                    "schema": "chase_shadow_reference_v0",
                    "evaluator_only": True,
                    "simulator_frame_index": 11,
                    "game_id": "chase",
                    "scenario": "chaser-depth-obstacles",
                    "chaser_control_source": "builtin",
                },
                "observation": {
                    "observation_id": "obs-11",
                    "things": [{"thing_id": "front_camera_frame"}],
                    "signals": [],
                    "sensor_snapshot": {"metadata": {"simulator_frame_index": 11}},
                },
                "memory": {
                    "health": "healthy",
                    "record_count": 2,
                    "records": [
                        {
                            # Legitimate retention from earlier sampled frame.
                            "record_id": "thing:obstacle_000",
                            "provenance": {"frame_id": "chase_frame_000010"},
                        },
                        {
                            "record_id": "thing:front_camera_frame",
                            "provenance": {"frame_id": "chase_frame_000011"},
                        },
                    ],
                },
            },
        ]
        cursor = {"n": 0}

        def load_latest() -> dict:
            idx = min(cursor["n"], len(frames) - 1)
            cursor["n"] += 1
            return frames[idx]

        def probe() -> dict:
            if cursor["n"] < 3:
                return {
                    "status": "live",
                    "last_health": "healthy",
                    "last_record_count": 1,
                    "last_epoch_id": "epoch-1",
                    "reset_count": 1,
                    "implementation_id": "bounded_evidence",
                    "activation": "runtime/memory/active.json",
                }
            return {
                "status": "live",
                "last_health": "empty",
                "last_record_count": 0,
                "last_epoch_id": "epoch-2",
                "reset_count": 2,
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
                    "epoch_id": "epoch-2",
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
        self.assertEqual(result.exit_code, 0, result.message)
        payload = json.loads(result.message)
        self.assertTrue(payload["passed"])
        self.assertEqual(payload["provider"], "chase-sim")
        self.assertEqual(
            payload["safety"]["lifecycle_source"],
            "live_automation_worker+shadow_reference",
        )
        phases = {item["phase"] for item in payload["phase_results"]}
        self.assertIn("shadow_alignment", phases)
        self.assertIn("memory_provenance", phases)
        self.assertIn("observe_only", phases)
        self.assertIn("shadow_isolation", phases)
        self.assertIn("reset", phases)
        self.assertTrue(all(item["passed"] for item in payload["phase_results"]))
        self.assertEqual(payload["safety"]["control_source"], "simulator")
        self.assertEqual(payload["safety"]["action_policy"], "observe_only")
        self.assertTrue(payload["safety"]["simulator_retains_authority"])
        provenance = next(
            item for item in payload["phase_results"] if item["phase"] == "memory_provenance"
        )
        self.assertEqual(provenance["score"]["retained_prior_matches"], 1)
        self.assertEqual(provenance["score"]["current_frame_matches"], 1)

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
        self.assertTrue(any("shadow.chaser_control_source=ws" in v for v in score["violations"]))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)


