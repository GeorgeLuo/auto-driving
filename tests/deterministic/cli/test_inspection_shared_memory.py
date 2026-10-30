"""Inspect runs continue seeded history and record its original provenance."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from cli.automa_cli.app import build_parser
from cli.automa_cli.plugins.step_inspect import inspect_step
from cli.automa_cli.shared.inspection_runs import SharedMemorySeed
from cli.automa_cli.plugins.step_replay import StepReplay
from cli.automa_cli.workbench.source import normalize_image_file

KEY = "perception.sim_cardboard_permanence.history"
PLUGINS = ["sim_cardboard_obstacle", "sim_cardboard_permanence"]
LEDGER_KEY = "memory.bounded_evidence.ledger"
LEDGER_SPEC = "implementations.decision_cycle.memory.shared.evidence_ledger.ledger:EvidenceLedger"


def _identity(frame: dict) -> dict:
    found = [
        thing for thing in frame["record"]["things"]
        if str(thing.get("thing_id", "")).startswith("cardboard_object_")
    ]
    if len(found) != 1:
        raise AssertionError(found)
    return found[0]


class InspectionSharedMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "images"
        self.source.mkdir()
        rgb = np.empty((120, 160, 3), dtype=np.uint8)
        rgb[:50] = (215, 214, 211)
        rgb[50:] = (213, 197, 172)
        rgb[30:70, 40:100] = (158, 107, 56)
        self.image = self.source / "01.png"
        Image.fromarray(rgb).save(self.image)
        Image.fromarray(rgb).save(self.source / "02.png")
        self.seed_file = self.root / "seed.json"
        self.document = {
            "schema": "shared_memory_v1",
            "values": {KEY: {
                "objects": [{"object_id": 7, "bbox_xyxy_norm": [0.25, 0.25, 0.62, 0.58],
                             "seen_frames": 3, "missed_frames": 0}],
                "next_object_id": 8,
            }},
        }
        self.write_seed()
        self.record_root = self.root / "recordings"
        self.enterContext(patch("cli.automa_cli.plugins.step_inspect.inspect_root", return_value=self.record_root))

    def write_seed(self) -> None:
        self.seed_file.write_text(json.dumps(self.document), encoding="utf-8")

    def report(self, result) -> dict:
        self.assertEqual(result.exit_code, 0, result.message)
        return json.loads(result.message)

    def assert_current_pair(self, frame: dict, object_id: int, event: str) -> None:
        things = frame["record"]["things"]
        self.assertEqual(len(things), 2)
        detection, mapped = things
        self.assertEqual(detection["source_plugin_id"], "sim_cardboard_obstacle")
        self.assertEqual(mapped["source_plugin_id"], "sim_cardboard_permanence")
        self.assertEqual(mapped["location"], detection["location"])
        self.assertEqual(mapped["properties"]["object_id"], object_id)
        self.assertEqual(mapped["properties"]["event"], event)

    def test_perception_inspect_continues_a_seeded_object(self) -> None:
        seeded = self.report(inspect_step(
            "perception", source=self.image, plugins=PLUGINS,
            shared_memory_file=self.seed_file, json_output=True, record=True,
        ))
        plain = self.report(inspect_step(
            "perception", source=self.image, plugins=PLUGINS, json_output=True,
        ))
        continued = _identity(seeded["frames"][0])
        self.assert_current_pair(seeded["frames"][0], 7, "matched")
        self.assertEqual(continued["thing_id"], "cardboard_object_007")
        self.assertEqual(continued["properties"]["event"], "matched")
        self.assertEqual(continued["properties"]["seen_frames"], 4)
        fresh = _identity(plain["frames"][0])
        self.assertEqual(fresh["thing_id"], "cardboard_object_001")
        self.assertEqual(fresh["properties"]["event"], "new")
        self.assertNotIn("shared_memory_seed", plain)
        self.assertEqual(seeded["shared_memory_seed"]["document"], self.document)

        # Replaying the recording starts fresh; its seed is provenance only.
        replayed = self.report(inspect_step("perception", source=Path(seeded["run_dir"]), json_output=True))
        self.assertEqual(_identity(replayed["frames"][0])["thing_id"], "cardboard_object_001")
        self.assertNotIn("shared_memory_seed", replayed)

    def test_a_far_seed_does_not_draw_a_stale_box_or_claim_a_known_match(self) -> None:
        self.document["values"][KEY]["objects"][0]["bbox_xyxy_norm"] = [0.8, 0.25, 0.98, 0.58]
        self.write_seed()
        report = self.report(inspect_step(
            "perception", source=self.image, plugins=PLUGINS,
            shared_memory_file=self.seed_file, json_output=True,
        ))
        self.assert_current_pair(report["frames"][0], 8, "new")

    def test_all_visible_obstacles_receive_distinct_seeded_identities(self) -> None:
        boxes = [(10, 30, 35, 70), (65, 40, 90, 75), (120, 50, 145, 80)]
        rgb = np.empty((120, 160, 3), dtype=np.uint8)
        rgb[:] = (213, 197, 172)
        objects = []
        for object_id, (x1, y1, x2, y2) in zip((7, 19, 23), boxes):
            rgb[y1:y2, x1:x2] = (158, 107, 56)
            objects.append({
                "object_id": object_id,
                "bbox_xyxy_norm": [x1 / 159, y1 / 119, (x2 - 1) / 159, (y2 - 1) / 119],
                "seen_frames": 3, "missed_frames": 0,
            })
        Image.fromarray(rgb).save(self.image)
        self.document["values"][KEY] = {"objects": list(reversed(objects)), "next_object_id": 24}
        self.write_seed()
        report = self.report(inspect_step(
            "perception", source=self.image, plugins=PLUGINS, shared_memory_file=self.seed_file, json_output=True,
        ))
        things = report["frames"][0]["record"]["things"]
        detections = [thing for thing in things if thing["source_plugin_id"] == PLUGINS[0]]
        identities = [thing for thing in things if thing["source_plugin_id"] == PLUGINS[1]]
        self.assertEqual(len(detections), 3)
        self.assertEqual(len(identities), 3)
        self.assertEqual({thing["properties"]["object_id"] for thing in identities}, {7, 19, 23})
        self.assertEqual([thing["location"] for thing in detections], [thing["location"] for thing in identities])
        self.assertTrue(all(thing["properties"]["event"] == "matched" for thing in identities))
        self.assertTrue(all(thing["properties"]["seen_frames"] == 4 for thing in identities))

    def test_the_seeded_identity_returns_after_a_gap_at_the_current_detection(self) -> None:
        Image.new("RGB", (160, 120), (213, 197, 172)).save(self.source / "02.png")
        rgb = np.empty((120, 160, 3), dtype=np.uint8)
        rgb[:] = (213, 197, 172)
        rgb[30:70, 44:104] = (158, 107, 56)
        Image.fromarray(rgb).save(self.source / "03.png")
        report = self.report(inspect_step(
            "perception", source=self.source, plugins=PLUGINS,
            shared_memory_file=self.seed_file, json_output=True,
        ))
        first, gap, returned = report["frames"]
        self.assert_current_pair(first, 7, "matched")
        self.assertEqual(gap["record"]["things"], [])
        self.assert_current_pair(returned, 7, "matched")
        self.assertEqual(_identity(returned)["properties"]["seen_frames"], 5)
        self.assertNotEqual(
            _identity(first)["location"]["bbox_xyxy_norm"],
            _identity(returned)["location"]["bbox_xyxy_norm"],
        )

    def test_memory_inspect_starts_from_a_seeded_ledger(self) -> None:
        self.document["values"] = {LEDGER_KEY: {
            "$record": LEDGER_SPEC,
            "data": {"memory_id": "memory-seeded", "epoch_id": "seeded", "bounds": {"max_records": 8}},
        }}
        self.write_seed()
        report = self.report(inspect_step(
            "memory", source=str(self.source), plugins=["bounded_evidence"],
            shared_memory_file=self.seed_file, json_output=True,
        ))
        self.assertEqual(len(report["frames"]), 2)
        self.assertEqual(report["frames"][0]["record"]["plugins"][0]["state"]["epoch_id"], "seeded")
        self.assertEqual(report["shared_memory_seed"]["document"], self.document)
        # The final status is suitable as the next ledger record's data.
        self.document["values"][LEDGER_KEY]["data"] = report["frames"][-1]["record"]["plugins"][0]["state"]
        self.write_seed()
        continued = self.report(inspect_step(
            "memory", source=str(self.image), plugins=["bounded_evidence"],
            shared_memory_file=self.seed_file, json_output=True,
        ))
        self.assertEqual(continued["frames"][0]["record"]["plugins"][0]["state"]["epoch_id"], "seeded")

    def test_a_recorded_step_inspect_keeps_the_seed_as_provenance(self) -> None:
        report = self.report(inspect_step(
            "proposal", source=self.image, shared_memory_file=self.seed_file,
            record=True, json_output=True,
        ))
        saved = json.loads((Path(report["run_dir"]) / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["shared_memory_seed"], {"path": str(self.seed_file), "document": self.document})

    def test_an_unreadable_seed_exits_2_before_recording(self) -> None:
        self.document["values"] = {"bad": {"$record": "bad"}}
        self.write_seed()
        calls = (
            lambda: inspect_step("perception", source=self.image, shared_memory_file=self.seed_file, record=True),
            lambda: inspect_step("memory", source=str(self.image), shared_memory_file=self.seed_file, record=True),
            lambda: inspect_step("proposal", source=self.image, shared_memory_file=self.seed_file, record=True),
        )
        for call in calls:
            with self.subTest(call=call):
                result = call()
                self.assertEqual(result.exit_code, 2)
                self.assertIn("Could not read shared memory file", result.message)
                self.assertIn(str(self.seed_file), result.message)
                self.assertFalse(self.record_root.exists())

    def test_every_inspect_parser_takes_a_seed_file(self) -> None:
        parser = build_parser()
        for step in ("perception", "memory", "proposal", "plan", "action"):
            with self.subTest(step=step):
                args = parser.parse_args([
                    "plugins", "inspect", step, str(self.image),
                    "--shared-memory", str(self.seed_file),
                ])
                self.assertEqual(args.shared_memory_file, self.seed_file)

    def test_a_replay_uses_the_seeded_map_even_when_it_is_empty(self) -> None:
        seed = SharedMemorySeed(self.seed_file, self.document, {})
        replay = StepReplay(normalize_image_file(self.image), steps=("perception",), seed=seed)
        self.assertIs(replay.shared_memory, seed.shared_memory)
