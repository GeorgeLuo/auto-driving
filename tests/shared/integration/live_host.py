from __future__ import annotations

import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path

from autonomy.decision_cycle.activation import write_step_activation
from autonomy.runtime.client import RuntimeClient
from autonomy.runtime.session import RunConfiguration
from cli.automa_cli.bundles import controller_bundle_paths
from cli.automa_cli.paths import ROOT
from cli.automa_cli.step_activations import bundle_activation_path
from cli.automa_cli.streaming import probe_live_memory
from implementations.decision_cycle.catalog import packaged_activation
from tests.shared.cli.automation_pipeline.pipeline_fixtures import (
    HOST_ID,
    _write_activations,
    chase_runtime,
    staged_runners,
)


def wait_until(predicate, *, timeout_s: float = 10.0, message: str):
    deadline = time.monotonic() + timeout_s
    while not (value := predicate()):
        if time.monotonic() > deadline:
            raise AssertionError(message)
        time.sleep(0.05)
    return value


def stage_runtime(runtime_root: Path) -> None:
    """Stage the steps that have no built-in.

    The bundle is not an extracted release, so plan names the source tree that
    `hosts info plan` loads.
    """
    bundle = controller_bundle_paths(runtime_root / HOST_ID)
    _write_activations(bundle)
    for activation in (
        packaged_activation("memory", ["bounded_evidence"]),
        packaged_activation("plan", metadata={"controller_bundle": {"root_dir": str(ROOT)}}),
    ):
        write_step_activation(bundle_activation_path(bundle, activation.step), activation)


class LiveHostTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runtime_root = Path(tmp.name) / "vehicles"
        stage_runtime(self.runtime_root)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(staged_runners())
        self.host = stack.enter_context(chase_runtime(self.runtime_root))
        self.client = RuntimeClient(self.host.base_url, timeout_s=3.0)

    def observe(self, mode: str = "observe_only") -> None:
        self.client.start(RunConfiguration(mode=mode, num_decisions=0))
        self.addCleanup(self.client.stop)
        wait_until(
            lambda: any(
                plugin["record_count"]
                for plugin in probe_live_memory(host_id=HOST_ID)["plugins"]
            ),
            message="the session never filled memory",
        )
