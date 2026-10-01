from __future__ import annotations
import unittest
from cli.automa_cli.chase_max_age import (
    capacity_eviction_is_ambiguous,
    extract_chase_lifecycle_keys,
    frame_control_is_strict_zero,
    score_chase_max_age_expiry,
)
from tests.cli.memory.chase_max_age_fixtures import (
    _chase_frame,
)


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
