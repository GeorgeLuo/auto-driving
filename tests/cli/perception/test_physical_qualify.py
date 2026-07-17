from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomy.perception import PerceptionText
from cli.automa_cli.physical_qualify import (
    _compare_metrics,
    _perception_to_score_payload,
    _promotion_decision,
    _strategy_metrics,
    run_physical_strategy_qualification,
)


def _fake_perception(*, zones: list[str], floor_visible: bool = True) -> PerceptionText:
    signals = [
        {
            "signal_id": "floor_visible",
            "value": floor_visible,
            "confidence": 1.0,
        },
        {
            "signal_id": "floor_boundary_available",
            "value": bool(zones),
            "confidence": 0.8 if zones else 0.0,
        },
    ]
    things = []
    for index, zone in enumerate(zones):
        things.append(
            {
                "thing_id": f"floor_boundary_{index:03d}",
                "kind": "floor_boundary",
                "confidence": 0.8,
                "location": {
                    "zone": zone,
                    "frame": "image",
                    "bbox_xyxy_norm": [0.2, 0.4, 0.3, 0.5],
                },
            }
        )
    things.append(
        {
            "thing_id": "traversable_floor",
            "kind": "surface",
            "confidence": 1.0,
            "location": {"zone": "visible_floor", "frame": "topdown_fov"},
        }
    )
    return PerceptionText.from_dict(
        {
            "schema": "perception_text_v2",
            "plugin_id": "test",
            "status": "ok",
            "lines": ["test"],
            "signals": signals,
            "things": things,
        }
    )


class PhysicalQualifyUnitTests(unittest.TestCase):
    def test_promotion_requires_two_behavioral_improvements(self) -> None:
        control = {
            "overall_pass_rate": 0.6,
            "directional_zone_hit_rate": 0.5,
            "clear_false_positive_boundaries_mean": 1.0,
            "removal_pass_rate": 1.0,
            "mean_boundary_count": 2.0,
            "median_duration_ms": 50.0,
        }
        better = {
            "overall_pass_rate": 0.9,
            "directional_zone_hit_rate": 0.8,
            "clear_false_positive_boundaries_mean": 1.0,
            "removal_pass_rate": 1.0,
            "mean_boundary_count": 2.0,
            "median_duration_ms": 80.0,
        }
        comparison = _compare_metrics(control, better)
        decision = _promotion_decision(comparison, control, better)
        self.assertEqual(decision["status"], "promote_candidate")
        self.assertGreaterEqual(len(decision["behavioral_improvements"]), 2)

        worse = {
            "overall_pass_rate": 0.5,
            "directional_zone_hit_rate": 0.4,
            "clear_false_positive_boundaries_mean": 2.0,
            "removal_pass_rate": 0.0,
            "mean_boundary_count": 3.0,
            "median_duration_ms": 40.0,
        }
        comparison = _compare_metrics(control, worse)
        decision = _promotion_decision(comparison, control, worse)
        self.assertEqual(decision["status"], "reject_keep_control")

    def test_score_payload_marks_offline_apply_as_manual_zero_control(self) -> None:
        perception = _fake_perception(zones=["mid_right"]).to_dict()
        payload = _perception_to_score_payload(
            perception_dict=perception,
            frame_id="frame",
            duration_ms=12.0,
        )
        self.assertEqual(payload["mode"], "user")
        self.assertEqual(payload["control"]["steering"], 0.0)
        self.assertEqual(payload["health"], "healthy")


class PhysicalQualifyCommandTests(unittest.TestCase):
    def test_qualify_from_check_run_with_mocked_mappers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            check_run = root / "check"
            check_run.mkdir()
            qualify_root = root / "qualify"
            sequence = {
                "clear": [],
                "left": ["mid_left"],
                "center": ["center"],
                "right": ["mid_right"],
                "removed": [],
            }
            for index, (placement, zones) in enumerate(sequence.items(), start=1):
                step = check_run / f"{index:02d}-{placement}"
                step.mkdir()
                (step / "frame.jpg").write_bytes(b"\xff\xd8\xff\xd9")

            control_calls = {"n": 0}
            candidate_calls = {"n": 0}
            placements = list(sequence.keys())

            class FakeControl:
                def reset(self):
                    return None

                def perceive(self, request):
                    del request
                    placement = placements[control_calls["n"]]
                    control_calls["n"] += 1
                    return _fake_perception(zones=sequence[placement])

            class FakeCandidate:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return None

                def reset(self):
                    return None

                def perceive(self, request):
                    del request
                    # Worse clear FP, same directional hits.
                    placement = placements[candidate_calls["n"]]
                    candidate_calls["n"] += 1
                    if placement == "clear":
                        return _fake_perception(zones=["mid_left", "mid_right"])
                    return _fake_perception(zones=sequence[placement])

            with patch(
                "cli.automa_cli.physical_qualify.PERCEPTION_ALGORITHMS",
                {"lightweight_observer": {"mapper_spec": "x", "mapper_config": {}}},
            ), patch(
                "cli.automa_cli.physical_qualify._load_mapper",
                return_value=FakeControl(),
            ), patch(
                "cli.automa_cli.physical_qualify._close_mapper",
                return_value=None,
            ), patch(
                "cli.automa_cli.physical_qualify.LabPerceptionMapper",
                return_value=FakeCandidate(),
            ), patch(
                "cli.automa_cli.physical_qualify.QUALIFY_OUTPUT_ROOT",
                qualify_root,
            ):
                result = run_physical_strategy_qualification(
                    check_run=check_run,
                    control_algorithm="lightweight_observer",
                    candidate_id="floor_continuity",
                    record=True,
                    json_output=True,
                )
            self.assertEqual(result.exit_code, 0, result.message)
            report = json.loads(result.message)
            self.assertEqual(report["decision"]["status"], "reject_keep_control")
            self.assertTrue(report["decision"]["control_remains_operational_fallback"])
            self.assertFalse(report["decision"]["onboard_pi_viability_measured"])
            out = Path(report["out_dir"])
            self.assertTrue((out / "report.json").exists())
            self.assertTrue((out / "summary.md").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
