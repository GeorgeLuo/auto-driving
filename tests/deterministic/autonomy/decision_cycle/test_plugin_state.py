from __future__ import annotations

import math
import unittest
from concurrent.futures import ThreadPoolExecutor

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.plugin import MemoryPluginContract
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.decision_cycle.plugin_state import RetainedStateError, unchanged_instance


class _Plugin:
    plugin_id = "probe"

    def __init__(self) -> None:
        self.config = {"limits": [1, 2]}
        self.gain = math.nan
        self.pool = ThreadPoolExecutor(max_workers=1)


class _Counting:
    plugin_id = "counting"
    contract = MemoryPluginContract(description="Test memory plugin.")

    def __init__(self) -> None:
        self.frames = 0

    def update(self, context) -> None:
        self.frames += 1

    def reset(self, shared_memory) -> None:
        pass


class UnchangedInstanceTests(unittest.TestCase):
    def changed(self, change) -> str:
        plugin = _Plugin()
        with self.assertRaises(RetainedStateError) as raised, unchanged_instance(plugin):
            change(plugin)
        return str(raised.exception)

    def test_rebound_added_removed_and_nested_changes_are_named(self) -> None:
        self.assertIn("changed gain on its instance", self.changed(lambda plugin: setattr(plugin, "gain", 1.0)))
        self.assertIn("changed last", self.changed(lambda plugin: setattr(plugin, "last", None)))
        self.assertIn("changed config", self.changed(lambda plugin: delattr(plugin, "config")))
        self.assertIn("changed config", self.changed(lambda plugin: plugin.config["limits"].append(3)))

    def test_work_inside_a_resource_and_state_put_back_pass(self) -> None:
        plugin = _Plugin()
        with unchanged_instance(plugin):
            plugin.pool.submit(int).result()
            plugin.frame = "scratch"
            del plugin.frame

    def test_a_raising_call_keeps_its_own_error(self) -> None:
        plugin = _Plugin()
        with self.assertRaisesRegex(ValueError, "plugin failed"), unchanged_instance(plugin):
            plugin.gain = 1.0
            raise ValueError("plugin failed")

    def test_a_runner_fails_the_plugin_call_and_records_the_error(self) -> None:
        runner = MemoryRunner.from_plugins({"counting": _Counting()})
        with self.assertRaisesRegex(RetainedStateError, "changed frames"):
            runner.update(DecisionFrameContext("frame_0", 0, 100, shared_memory={}))
        (plugin,) = runner.status()["plugins"]
        self.assertIn("RetainedStateError", plugin["last_error"])
        self.assertIn("changed frames", plugin["last_error"])


if __name__ == "__main__":
    unittest.main()
