from __future__ import annotations
import json
import unittest
from autonomy.decision.memory import canonical_json_bytes, canonical_json_utf8
from cli.automa_cli.decision import (
    ADAPTER_ENGINE_SPEC,
    strict_decode_apply_memory,
    strict_decode_apply_observation,
)
from tests.support.cli_runner import run_automa
from tests.cli.decision.shadow_decision_surfaces_fixtures import (
    ACTIVE_RUN,
    ShadowDecisionSurfaceFixture,
)


class ShadowDecisionSurfaceTests(ShadowDecisionSurfaceFixture, unittest.TestCase):
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

        good_mem = json.loads((ACTIVE_RUN / "sequence.json").read_text())["frames"][0][
            "memory"
        ]
        missing_created = dict(good_mem)
        del missing_created["created_at_ms"]
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_memory(missing_created)

        bounds_incomplete = dict(good_mem)
        bounds_incomplete["bounds"] = {
            k: v for k, v in good_mem["bounds"].items() if k != "max_serialized_bytes"
        }
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_memory(bounds_incomplete)

        bounds_str = dict(good_mem)
        bounds_str["bounds"] = dict(good_mem["bounds"])
        bounds_str["bounds"]["max_records"] = "2"
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_memory(bounds_str)

        conf_str = dict(good_mem)
        conf_str["records"] = [dict(good_mem["records"][0])]
        conf_str["records"][0] = dict(conf_str["records"][0])
        conf_str["records"][0]["confidence"] = "0.8"
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_memory(conf_str)

        non_dict_record = dict(good_mem)
        non_dict_record["records"] = ["nope"]
        non_dict_record["record_count"] = 1
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_memory(non_dict_record)

        bad_location = dict(good_mem)
        bad_location["records"] = [dict(good_mem["records"][0])]
        bad_location["records"][0] = dict(bad_location["records"][0])
        bad_location["records"][0]["location"] = "left"
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_memory(bad_location)

        count_mismatch = dict(good_mem)
        count_mismatch["record_count"] = 99
        with self.assertRaises(DecisionSurfaceError):
            strict_decode_apply_memory(count_mismatch)

        # complete export accepted
        strict_decode_apply_observation(good_obs)
        strict_decode_apply_memory(good_mem)

    def test_canonical_json_utf8_not_length_only(self) -> None:
        a = {"a": 1, "b": 2}
        b = {"a": 2, "b": 1}
        # same length different content is possible
        self.assertEqual(canonical_json_bytes(a), canonical_json_bytes(b))
        self.assertNotEqual(canonical_json_utf8(a), canonical_json_utf8(b))

    def test_cli_update_shadow_engine_choice(self) -> None:
        result = run_automa(
            "vehicles",
            "update",
            "decision",
            "--id",
            "chase-sim-chaser",
            "--engine",
            "shadow-proposals",
            "--json",
            runtime_root=self.runtime_root,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload["manifest"]["decision"]["engine_spec"], ADAPTER_ENGINE_SPEC
        )
