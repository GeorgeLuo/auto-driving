"""DecisionDataSource contract tests (M006-01)."""

from __future__ import annotations

import unittest

from autonomy.decision_cycle.proposal.inputs import (
    build_decision_data_source,
    evidence_envelope,
    omit_forbidden_channel_keys,
    ready_envelope,
    unavailable_envelope,
)
from autonomy.decision_cycle.memory.evidence import MemoryOrigin, RetainedEvidence
from autonomy.decision_cycle.perception.evidence.values import ViewLocation
from tests.support.action_fixtures import packaged_decision_chain


def _record(*, frame_id: str = "frame_001", kind: str = "floor_boundary") -> RetainedEvidence:
    return RetainedEvidence(
        record_id=f"thing:1:{kind}",
        kind=kind,
        label=kind,
        confidence=0.9,
        origin=MemoryOrigin(
            observation_id="obs",
            observed_id="ev",
            coordinate_frame="image",
            observed_at_ms=1000,
            updated_at_ms=1000,
            source_plugin_id="plugin",
            frame_id=frame_id,
        ),
        location=ViewLocation(
            frame="image", zone="left", bbox_xyxy_norm=(0.0, 0.0, 0.2, 0.5)
        ),
        properties={},
    )


class DecisionDataSourceTests(unittest.TestCase):
    def test_evidence_envelope(self) -> None:
        env = evidence_envelope(None)
        self.assertEqual(env.status, "unavailable")
        self.assertEqual(env.reason, "evidence_not_published")
        self.assertIsNone(env.value)

        self.assertEqual(evidence_envelope(()).value, ())
        self.assertEqual(evidence_envelope(()).status, "ready")

        record = _record()
        env = evidence_envelope([record])
        self.assertEqual(env.status, "ready")
        self.assertEqual(env.value, (record,))
        self.assertIsNot(env.value[0], record)
        self.assertEqual(env.to_dict()["value"], [record.to_dict()])

        with self.assertRaises(TypeError):
            evidence_envelope([{"record_id": "raw"}])

    def test_frozen_against_mutation(self) -> None:
        source = build_decision_data_source(
            frame_id="frame_001",
            frame_index=0,
            timestamp_ms=10,
            evidence=(_record(),),
        )
        payload = source.to_dict()
        payload["evidence"]["status"] = "error"
        payload["evidence"]["value"][0]["label"] = "mutated"
        again = source.to_dict()
        self.assertEqual(again["evidence"]["status"], "ready")
        self.assertEqual(again["evidence"]["value"][0]["label"], "floor_boundary")
        # Dataclass freeze rejects attribute rebinding through normal assignment.
        with self.assertRaises(Exception):
            source.frame_id = "hijacked"  # type: ignore[misc]

    def test_observation_default_unconfigured(self) -> None:
        source = build_decision_data_source(
            frame_id="f1", frame_index=0, timestamp_ms=1
        )
        # Default observe step is unconfigured for this unit.
        self.assertEqual(source.observation.status, "unavailable")
        self.assertEqual(source.observation.reason, "observation_not_configured")


    def test_omitting_evaluator_keys_makes_a_live_capture_admissible(self) -> None:
        dirty = {
            "readings": {
                "front_camera": {
                    "metadata": {
                        "content_type": "image/png",
                        "evaluator_reference": {"status": "available"},
                    }
                }
            }
        }
        cleaned = omit_forbidden_channel_keys(dirty)
        self.assertNotIn(
            "evaluator_reference",
            cleaned["readings"]["front_camera"]["metadata"],
        )
        self.assertEqual(
            cleaned["readings"]["front_camera"]["metadata"]["content_type"],
            "image/png",
        )
        source = build_decision_data_source(
            frame_id="f",
            frame_index=0,
            timestamp_ms=1,
            observation={"observation_id": "obs", "created_at_ms": 1, "sensor_frame": cleaned, "things": [], "signals": [], "summary": [], "artifacts": {}, "metadata": {}, "schema": "decision_observation_v1"},
        )
        self.assertEqual(source.observation.status, "ready")

    def test_rejects_evaluator_and_map_metadata(self) -> None:
        from autonomy.serialization import canonical_json_bytes

        with self.assertRaises(ValueError):
            build_decision_data_source(
                frame_id="f",
                frame_index=0,
                timestamp_ms=1,
                metadata={"evaluator": {"ground_truth": "privileged"}},
            )
        with self.assertRaises(ValueError):
            build_decision_data_source(
                frame_id="f",
                frame_index=0,
                timestamp_ms=1,
                metadata={"map": {"lanes": []}},
            )
        with self.assertRaises(ValueError):
            build_decision_data_source(
                frame_id="f",
                frame_index=0,
                timestamp_ms=1,
                metadata={"nested": {"reference_decision": 1}},
            )
        with self.assertRaises(ValueError):
            build_decision_data_source(
                frame_id="f",
                frame_index=0,
                timestamp_ms=1,
                metadata={"reference-decision": {"x": 1}},
            )
        # Legal sources remain serializable end-to-end.
        source = build_decision_data_source(
            frame_id="f", frame_index=0, timestamp_ms=1
        )
        canonical_json_bytes(source.to_dict())


    def test_ready_observation_accepts_detached_dict(self) -> None:
        from autonomy.decision_cycle.observation.values import Observation
        from autonomy.decision_cycle.proposal.inputs import (
            DecisionDataSource,
        )

        obs = Observation(
            observation_id="obs-1",
            created_at_ms=1,
            sensor_frame={},
            summary=(),
            things=(),
            signals=(),
        )
        source = build_decision_data_source(
            frame_id="f",
            frame_index=0,
            timestamp_ms=1,
            observation=None,
            # Ready detached dict through envelope constructor.
        )
        # Direct envelope path with detached dict.

        ready = ready_envelope(obs.to_dict(), updated_at_ms=1)
        source = DecisionDataSource(
            frame_id="f",
            frame_index=0,
            timestamp_ms=1,
            observation=ready,
            evidence=unavailable_envelope("evidence_not_published"),
        )
        self.assertEqual(source.observation.status, "ready")
        self.assertIsInstance(source.observation.value, Observation)
        self.assertEqual(source.observation.value.observation_id, "obs-1")

    def test_runner_default_observation_not_configured(self) -> None:

        result = packaged_decision_chain().run(
            frame_id="f", frame_index=0, timestamp_ms=1
        )
        control = result.control
        self.assertEqual(result.status, "ok")
        assert result.source is not None
        self.assertEqual(result.source.observation.status, "unavailable")
        self.assertEqual(
            result.source.observation.reason, "observation_not_configured"
        )
        self.assertEqual(control.steering, 0.0)

    def test_compound_privileged_origin_keys_rejected(self) -> None:
        from autonomy.decision_cycle.observation.values import Observation

        obs = Observation(
            observation_id="o",
            created_at_ms=1,
            sensor_frame={},
            metadata={"EvaluatorOutput": {"direction": "left"}},
        )
        with self.assertRaises(ValueError):
            build_decision_data_source(
                frame_id="f", frame_index=0, timestamp_ms=1, observation=obs
            )
        for bad_key in (
            "MapPrivileged",
            "DebugTruthPayload",
            "ReferenceDecisionV2",
            "evaluator_output",
        ):
            with self.subTest(key=bad_key):
                with self.assertRaises(ValueError):
                    build_decision_data_source(
                        frame_id="f",
                        frame_index=0,
                        timestamp_ms=1,
                        metadata={bad_key: {"x": 1}},
                    )


    def test_runner_observation_dict_and_error_paths(self) -> None:
        from autonomy.decision_cycle.observation.values import Observation

        engine = packaged_decision_chain()
        obs = Observation(
            observation_id="obs-runner",
            created_at_ms=1,
            sensor_frame={},
        )
        result = engine.run(
            frame_id="f",
            frame_index=0,
            timestamp_ms=1,
            observation=obs.to_dict(),
        )
        control = result.control
        self.assertEqual(result.status, "ok")
        assert result.source is not None
        self.assertEqual(result.source.observation.status, "ready")
        self.assertIsInstance(result.source.observation.value, Observation)
        self.assertEqual(
            result.source.observation.value.observation_id, "obs-runner"
        )
        self.assertEqual(control.steering, 0.0)

        err_result = engine.run(
            frame_id="f",
            frame_index=0,
            timestamp_ms=1,
            observation_error="camera_failed",
        )
        err_control = err_result.control
        self.assertEqual(err_result.status, "ok")
        assert err_result.source is not None
        self.assertEqual(err_result.source.observation.status, "error")
        self.assertEqual(err_result.source.observation.reason, "camera_failed")
        self.assertIsNone(err_result.source.observation.value)
        self.assertEqual(err_control.steering, 0.0)


if __name__ == "__main__":
    unittest.main()
