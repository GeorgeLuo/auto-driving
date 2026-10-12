from __future__ import annotations

import json
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path

from autonomy.runtime.client import RuntimeClient
from autonomy.runtime.session import RunConfiguration
from cli.automa_cli.bundles import controller_bundle_paths
from cli.automa_cli.streaming import probe_live_memory
from implementations.decision_cycle.catalog import packaged_activation
from tests.shared.cli.automation_pipeline.pipeline_fixtures import (
    VEHICLE_ID,
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
    bundle = controller_bundle_paths(runtime_root / VEHICLE_ID)
    _write_activations(bundle)
    path = Path(bundle["memory_runtime_dir"]) / "active.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(packaged_activation("memory", ["bounded_evidence"]).to_payload()),
        encoding="utf-8",
    )


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
        self.client.start(RunConfiguration(mode=mode, interval_s=0.1, num_decisions=0))
        self.addCleanup(self.client.stop)
        wait_until(
            lambda: any(
                plugin["record_count"]
                for plugin in probe_live_memory(vehicle_id=VEHICLE_ID)["plugins"]
            ),
            message="the session never filled memory",
        )
