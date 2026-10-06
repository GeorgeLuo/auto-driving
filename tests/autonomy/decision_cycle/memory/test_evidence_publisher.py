"""The memory runner names the plugin whose value ``EVIDENCE_KEY`` holds."""

from __future__ import annotations

import unittest

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionSteps
from autonomy.decision_cycle.memory.publication import EVIDENCE_KEY
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.decision_cycle.observation.values import Observation
from autonomy.plugins import PluginManager
from autonomy.runtime.cycle_host import AutonomyCycleHost
from tests.autonomy.decision_cycle.memory.activation_fixtures import (
    RECORDING_SPEC,
    _RecordingMemory,
)


class _EvidenceMemory(_RecordingMemory):
    """Publishes its records at ``EVIDENCE_KEY``, as a retained-evidence plugin does."""

    publish = True

    def update(self, context, observation):
        super().update(context, observation)
        if self.publish:
            context.shared_memory[EVIDENCE_KEY] = self.state(context.shared_memory)["records"]

    def reset(self, shared_memory):
        super().reset(shared_memory)
        shared_memory[EVIDENCE_KEY] = ()


class _PassThroughMemory(_RecordingMemory):
    """Stores back the object already at ``EVIDENCE_KEY``."""

    def update(self, context, observation):
        super().update(context, observation)
        if EVIDENCE_KEY in context.shared_memory:
            context.shared_memory[EVIDENCE_KEY] = context.shared_memory[EVIDENCE_KEY]


class _RemovingMemory(_RecordingMemory):
    def update(self, context, observation):
        super().update(context, observation)
        context.shared_memory.pop(EVIDENCE_KEY, None)


class _FailingEvidenceMemory(_EvidenceMemory):
    """Publishes, then raises."""

    def update(self, context, observation):
        super().update(context, observation)
        raise RuntimeError("after publishing")


EVIDENCE_SPEC = "tests.autonomy.decision_cycle.memory.test_evidence_publisher:_EvidenceMemory"


def _observation(frame_id: str) -> Observation:
    return Observation(
        observation_id=frame_id,
        created_at_ms=100,
        sensor_frame={},
        perception_plugin_id="test",
        summary=(),
    )


def _context(frame_id: str, shared_memory: dict) -> DecisionFrameContext:
    return DecisionFrameContext(frame_id, 0, 100, shared_memory=shared_memory)


def _runner(*plugins: _RecordingMemory) -> MemoryRunner:
    return MemoryRunner.from_plugins({plugin.plugin_id: plugin for plugin in plugins})


def _update(runner: MemoryRunner, shared_memory: dict, frame_id: str = "f1") -> dict:
    return runner.update(_context(frame_id, shared_memory), _observation(frame_id))


class EvidencePublisherTests(unittest.TestCase):
    def test_the_publisher_is_named_in_either_selection_order(self) -> None:
        for order in (("recording_test", "evidence"), ("evidence", "recording_test")):
            with self.subTest(order=order):
                plugins = {
                    "recording_test": _RecordingMemory(),
                    "evidence": _EvidenceMemory(plugin_id="evidence"),
                }
                runner = _runner(*(plugins[plugin_id] for plugin_id in order))

                report = _update(runner, {})

                self.assertEqual([entry["plugin_id"] for entry in report["plugins"]], list(order))
                self.assertEqual(report["evidence_publisher"], "evidence")
                self.assertEqual(runner.status()["evidence_publisher"], "evidence")

    def test_no_publisher_before_any_plugin_writes_the_key(self) -> None:
        runner = _runner(_RecordingMemory())

        self.assertIsNone(runner.report()["evidence_publisher"])
        self.assertIsNone(_update(runner, {})["evidence_publisher"])

    def test_the_later_of_two_publishers_is_named(self) -> None:
        runner = _runner(
            _EvidenceMemory(plugin_id="first"), _EvidenceMemory(plugin_id="second"),
        )

        self.assertEqual(_update(runner, {})["evidence_publisher"], "second")

    def test_storing_the_object_already_there_keeps_the_publisher(self) -> None:
        runner = _runner(_EvidenceMemory(plugin_id="evidence"), _PassThroughMemory(plugin_id="copy"))

        self.assertEqual(_update(runner, {})["evidence_publisher"], "evidence")

    def test_a_cycle_that_leaves_the_key_alone_keeps_the_publisher(self) -> None:
        evidence = _EvidenceMemory(plugin_id="evidence")
        runner = _runner(evidence, _RecordingMemory())
        shared: dict = {}
        _update(runner, shared, "f1")

        evidence.publish = False

        self.assertEqual(_update(runner, shared, "f2")["evidence_publisher"], "evidence")

    def test_a_plugin_that_removes_the_key_clears_the_publisher(self) -> None:
        runner = _runner(_EvidenceMemory(plugin_id="evidence"), _RemovingMemory(plugin_id="remover"))

        self.assertIsNone(_update(runner, {})["evidence_publisher"])

    def test_a_plugin_that_raises_after_writing_is_the_publisher(self) -> None:
        runner = _runner(
            _EvidenceMemory(plugin_id="evidence"), _FailingEvidenceMemory(plugin_id="failing"),
        )

        with self.assertRaises(RuntimeError):
            _update(runner, {})
        self.assertEqual(runner.report()["evidence_publisher"], "failing")

    def test_a_value_no_plugin_left_has_no_publisher(self) -> None:
        shared: dict = {}
        runner = _runner(_EvidenceMemory(plugin_id="evidence"))
        _update(runner, shared)

        shared[EVIDENCE_KEY] = ("written elsewhere",)
        self.assertIsNone(runner.report()["evidence_publisher"])

        shared.clear()
        self.assertIsNone(runner.report()["evidence_publisher"])
        self.assertIsNone(runner.status()["evidence_publisher"])

    def test_host_reset_names_the_publisher_only_when_the_key_is_kept(self) -> None:
        runner = _runner(_RecordingMemory(), _EvidenceMemory(plugin_id="evidence"))
        host = AutonomyCycleHost(steps=DecisionSteps(memory=runner))
        _update(runner, host.shared_memory)
        self.assertTrue(host.shared_memory[EVIDENCE_KEY])

        report = host.reset_memory()

        # The reset replaced the records with a new empty tuple, which the host keeps.
        self.assertEqual(host.shared_memory[EVIDENCE_KEY], ())
        self.assertEqual(report["evidence_publisher"], "evidence")
        self.assertEqual([entry["plugin_id"] for entry in report["plugins"]], ["recording_test", "evidence"])

        report = host.reset_memory()

        # The second reset stored the same empty tuple, so the host dropped the key.
        self.assertNotIn(EVIDENCE_KEY, host.shared_memory)
        self.assertIsNone(report["evidence_publisher"])

    def test_the_publisher_leaving_the_selection_clears_it(self) -> None:
        manager = PluginManager.from_specs(
            "memory",
            {"recording_test": RECORDING_SPEC, "evidence": EVIDENCE_SPEC},
            {"evidence": {"plugin_id": "evidence"}},
        )
        manager.select(("recording_test", "evidence"))
        runner = MemoryRunner(manager)
        shared: dict = {}
        self.assertEqual(_update(runner, shared)["evidence_publisher"], "evidence")

        manager.select(("recording_test",))
        report = _update(runner, shared, "f2")

        self.assertEqual([entry["plugin_id"] for entry in report["plugins"]], ["recording_test"])
        self.assertIn(EVIDENCE_KEY, shared)
        self.assertIsNone(report["evidence_publisher"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
