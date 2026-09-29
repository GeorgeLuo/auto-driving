from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


_IDENTITY = """
import importlib

from autonomy.shared_memory import SharedMemory as HostSharedMemory
from autonomy.memory import (
    ActivatedMemoryStep,
    MemoryActivation,
    MemoryImplementation as PackageProtocol,
    MemorySnapshot as PackageSnapshot,
    PluginMemoryRunner,
    SharedMemory,
    read_memory_activation,
)
from autonomy.memory.activation import ActivatedMemoryStep as ActivationMemoryStep
from autonomy.memory.plugin import MemoryImplementation
from autonomy.memory.plugin_runner import PluginMemoryRunner as DirectPluginMemoryRunner
from autonomy.memory.selection import memory_plugin_manager
from autonomy.memory.values import MemorySnapshot
from autonomy.decision import (
    ActivatedMemoryStep as DecisionActivatedMemoryStep,
    MemoryImplementation as DecisionPackageProtocol,
    MemorySnapshot as DecisionPackageSnapshot,
    read_memory_activation as decision_read_memory_activation,
)
from autonomy.decision.activation import (
    ActivatedMemoryStep as DecisionModuleActivatedMemoryStep,
    MemoryActivation as DecisionModuleMemoryActivation,
    bounds_from_config as decision_bounds_from_config,
    read_memory_activation as decision_module_read_memory_activation,
)
from autonomy.decision.memory import MemorySnapshot as DecisionModuleSnapshot
from autonomy.decision.plugin import MemoryImplementation as DecisionModuleProtocol
from autonomy.memory.activation import (
    MemoryActivation as DirectMemoryActivation,
    bounds_from_config as direct_bounds_from_config,
    read_memory_activation as direct_read_memory_activation,
)
from autonomy.perception.mappers import PluginPerceptionMapper as MapperPackageClass
from autonomy.perception.mappers.plugin_runner import (
    PluginPerceptionMapper as MapperModuleClass,
)
from autonomy.perception.plugin_runner import PluginPerceptionMapper

spec = importlib.import_module("autonomy.perception.mappers.plugin_runner")
assert spec.PluginPerceptionMapper is PluginPerceptionMapper
assert MapperPackageClass is PluginPerceptionMapper is MapperModuleClass
assert DecisionModuleSnapshot is DecisionPackageSnapshot is PackageSnapshot is MemorySnapshot
assert DecisionModuleProtocol is DecisionPackageProtocol is PackageProtocol is MemoryImplementation
assert DecisionActivatedMemoryStep is ActivatedMemoryStep
assert DecisionModuleActivatedMemoryStep is ActivatedMemoryStep
assert ActivationMemoryStep is ActivatedMemoryStep is PluginMemoryRunner
assert DirectPluginMemoryRunner is PluginMemoryRunner
assert DecisionModuleMemoryActivation is MemoryActivation is DirectMemoryActivation
assert decision_read_memory_activation is read_memory_activation is direct_read_memory_activation
assert decision_bounds_from_config is direct_bounds_from_config
assert HostSharedMemory is SharedMemory
assert MemoryActivation is not None and memory_plugin_manager is not None
"""


class PackageOwnershipTests(unittest.TestCase):
    def test_legacy_imports_are_the_same_objects(self) -> None:
        root = Path(__file__).resolve().parents[3]
        orders = (
            "import autonomy.decision",
            "import autonomy.memory",
            "import autonomy.perception",
            "import autonomy.memory.plugin",
            "import autonomy.perception.mappers.plugin_runner",
            "from autonomy.decision.activation import MemoryActivation",
        )
        for first in orders:
            with self.subTest(first=first):
                completed = subprocess.run(
                    [sys.executable, "-c", f"{first}\n{_IDENTITY}"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
