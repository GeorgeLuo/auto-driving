from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from autonomy.decision_cycle.memory.publication import OBSERVATION_KEY
from cli.automa_cli import memory
from cli.automa_cli.memory import inspect_memory
from tests.support.cli_runner import run_automa

SUMMARY_KEYS = {"plugin_id", "health", "record_count", "epoch_id"}


def write_frames(root: Path, count: int = 3) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        image = np.full((240, 320, 3), 140, np.uint8)
        left = 60 + 15 * index
        cv2.rectangle(image, (left, 90), (left + 80, 200), (30, 30, 200), -1)
        cv2.imwrite(str(root / f"frame_{index}.png"), image)
    return root


class StubMemory:
    """A memory step that publishes a replacement observation with no things."""

    def __call__(self, context, observation):
        context.shared_memory[OBSERVATION_KEY] = replace(observation, things=())
        return self.report()

    def report(self):
        state = {"health": "healthy", "record_count": 1, "epoch_id": "epoch-stub"}
        return {"schema": "memory_report_v0", "plugins": [{"plugin_id": "stub", "state": state}]}


class MemoryInspectTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.frames = write_frames(self.tmp / "frames")
        self.root = self.tmp / "inspections"
        patcher = patch.object(memory, "INSPECT_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def inspect(self, source=None, **options) -> dict:
        result = inspect_memory(str(source or self.frames), json_output=True, **options)
        self.assertEqual(result.exit_code, 0, result.message)
        return json.loads(result.message)

    def test_help_lists_inspect_and_not_replay(self) -> None:
        listing = run_automa("vehicles", "memory", "help").stdout
        self.assertIn("inspect", listing)
        for removed in ("replay", "enable", "disable"):
            self.assertNotIn(removed, listing)
        flags = run_automa("vehicles", "memory", "inspect", "--help").stdout
        for flag in ("--preset", "--plugin", "--record", "--json"):
            self.assertIn(flag, flags)
        for live in ("--id", "--frames", "--interval-s", "--timeout-s"):
            self.assertNotIn(live, flags)

    def test_reports_each_frames_summary_in_source_order(self) -> None:
        report = self.inspect()
        self.assertEqual(report["schema"], "memory_inspect_v0")
        self.assertEqual(report["source"]["frame_count"], 3)
        self.assertEqual([item["frame_index"] for item in report["frames"]], [0, 1, 2])
        for item in report["frames"]:
            self.assertEqual({key for plugin in item["plugins"] for key in plugin}, SUMMARY_KEYS)
        last = report["frames"][-1]["plugins"][-1]
        final = report["final"]["plugins"][-1]["state"]
        self.assertEqual(
            (last["health"], last["record_count"], last["epoch_id"]),
            (final["health"], final["record_count"], final["epoch_id"]),
        )

    def test_shows_every_plugins_summary(self) -> None:
        report = self.inspect(plugins=["bounded_evidence", "multi_obstruction_tracks"])
        self.assertEqual(report["memory"]["plugins"], ["bounded_evidence", "multi_obstruction_tracks"])
        for item in report["frames"]:
            self.assertEqual(
                [plugin["plugin_id"] for plugin in item["plugins"]],
                ["bounded_evidence", "multi_obstruction_tracks"],
            )
        self.assertEqual(len(report["final"]["plugins"]), 2)

    def test_reports_the_replacement_observation_next_to_the_frames_own(self) -> None:
        plain = self.inspect()
        self.assertIsNone(plain["frames"][0]["replacement"])
        with patch.object(memory.MemoryRunner, "from_activation", return_value=StubMemory()):
            report = self.inspect()
        for item in report["frames"]:
            self.assertGreater(item["observation"]["things"], 0)
            self.assertEqual(item["replacement"]["things"], 0)
            self.assertEqual(item["replacement"]["observation_id"], item["observation"]["observation_id"])

    def test_frame_times_come_from_the_source_manifest(self) -> None:
        run = write_frames(self.tmp / "run", count=2)
        (run / "manifest.json").write_text(
            json.dumps(
                {
                    "frames": [
                        {"image_path": "frame_0.png", "timestamp_ms": 5000},
                        {"image_path": "frame_1.png", "timestamp_ms": 5400},
                    ]
                }
            ),
            encoding="utf-8",
        )
        report = self.inspect(run)
        self.assertEqual([item["timestamp_ms"] for item in report["frames"]], [5000, 5400])

    def test_a_single_image_is_a_one_frame_source(self) -> None:
        report = self.inspect(self.frames / "frame_0.png")
        self.assertEqual(report["source"]["frame_count"], 1)
        self.assertEqual(len(report["frames"]), 1)

    def test_without_record_writes_nothing(self) -> None:
        report = self.inspect()
        self.assertIsNone(report["run_dir"])
        self.assertFalse(self.root.exists())

    def test_record_writes_the_report_and_source_frames_only(self) -> None:
        report = self.inspect(record=True)
        run_dir = Path(report["run_dir"])
        self.assertEqual(run_dir.parent, self.root)
        written = sorted(path.relative_to(run_dir).as_posix() for path in run_dir.rglob("*") if path.is_file())
        self.assertEqual(
            written,
            ["frames/frame_000000.png", "frames/frame_000001.png", "frames/frame_000002.png", "report.json"],
        )
        self.assertEqual(json.loads((run_dir / "report.json").read_text()), report)

    def test_rejects_bad_selections_and_sources(self) -> None:
        for options in ({"preset": "nope"}, {"preset": "recency_ledger", "plugins": ["bounded_evidence"]}):
            with self.subTest(options=options):
                result = inspect_memory(str(self.frames), **options)
                self.assertEqual(result.exit_code, 2)
        missing = inspect_memory(str(self.tmp / "missing"))
        self.assertEqual(missing.exit_code, 2)
        self.assertIn("does not exist", missing.message)
        empty = self.tmp / "empty"
        empty.mkdir()
        self.assertEqual(inspect_memory(str(empty)).exit_code, 2)
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
