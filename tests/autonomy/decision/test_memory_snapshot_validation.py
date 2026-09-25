from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from autonomy.decision import (
    ActivatedMemoryStage,
    DecisionFrameContext,
    Observation,
    read_memory_activation,
)
from tests.autonomy.decision.memory_activation_fixtures import (
    _valid_payload,
    _write_payload,
)


class MemoryActivationTests(unittest.TestCase):
    def test_rejects_snapshot_that_exceeds_its_own_serialized_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"][
                "implementation_spec"
            ] = "tests.autonomy.decision.memory_activation_fixtures:_SelfContradictingBoundsMemory"
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            with self.assertRaisesRegex(ValueError, "declares max_serialized_bytes"):
                stage.update(
                    DecisionFrameContext("f1", 1, 1),
                    Observation("o1", 1, {}),
                )

    def test_rejects_when_normalization_would_exceed_activation_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_config"]["max_serialized_bytes"] = 600
            payload["memory"]["implementation_config"]["eviction_policy"] = "p" * 140
            payload["memory"][
                "implementation_spec"
            ] = "tests.autonomy.decision.memory_activation_fixtures:_NormalizationInflatesSizeMemory"
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            with self.assertRaisesRegex(ValueError, "eviction_policy"):
                stage.update(
                    DecisionFrameContext("f1", 1, 1),
                    Observation("o1", 1, {}),
                )
            # Policy mismatch is rejected before silent normalize-and-relabel.
            self.assertIsNotNone(stage.last_error)
            self.assertIn("eviction_policy", stage.last_error or "")

    def test_rejects_when_normalization_increases_size_past_activation_ceiling(
        self,
    ) -> None:
        """Same eviction_policy; tighter declared size fields make normalize grow past limit."""
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_config"]["max_serialized_bytes"] = 512
            payload["memory"]["implementation_config"][
                "eviction_policy"
            ] = "oldest_first"
            payload["memory"][
                "implementation_spec"
            ] = "tests.autonomy.decision.memory_activation_fixtures:_TighterDeclaredSizeMemory"
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            with self.assertRaisesRegex(ValueError, "normalized memory snapshot"):
                stage.update(
                    DecisionFrameContext("f1", 1, 1),
                    Observation("o1", 1, {}),
                )
            self.assertIn("normalized memory snapshot", stage.last_error or "")

    def test_framework_rejects_non_json_property_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"][
                "implementation_spec"
            ] = "tests.autonomy.decision.memory_activation_fixtures:_NonJsonPropertyMemory"
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            with self.assertRaisesRegex(ValueError, "JSON"):
                stage.update(
                    DecisionFrameContext("frame_9", 9, 900),
                    Observation("obs_9", 890, {}),
                )

    def test_activation_document_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                FileNotFoundError, "memory activation is missing"
            ):
                read_memory_activation(Path(tmp) / "active.json")

        for payload in ([], "x", None):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(ValueError, "must be a JSON object"):
                    read_memory_activation(_write_payload(tmp, payload))

        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["schema"] = "old"
            with self.assertRaisesRegex(ValueError, "unsupported schema"):
                read_memory_activation(_write_payload(tmp, payload))

        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"] = None
            with self.assertRaisesRegex(ValueError, "no memory section"):
                read_memory_activation(_write_payload(tmp, payload))

        for field in ("implementation_id", "implementation_spec"):
            for value in ("", "  ", None, 7):
                with self.subTest(
                    field=field, value=value
                ), tempfile.TemporaryDirectory() as tmp:
                    payload = _valid_payload()
                    payload["memory"][field] = value
                    with self.assertRaisesRegex(ValueError, f"no {field}"):
                        read_memory_activation(_write_payload(tmp, payload))

        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_config"] = []
            with self.assertRaisesRegex(ValueError, "invalid implementation_config"):
                read_memory_activation(_write_payload(tmp, payload))

    def test_implementation_id_mismatch_is_rejected_at_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_id"] = "other_id"
            with self.assertRaisesRegex(ValueError, "implementation_id mismatch"):
                ActivatedMemoryStage(
                    read_memory_activation(_write_payload(tmp, payload))
                )

    def test_selected_config_is_detached_from_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            activation = read_memory_activation(_write_payload(tmp, payload))
            activation.implementation_config["max_records"] = 99
            self.assertEqual(
                activation.payload["memory"]["implementation_config"]["max_records"], 4
            )
