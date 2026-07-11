from __future__ import annotations

import ast
import subprocess
import unittest
from pathlib import Path

from autonomy.perception.mappers.current import CurrentDirectoryPerceptionMapper
from autonomy.runtime import AutonomyControl, AutonomyManager, AutonomySnapshot
from autonomy.runtime.manager import EngineLoadError
from autonomy.vehicle import CarInterface


ROOT = Path(__file__).resolve().parents[2]


class MissingResetEngine:
    def step(self, snapshot: AutonomySnapshot) -> AutonomyControl:
        return AutonomyControl(reason="missing-reset")


class DictionaryOutputEngine:
    def reset(self) -> None:
        return None

    def describe_schema(self) -> dict[str, str]:
        return {"schema": "autonomy_engine_schema_v0"}

    def step(self, snapshot: AutonomySnapshot) -> dict[str, float]:
        return {"steering": 0.0, "throttle": 0.0}


class MissingSchemaEngine:
    def reset(self) -> None:
        return None

    def step(self, snapshot: AutonomySnapshot) -> AutonomyControl:
        return AutonomyControl(reason="missing-schema")


class ContractStrictnessTests(unittest.TestCase):
    def test_stable_autonomy_does_not_import_implementations(self) -> None:
        violations: list[str] = []
        for path in (ROOT / "autonomy").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                imported: list[str] = []
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported = [node.module]
                if any(name == "implementations" or name.startswith("implementations.") for name in imported):
                    violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [])

    def test_generated_runtime_ignore_does_not_hide_runtime_source_packages(self) -> None:
        for source_path in (
            "autonomy/runtime/engine.py",
            "implementations/runtime/donkeycar/donkey_part.py",
        ):
            result = subprocess.run(
                ["git", "check-ignore", "-q", source_path],
                cwd=ROOT,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0, source_path)

        generated = subprocess.run(
            ["git", "check-ignore", "-q", "runtime/vehicles/example/state.json"],
            cwd=ROOT,
            check=False,
        )
        self.assertEqual(generated.returncode, 0)

    def test_vehicle_contract_has_one_sensor_read_path(self) -> None:
        self.assertTrue(hasattr(CarInterface, "read_sensors"))
        self.assertFalse(hasattr(CarInterface, "capture_frame"))

    def test_mapper_rejects_removed_configuration_option(self) -> None:
        with self.assertRaises(TypeError):
            CurrentDirectoryPerceptionMapper(include_traversability=True)

    def test_engine_requires_reset_method(self) -> None:
        manager = AutonomyManager()

        with self.assertRaises(EngineLoadError):
            manager.load_engine(f"{__name__}:MissingResetEngine")

    def test_engine_rejects_dictionary_control_output(self) -> None:
        manager = AutonomyManager()
        manager.load_engine(f"{__name__}:DictionaryOutputEngine")

        control = manager.step(AutonomySnapshot())

        self.assertEqual(control.reason, "engine-error")
        self.assertIn("must return AutonomyControl", manager.last_error or "")

    def test_engine_requires_schema_method(self) -> None:
        manager = AutonomyManager()

        with self.assertRaises(EngineLoadError):
            manager.load_engine(f"{__name__}:MissingSchemaEngine")


if __name__ == "__main__":
    unittest.main(verbosity=2)
