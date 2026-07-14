from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class DependencyBoundaryTests(unittest.TestCase):
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

    def test_stable_perception_contains_no_concrete_algorithms(self) -> None:
        perception_root = ROOT / "autonomy" / "perception"
        forbidden_packages = {"cv2", "numpy", "PIL", "scipy", "sklearn", "torch", "ultralytics"}
        forbidden_domains = {"core", "features", "landmarks", "motion", "traversability"}
        dependency_violations: list[str] = []
        domain_violations: list[str] = []
        sensor_policy_violations: list[str] = []

        for path in perception_root.rglob("*.py"):
            relative = path.relative_to(perception_root)
            if relative.parts[0] in forbidden_domains:
                domain_violations.append(str(relative))
            source = path.read_text(encoding="utf-8")
            if "FRONT_CAMERA_SENSOR_ID" in source:
                sensor_policy_violations.append(str(relative))
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                imported: list[str] = []
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported = [node.module]
                if any(name.split(".", 1)[0] in forbidden_packages for name in imported):
                    dependency_violations.append(str(relative))

        self.assertEqual(domain_violations, [])
        self.assertEqual(dependency_violations, [])
        self.assertEqual(sensor_policy_violations, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
