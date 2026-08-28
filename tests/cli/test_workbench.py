from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

from autonomy.perception import (
    PERCEPTION_TEXT_SCHEMA,
    PerceptionText,
    PerceptionSignal,
    PerceivedThing,
    ViewLocation,
)
from cli.automa_cli.workbench import (
    ImageReplayRunner,
    ReplayActionError,
    SourceValidationError,
    WorkbenchServer,
    normalize_image_directory,
)
from tests.support.cli_runner import run_automa


class FixtureMapper:
    plugin_id = "fixture_mapper"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.reset_count = 0

    def reset(self) -> None:
        self.reset_count += 1

    def describe_schema(self) -> dict[str, object]:
        return {"plugin_id": self.plugin_id}

    def perceive(self, request) -> PerceptionText:
        reading = request.sensor("front_camera")
        path = reading.path if reading is not None else ""
        self.calls.append(str(path))
        return PerceptionText(
            schema=PERCEPTION_TEXT_SCHEMA,
            plugin_id=self.plugin_id,
            status="ok",
            lines=("fixture evidence",),
            signals=(
                PerceptionSignal(
                    signal_id="fixture_visible",
                    value=True,
                    source_plugin_id=self.plugin_id,
                ),
            ),
            things=(
                PerceivedThing(
                    thing_id="fixture_thing",
                    kind="fixture",
                    label="fixture",
                    location=ViewLocation(
                        frame="image",
                        zone="center",
                        bbox_xyxy_norm=(0.1, 0.1, 0.4, 0.4),
                    ),
                    confidence=0.9,
                    source_plugin_id=self.plugin_id,
                ),
            ),
        )


def _make_images(root: Path, count: int = 3) -> None:
    for index in range(count):
        Image.new("RGB", (40, 30), (20 + index, 35, 50)).save(
            root / f"frame_{index:02d}.png"
        )


def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true before timeout")


class WorkbenchTests(unittest.TestCase):
    def test_directory_adapter_honors_manifest_order_and_absence(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 2)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "source_id": "fixture.sequence",
                        "frames": [
                            {
                                "frame_id": "capture-b",
                                "frame_index": 5,
                                "timestamp_ms": 500,
                                "image_path": "frame_01.png",
                            },
                            {
                                "frame_id": "capture-dropout",
                                "frame_index": 6,
                                "timestamp_ms": 600,
                                "absent": True,
                                "absence_reason": "camera dropout",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            feed = normalize_image_directory(root)

        self.assertEqual(feed.source_id, "fixture.sequence")
        self.assertEqual([frame.frame_id for frame in feed.frames], ["capture-b", "capture-dropout"])
        self.assertEqual(feed.frames[0].image_path.name, "frame_01.png")
        self.assertTrue(feed.frames[1].absent)
        self.assertEqual(feed.frames[1].absence_reason, "camera dropout")

    def test_directory_adapter_rejects_traversal_duplicate_and_unsupported_inputs(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 1)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "frames": [
                            {"frame_id": "same", "image_path": "frame_00.png"},
                            {"frame_id": "same", "image_path": "frame_00.png"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(SourceValidationError):
                normalize_image_directory(root)

            (root / "manifest.json").write_text(
                json.dumps({"frames": [{"image_path": "../outside.png"}]}),
                encoding="utf-8",
            )
            with self.assertRaises(SourceValidationError):
                normalize_image_directory(root)

            (root / "manifest.json").unlink()
            (root / "bad.gif").write_bytes(b"not an image")
            with self.assertRaises(SourceValidationError):
                normalize_image_directory(root)

    def test_runner_uses_existing_pipeline_and_reports_memory_effects(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 2)
            mapper = FixtureMapper()
            runner = ImageReplayRunner(
                root,
                cadence_ms=0,
                mapper_factory=lambda: mapper,
            )
            runner.start()
            state = runner.wait(5)

        self.assertEqual(state["phase"], "completed")
        self.assertEqual(state["sequence_id"], "workbench.image_replay.v1")
        self.assertEqual(state["progress"]["completed"], 2)
        self.assertEqual(len(mapper.calls), 2)
        self.assertEqual(state["observation"]["metadata"]["source"], "workbench.image_replay.v1")
        self.assertEqual(state["memory"]["health"], "healthy")
        self.assertGreaterEqual(state["memory"]["record_count"], 2)
        self.assertTrue(state["timeline"][0]["memory_effect"]["added"])
        self.assertTrue(state["cleanup"]["source_read_only"])
        self.assertFalse(state["cleanup"]["movement_control"])
        self.assertFalse(state["machine_detail"]["side_effects"]["simulator"])

    def test_absence_does_not_invoke_perception_or_fabricate_image(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 1)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "frames": [
                            {"frame_id": "present", "image_path": "frame_00.png"},
                            {"frame_id": "absent", "absent": True, "absence_reason": "dropout"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            mapper = FixtureMapper()
            runner = ImageReplayRunner(
                root,
                cadence_ms=0,
                mapper_factory=lambda: mapper,
            )
            runner.start()
            state = runner.wait(5)

        self.assertEqual(state["phase"], "completed")
        self.assertEqual(len(mapper.calls), 1)
        self.assertTrue(state["timeline"][1]["frame"]["absent"])
        self.assertEqual(state["observation"]["metadata"]["absence_reason"], "dropout")

    def test_pause_resume_step_reset_and_stale_run_are_server_owned(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 3)
            runner = ImageReplayRunner(root, cadence_ms=1000)
            first = runner.start()
            run_id = first["run_id"]
            _wait_until(lambda: len(runner.state()["timeline"]) >= 1)
            paused = runner.dispatch("pause", run_id=run_id)
            self.assertEqual(paused["phase"], "paused")
            with self.assertRaises(ReplayActionError):
                runner.dispatch("pause", run_id="stale-run")
            stepped = runner.dispatch("step", run_id=run_id)
            self.assertEqual(stepped["phase"], "paused")
            self.assertEqual(len(stepped["timeline"]), 2)
            runner.dispatch("resume", run_id=run_id)
            completed = runner.wait(5)
            self.assertEqual(completed["phase"], "completed")
            reset = runner.dispatch("reset")

        self.assertEqual(reset["phase"], "idle")
        self.assertIsNone(reset["run_id"])
        self.assertEqual(reset["progress"]["completed"], 0)

    def test_loopback_api_persists_after_terminal_state_and_rejects_raw_argv(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 1)
            runner = ImageReplayRunner(cadence_ms=0)
            server = WorkbenchServer(runner).start()
            self.addCleanup(server.stop)
            base = server.url
            self.assertIsNotNone(base)

            html = urlopen(base, timeout=2).read().decode("utf-8")
            self.assertIn("Perception-memory Workbench", html)
            self.assertIn('id="stepButton"', html)
            self.assertIn('id="memorySelected"', html)

            start_body = json.dumps(
                {"action": "start", "source_dir": str(root), "cadence_ms": 0}
            ).encode("utf-8")
            start = json.loads(
                urlopen(
                    Request(
                        base + "api/action",
                        data=start_body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    timeout=2,
                ).read()
            )
            run_id = start["state"]["run_id"]
            state = runner.wait(5)
            self.assertEqual(state["phase"], "completed")

            health = json.loads(urlopen(base + "api/health", timeout=2).read())
            self.assertTrue(health["available"])
            self.assertTrue(health["persistent_across_terminal_state"])
            latest = json.loads(urlopen(base + "api/state", timeout=2).read())
            self.assertEqual(latest["phase"], "completed")
            frame = urlopen(
                base + "api/frame?run_id=" + run_id + "&frame_id=" + state["timeline"][0]["frame"]["frame_id"],
                timeout=2,
            )
            self.assertEqual(frame.status, 200)
            self.assertTrue(frame.read())

            second_start_body = json.dumps(
                {"action": "start", "source_dir": str(root), "cadence_ms": 0}
            ).encode("utf-8")
            second_start = json.loads(
                urlopen(
                    Request(
                        base + "api/action",
                        data=second_start_body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    timeout=2,
                ).read()
            )
            self.assertNotEqual(second_start["state"]["run_id"], run_id)
            self.assertEqual(runner.wait(5)["phase"], "completed")

            bad_body = json.dumps({"action": "start", "argv": ["--unsafe"]}).encode("utf-8")
            with self.assertRaises(HTTPError) as error:
                urlopen(
                    Request(
                        base + "api/action",
                        data=bad_body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    timeout=2,
                )
            self.assertEqual(error.exception.code, 400)
            bad_payload = json.loads(error.exception.read())
            self.assertEqual(bad_payload["boundary"], "input")

    def test_cli_replay_machine_readable_boundary(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 1)
            result = run_automa(
                "vehicles",
                "workbench",
                "replay",
                str(root),
                "--cadence-ms",
                "0",
                "--json",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["phase"], "completed")
        self.assertEqual(payload["sequence_id"], "workbench.image_replay.v1")
        self.assertNotIn("argv", payload)


if __name__ == "__main__":
    unittest.main()
