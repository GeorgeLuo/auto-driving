from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autonomy.runtime import AutonomyManager, apply_decision_activation, read_decision_activation


def _valid_payload() -> dict:
    return {
        "schema": "automa_decision_activation_v0",
        "decision": {
            "engine_id": "idle",
            "engine_spec": "autonomy.runtime.engine:IdleAutonomyEngine",
            "engine_config": {
                "policy": {
                    "stages": ["perception", "decision"],
                }
            },
        },
        "controller_bundle": {"release": "fixture-release"},
    }


def _write_payload(root: str, payload: object) -> Path:
    activation_path = Path(root) / "active.json"
    activation_path.write_text(json.dumps(payload), encoding="utf-8")
    return activation_path


class DecisionActivationTests(unittest.TestCase):
    def test_activation_loads_and_applies_the_declared_engine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["decision"]["engine_config"] = {}
            activation_path = _write_payload(tmp, payload)

            activation = read_decision_activation(activation_path)
            manager = AutonomyManager()
            status = apply_decision_activation(manager, activation)

        self.assertEqual(activation.engine_id, "idle")
        self.assertEqual(status["engine"], "autonomy.runtime.engine:IdleAutonomyEngine")

    def test_activation_file_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(FileNotFoundError, "decision activation is missing"):
                read_decision_activation(Path(tmp) / "active.json")

    def test_activation_requires_a_json_object(self) -> None:
        for payload in ([], None, "activation"):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmp:
                activation_path = _write_payload(tmp, payload)

                with self.assertRaisesRegex(ValueError, "must be a JSON object"):
                    read_decision_activation(activation_path)

    def test_activation_rejects_an_unknown_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["schema"] = "old_schema"
            activation_path = _write_payload(tmp, payload)

            with self.assertRaisesRegex(ValueError, "unsupported schema"):
                read_decision_activation(activation_path)

    def test_activation_requires_a_decision_section(self) -> None:
        for decision in (None, [], "idle"):
            with self.subTest(decision=decision), tempfile.TemporaryDirectory() as tmp:
                payload = _valid_payload()
                payload["decision"] = decision
                activation_path = _write_payload(tmp, payload)

                with self.assertRaisesRegex(ValueError, "no decision section"):
                    read_decision_activation(activation_path)

    def test_activation_requires_non_blank_engine_identity_and_spec(self) -> None:
        for field in ("engine_id", "engine_spec"):
            for value in (None, 7, "", "   "):
                with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as tmp:
                    payload = _valid_payload()
                    payload["decision"][field] = value
                    activation_path = _write_payload(tmp, payload)

                    with self.assertRaisesRegex(ValueError, f"no {field}"):
                        read_decision_activation(activation_path)

    def test_activation_requires_an_engine_config_object(self) -> None:
        for config in (None, [], "idle"):
            with self.subTest(config=config), tempfile.TemporaryDirectory() as tmp:
                payload = _valid_payload()
                payload["decision"]["engine_config"] = config
                activation_path = _write_payload(tmp, payload)

                with self.assertRaisesRegex(ValueError, "invalid engine_config"):
                    read_decision_activation(activation_path)

    def test_selected_config_is_detached_from_the_preserved_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            activation = read_decision_activation(_write_payload(tmp, payload))

        activation.engine_config["policy"]["stages"].append("action")
        self.assertEqual(
            activation.payload["decision"]["engine_config"]["policy"]["stages"],
            ["perception", "decision"],
        )

        activation.payload["decision"]["engine_config"]["policy"]["stages"].append("memory")
        self.assertEqual(
            activation.engine_config["policy"]["stages"],
            ["perception", "decision", "action"],
        )
        self.assertEqual(
            activation.payload["controller_bundle"],
            {"release": "fixture-release"},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
