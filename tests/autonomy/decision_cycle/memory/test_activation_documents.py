from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.memory.plugin_runner import PluginMemoryRunner
from autonomy.decision_cycle.memory.activation import read_memory_activation
from tests.autonomy.decision_cycle.memory.activation_fixtures import (
    _valid_payload,
    _write_payload,
)


class MemoryActivationDocumentTests(unittest.TestCase):
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

    def test_catalog_id_may_differ_from_implementation_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_id"] = "other_id"
            step = PluginMemoryRunner(
                read_memory_activation(_write_payload(tmp, payload))
            )
            self.assertEqual(step.plugin_ids, ("other_id",))
            self.assertEqual(step.implementation.implementation_id, "recording_test")
            status = step.status()
            self.assertEqual(status["implementation_id"], "recording_test")
            self.assertEqual(status["plugins"][0]["plugin_id"], "other_id")
            self.assertEqual(status["plugins"][0]["implementation_id"], "recording_test")
            self.assertEqual(step.report()["plugins"][0]["implementation_id"], "recording_test")

    def test_selected_config_is_detached_from_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_config"]["max_records"] = 4
            activation = read_memory_activation(_write_payload(tmp, payload))
            activation.implementation_config["max_records"] = 99
            self.assertEqual(
                activation.payload["memory"]["implementation_config"]["max_records"], 4
            )
