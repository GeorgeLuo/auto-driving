from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


class MemoryPublicPathTests(unittest.TestCase):
    def test_memory_package_and_legacy_decision_imports_resolve_identically(self) -> None:
        script = """
from autonomy.memory import (
    ActivatedMemoryStep,
    MemoryActivation,
    PluginMemoryRunner,
    SharedMemory,
    read_memory_activation,
)
from autonomy.memory.activation import ActivatedMemoryStep as ActivationMemoryStep
from autonomy.memory.plugin_runner import PluginMemoryRunner as DirectPluginMemoryRunner
from autonomy.memory.selection import memory_plugin_manager
from autonomy.decision import (
    ActivatedMemoryStep as DecisionActivatedMemoryStep,
    read_memory_activation as decision_read_memory_activation,
)
assert DecisionActivatedMemoryStep is ActivatedMemoryStep
assert ActivationMemoryStep is ActivatedMemoryStep is PluginMemoryRunner
assert DirectPluginMemoryRunner is PluginMemoryRunner
assert decision_read_memory_activation is read_memory_activation
assert SharedMemory is not None and MemoryActivation is not None
assert memory_plugin_manager is not None
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
