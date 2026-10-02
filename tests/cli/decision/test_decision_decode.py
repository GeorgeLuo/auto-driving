from __future__ import annotations
import json
import unittest
from autonomy.serialization import canonical_json_bytes, canonical_json_utf8
from cli.automa_cli.decision import (
    strict_decode_apply_evidence,
    strict_decode_apply_observation,
)
from tests.cli.decision.decision_surfaces_fixtures import (
    ACTIVE_RUN,
    DecisionSurfaceFixture,
)


class DecisionSurfaceTests(DecisionSurfaceFixture, unittest.TestCase):
    def test_strict_decode_rejects_malformations(self) -> None:
        from cli.automa_cli.decision import DecisionSurfaceError

        with self.assertRaises(DecisionSurfaceError) as ctx:
            strict_decode_apply_observation({"observation_id": "only"})
        self.assertEqual(ctx.exception.error, "run_invalid")

        good_obs = json.loads((ACTIVE_RUN / "sequence.json").read_text())["frames"][0][
            "observation"
        ]
        coerced = dict(good_obs)
        coerced["created_at_ms"] = "123"
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_observation(coerced)

        artifacts_bad = dict(good_obs)
        artifacts_bad["artifacts"] = {"k": 7}
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_observation(artifacts_bad)

        things_bad = dict(good_obs)
        things_bad["things"] = ["not-a-dict"]
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_observation(things_bad)

        extra = dict(good_obs)
        extra["extra_key"] = True
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_observation(extra)

        good_evidence = json.loads((ACTIVE_RUN / "sequence.json").read_text())["frames"][0][
            "evidence"
        ]
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_evidence({"records": good_evidence})

        conf_str = [dict(good_evidence[0])]
        conf_str[0]["confidence"] = "0.8"
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_evidence(conf_str)

        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_evidence(["nope"])

        bad_location = [dict(good_evidence[0])]
        bad_location[0]["location"] = "left"
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_evidence(bad_location)

        missing_provenance_key = [dict(good_evidence[0])]
        missing_provenance_key[0]["provenance"] = {
            key: value
            for key, value in good_evidence[0]["provenance"].items()
            if key != "updated_at_ms"
        }
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_evidence(missing_provenance_key)

        # complete export accepted
        strict_decode_apply_observation(good_obs)
        self.assertEqual(len(strict_decode_apply_evidence(good_evidence)), len(good_evidence))

    def test_strict_decode_rejects_the_old_provenance_key(self) -> None:
        from cli.automa_cli.decision import DecisionSurfaceError

        good_evidence = json.loads((ACTIVE_RUN / "sequence.json").read_text())["frames"][0][
            "evidence"
        ]
        recorded = dict(good_evidence[0])
        provenance = dict(recorded["provenance"])
        provenance["evidence_id"] = provenance.pop("observed_id")
        recorded["provenance"] = provenance

        with self.assertRaises(DecisionSurfaceError) as ctx:
            strict_decode_apply_evidence([recorded])
        self.assertEqual(ctx.exception.error, "run_invalid")

    def test_canonical_json_utf8_not_length_only(self) -> None:
        a = {"a": 1, "b": 2}
        b = {"a": 2, "b": 1}
        # same length different content is possible
        self.assertEqual(canonical_json_bytes(a), canonical_json_bytes(b))
        self.assertNotEqual(canonical_json_utf8(a), canonical_json_utf8(b))
