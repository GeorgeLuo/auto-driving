from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autonomy.perception import read_perception_activation


def _valid_payload() -> dict:
    return {
        "schema": "automa_perception_activation_v0",
        "perception": {
            "algorithm": "current",
            "mapper_spec": "autonomy.perception.mappers.plugin_runner:PluginRunnerPerceptionMapper",
            "mapper_config": {
                "plugins": ["frame", "floor_plane"],
                "plugin_specs": {"frame": "implementations.perception.plugins:FramePlugin"},
            },
        },
        "controller_bundle": {"release": "fixture-release"},
    }


def _write_payload(root: str, payload: object) -> Path:
    activation_path = Path(root) / "active.json"
    activation_path.write_text(json.dumps(payload), encoding="utf-8")
    return activation_path


class PerceptionActivationTests(unittest.TestCase):
    def test_activation_reads_the_declared_mapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            activation_path = _write_payload(tmp, _valid_payload())

            activation = read_perception_activation(activation_path)

        self.assertEqual(activation.algorithm, "current")
        self.assertEqual(
            activation.mapper_spec,
            "autonomy.perception.mappers.plugin_runner:PluginRunnerPerceptionMapper",
        )
        self.assertEqual(activation.mapper_config["plugins"], ["frame", "floor_plane"])

    def test_activation_file_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(FileNotFoundError, "perception activation is missing"):
                read_perception_activation(Path(tmp) / "active.json")

    def test_activation_requires_a_json_object(self) -> None:
        for payload in ([], None, "activation"):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmp:
                activation_path = _write_payload(tmp, payload)

                with self.assertRaisesRegex(ValueError, "must be a JSON object"):
                    read_perception_activation(activation_path)

    def test_activation_rejects_an_unknown_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["schema"] = "old_schema"
            activation_path = _write_payload(tmp, payload)

            with self.assertRaisesRegex(ValueError, "unsupported schema"):
                read_perception_activation(activation_path)

    def test_activation_requires_a_perception_section(self) -> None:
        for perception in (None, [], "current"):
            with self.subTest(perception=perception), tempfile.TemporaryDirectory() as tmp:
                payload = _valid_payload()
                payload["perception"] = perception
                activation_path = _write_payload(tmp, payload)

                with self.assertRaisesRegex(ValueError, "no perception section"):
                    read_perception_activation(activation_path)

    def test_activation_requires_non_blank_algorithm_and_mapper_spec(self) -> None:
        for field in ("algorithm", "mapper_spec"):
            for value in (None, 7, "", "   "):
                with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as tmp:
                    payload = _valid_payload()
                    payload["perception"][field] = value
                    activation_path = _write_payload(tmp, payload)

                    with self.assertRaisesRegex(ValueError, f"no {field}"):
                        read_perception_activation(activation_path)

    def test_activation_requires_a_mapper_config_object(self) -> None:
        for config in (None, [], "current"):
            with self.subTest(config=config), tempfile.TemporaryDirectory() as tmp:
                payload = _valid_payload()
                payload["perception"]["mapper_config"] = config
                activation_path = _write_payload(tmp, payload)

                with self.assertRaisesRegex(ValueError, "invalid mapper_config"):
                    read_perception_activation(activation_path)

    def test_selected_config_is_detached_from_the_preserved_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            activation = read_perception_activation(_write_payload(tmp, payload))

        activation.mapper_config["plugins"].append("motion_regions")
        self.assertEqual(
            activation.payload["perception"]["mapper_config"]["plugins"],
            ["frame", "floor_plane"],
        )

        activation.payload["perception"]["mapper_config"]["plugin_specs"]["motion"] = "fixture"
        self.assertNotIn("motion", activation.mapper_config["plugin_specs"])
        self.assertEqual(
            activation.payload["controller_bundle"],
            {"release": "fixture-release"},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
