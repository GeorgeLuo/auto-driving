from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cli.automa_cli.chase_max_age import (
    ChaseMaxAgeIdentity,
    capacity_eviction_is_ambiguous,
    extract_chase_lifecycle_keys,
    frame_control_is_strict_zero,
    parse_required_max_age_ms,
    parse_required_max_records,
    require_chase_max_age_identity,
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
    simulation_epoch: str = "chase-run:test",
    memory_epoch_id: str = "memory-epoch-0",
    run_id: str = "automation-run-1",
    worker_pid: int = 4242,
    capacity_eviction_count: int = 0,
    omit_observe_only: bool = False,
) -> dict:
    frame = {
        "frame_id": f"chase_frame_{index:06d}",
        "frame_index": index,
        "simulator_frame_index": index,
        "timestamp_ms": timestamp_ms if timestamp_ms is not None else 1_000 + index,
        "simulation_epoch": simulation_epoch,
        "run_id": run_id,
        "worker_pid": worker_pid,
        "control_source": "simulator",
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
            "simulation_epoch": simulation_epoch,
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
                    "simulation_epoch": simulation_epoch,
                }
            },
        },
        "memory": {
            "health": "healthy" if records else "empty",
            "record_count": len(records),
            "records": records,
            "epoch_id": memory_epoch_id,
            "metadata": {
                "capacity_eviction_count": capacity_eviction_count,
            },
        },
    }
    if not omit_observe_only:
        frame["control_application"] = "not_applied"
        frame["action_policy"] = "observe_only"
    return frame


def _live_probe(
    *,
    reset_count: int = 0,
    epoch: str = "memory-epoch-0",
    pid: int = 4242,
    run_id: str = "automation-run-1",
    max_age_ms: int = 1000,
    max_records: int = 32,
    include_bounds: bool = True,
    count: int = 1,
    capacity_eviction_count: int = 0,
) -> dict:
    payload = {
        "status": "live",
        "last_health": "healthy",
        "last_record_count": count,
        "last_epoch_id": epoch,
        "reset_count": reset_count,
        "worker_pid": pid,
        "run_id": run_id,
        "capacity_eviction_count": capacity_eviction_count,
        "implementation_id": "bounded_evidence",
        "activation": "runtime/memory/active.json",
    }
    if include_bounds:
        payload["bounds"] = {"max_age_ms": max_age_ms, "max_records": max_records}
    return payload


def _identity(**overrides: object) -> ChaseMaxAgeIdentity:
    base = dict(
        worker_pid=4242,
        run_id="automation-run-1",
        reset_count=1,
        memory_epoch_id="memory-epoch-0",
        simulation_epoch="chase-run:test",
        capacity_eviction_count=0,
    )
    base.update(overrides)
    return ChaseMaxAgeIdentity(**base)  # type: ignore[arg-type]


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

    def test_parse_required_bounds(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing"):
            parse_required_max_age_ms(None)
        with self.assertRaisesRegex(ValueError, "positive"):
            parse_required_max_age_ms({"max_age_ms": 0})
        self.assertEqual(parse_required_max_age_ms({"max_age_ms": 2500}), 2500)
        with self.assertRaisesRegex(ValueError, "max_records"):
            parse_required_max_records({"max_age_ms": 1000})
        self.assertEqual(
            parse_required_max_records({"max_records": 16, "max_age_ms": 1000}), 16
        )

    def test_control_requires_explicit_observe_only_metadata(self) -> None:
        ok, reason = frame_control_is_strict_zero(
            {"control": {"applied": False, "steering": 0.0, "throttle": 0.0}}
        )
        self.assertFalse(ok)
        self.assertIn("action_policy", str(reason))

        missing_app = {
            "control": {"applied": False, "steering": 0.0, "throttle": 0.0},
            "action_policy": "observe_only",
        }
        ok2, reason2 = frame_control_is_strict_zero(missing_app)
        self.assertFalse(ok2)
        self.assertIn("control_application", str(reason2))

        ok3, _ = frame_control_is_strict_zero(
            {
                "control": {"applied": False, "steering": 0.0, "throttle": 0.0},
                "action_policy": "observe_only",
                "control_application": "not_applied",
            }
        )
        self.assertTrue(ok3)

    def test_identity_requires_worker_pid_and_epochs(self) -> None:
        frame = _chase_frame(
            1,
            [],
            memory_epoch_id="e1",
            run_id="run-a",
            worker_pid=7,
            capacity_eviction_count=0,
        )
        with self.assertRaisesRegex(ValueError, "worker_pid"):
            require_chase_max_age_identity(
                {
                    "status": "live",
                    "reset_count": 1,
                    "last_epoch_id": "e1",
                    "run_id": "run-a",
                },
                frame,
            )
        with self.assertRaisesRegex(ValueError, "run_id"):
            require_chase_max_age_identity(
                {
                    "status": "live",
                    "worker_pid": 7,
                    "reset_count": 1,
                    "last_epoch_id": "e1",
                },
                frame,
            )
        with self.assertRaisesRegex(ValueError, "does not match"):
            require_chase_max_age_identity(
                {
                    "status": "live",
                    "worker_pid": 7,
                    "run_id": "run-a",
                    "reset_count": 1,
                    "last_epoch_id": "probe-epoch",
                },
                _chase_frame(
                    1,
                    [],
                    memory_epoch_id="frame-epoch",
                    run_id="run-a",
                    worker_pid=7,
                ),
            )
        with self.assertRaisesRegex(ValueError, "run_id"):
            require_chase_max_age_identity(
                {
                    "status": "live",
                    "worker_pid": 7,
                    "run_id": "run-new",
                    "reset_count": 1,
                    "last_epoch_id": "e1",
                },
                frame,
            )
        with self.assertRaisesRegex(ValueError, "capacity_eviction_count"):
            bare = _chase_frame(1, [], memory_epoch_id="e1", run_id="run-a", worker_pid=7)
            bare["memory"] = {
                "health": "empty",
                "record_count": 0,
                "records": [],
                "epoch_id": "e1",
                "metadata": {},
            }
            require_chase_max_age_identity(
                {
                    "status": "live",
                    "worker_pid": 7,
                    "run_id": "run-a",
                    "reset_count": 1,
                    "last_epoch_id": "e1",
                },
                bare,
            )
        identity = require_chase_max_age_identity(
            {
                "status": "live",
                "worker_pid": 7,
                "run_id": "run-a",
                "reset_count": 1,
                "last_epoch_id": "e1",
            },
            frame,
        )
        self.assertEqual(identity.worker_pid, 7)
        self.assertEqual(identity.run_id, "run-a")
        self.assertEqual(identity.simulation_epoch, "chase-run:test")
        self.assertEqual(identity.memory_epoch_id, "e1")

    def test_capacity_replacement_on_full_ledger_is_ambiguous(self) -> None:
        self.assertTrue(
            capacity_eviction_is_ambiguous(
                present_keys={"thing:obstacle_000"},
                present_ids={"thing:new_obstacle"},
                max_records=1,
            )
        )
        self.assertFalse(
            capacity_eviction_is_ambiguous(
                present_keys={"thing:obstacle_000"},
                present_ids={"thing:front_camera_frame"},
                max_records=32,
            )
        )

    def test_score_requires_age_identity_and_advancement(self) -> None:
        base = dict(
            lifecycle_keys={"thing:obstacle_000"},
            final_memory={"health": "empty", "records": []},
            control_ok=True,
            reset_used=False,
            max_age_ms=1000,
            age_elapsed_ms=1000,
            identity_stable=True,
            frames_advanced=True,
            capacity_eviction_ambiguous=False,
            headroom_proven=True,
        )
        self.assertTrue(score_chase_max_age_expiry(**base)["passed"])
        too_young = score_chase_max_age_expiry(**{**base, "age_elapsed_ms": 10})
        self.assertFalse(too_young["passed"])
        self.assertIn("before max-age", too_young["reason"])
        no_advance = score_chase_max_age_expiry(**{**base, "frames_advanced": False})
        self.assertFalse(no_advance["passed"])
        identity = score_chase_max_age_expiry(**{**base, "identity_stable": False})
        self.assertFalse(identity["passed"])
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
        worker = {
            "reset_count": 0,
            "epoch": "memory-epoch-0",
            "count": 1,
            "health": "healthy",
            "pid": 4242,
            "run_id": "automation-run-1",
            "capacity_eviction_count": 0,
        }

        def _frame_with_current_epoch(index: int, records: list[dict], **kwargs) -> dict:
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
            item for item in payload["phase_results"] if item["phase"] == "max_age_expiry"
        )
        self.assertTrue(expiry["passed"])
        self.assertEqual(expiry["score"]["lifecycle_keys"], ["thing:obstacle_000"])
        self.assertFalse(expiry["score"].get("reset_used", False))
        self.assertGreaterEqual(int(expiry["score"]["age_elapsed_ms"]), 1000)
        self.assertTrue(expiry["score"]["frames_advanced"])
        self.assertTrue(expiry["score"]["identity_stable"])
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
            item for item in payload["phase_results"] if item["phase"] == "max_age_expiry"
        )
        self.assertIn("max_age_ms", expiry["score"]["reason"])

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
            probe_fn=lambda: _live_probe(reset_count=1, epoch="memory-epoch-0", max_records=2),
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
