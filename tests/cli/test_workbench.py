from __future__ import annotations

import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image

from autonomy.perception import (
    PERCEPTION_TEXT_SCHEMA,
    PerceptionText,
    PerceptionSignal,
    PerceivedThing,
    ViewLocation,
)
from autonomy.decision.memory import MemoryBounds, MemorySnapshot
from cli.automa_cli.workbench import (
    PluginCatalogError,
    ImageReplayRunner,
    ReplayActionError,
    SourceValidationError,
    WorkbenchServer,
    normalize_image_directory,
    discover_plugin_catalog,
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


class ErrorStatusMapper(FixtureMapper):
    def perceive(self, request) -> PerceptionText:
        self.calls.append("error")
        return PerceptionText(
            schema=PERCEPTION_TEXT_SCHEMA,
            plugin_id=self.plugin_id,
            status="error",
            lines=("mapper failed",),
            signals=(),
            things=(),
        )


class BlockingSecondMapper(FixtureMapper):
    def __init__(self) -> None:
        super().__init__()
        self.second_started = threading.Event()
        self.release_second = threading.Event()

    def perceive(self, request) -> PerceptionText:
        if len(self.calls) == 1:
            self.second_started.set()
            self.release_second.wait(3)
        return super().perceive(request)


class ErrorMemory:
    def __call__(self, context, observation) -> MemorySnapshot:
        return MemorySnapshot(
            memory_id="error-memory",
            epoch_id="epoch-1",
            health="error",
            bounds=MemoryBounds(max_records=4),
            created_at_ms=0,
            error="injected memory failure",
        )

    def reset(self) -> None:
        return None


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
    def test_workbench_keeps_plugin_checkbox_nodes_stable_between_state_polls(self) -> None:
        html = Path("cli/automa_cli/workbench.html").read_text(encoding="utf-8")
        render_plugins = html.split("function renderPlugins() {", 1)[1].split(
            "function renderControls() {", 1
        )[0]
        cache_index = render_plugins.index(
            "if (renderKey === pluginCatalogRenderKey) {"
        )
        clear_index = render_plugins.index('elements.pluginCatalog.textContent = "";')
        stable_branch = render_plugins[cache_index:clear_index]

        self.assertLess(cache_index, clear_index)
        self.assertIn(
            'elements.pluginCatalog.querySelectorAll("input[data-plugin-id]")',
            stable_branch,
        )
        self.assertIn(
            "input.disabled = !readyById[pluginId] || !selectionAllowed;",
            stable_branch,
        )
        self.assertIn("renderPluginSummary(catalog, plugins, active);", stable_branch)
        self.assertIn("return;", stable_branch)
        self.assertIn("pluginCatalogRenderKey = null;", html)

    def test_workbench_keeps_last_frame_visible_while_next_image_loads(self) -> None:
        html = Path("cli/automa_cli/workbench.html").read_text(encoding="utf-8")
        render_frame = html.split("function renderFrame() {", 1)[1].split(
            "function drawOverlay() {", 1
        )[0]
        image_change = render_frame.split("if (imageKey !== lastImageKey) {", 1)[1].split(
            "if (loadedImageKey !== imageKey) return;", 1
        )[0]

        self.assertIn('var hasRenderedImage = loadedImageKey !== "";', image_change)
        self.assertIn("var preloadedImage = new Image();", image_change)
        self.assertIn("elements.frameImage.replaceWith(preloadedImage);", image_change)
        self.assertIn("elements.overlayCanvas.width = 1;", image_change)
        loading_branch = image_change.split("if (!hasRenderedImage) {", 1)[1].split(
            "} else {", 1
        )[0]
        self.assertIn('elements.emptyState.textContent = "Loading frame "', loading_branch)
        self.assertNotIn('elements.emptyState.textContent = "Loading frame "', image_change.split(
            "} else {", 1
        )[1])
        self.assertIn("elements.viewerFrame.hidden = false;", image_change)
        self.assertIn("elements.emptyState.hidden = true;", image_change)

    def test_manifest_catalog_is_recursive_deterministic_and_explicit_about_readiness(self) -> None:
        catalog = discover_plugin_catalog(Path("lab/plugins/perception"))
        self.assertEqual(
            [item.plugin_id for item in catalog.plugins],
            ["classical_regions", "fastsam", "floor_continuity", "floor_continuity_capture"],
        )
        self.assertTrue(catalog.valid)
        self.assertTrue(catalog.plugins[0].ready)
        self.assertEqual(catalog.plugins[0].inputs[0]["name"], "frame")
        self.assertEqual(
            catalog.plugins[0].output["schema"],
            "perception_text_v2",
        )
        self.assertFalse(catalog.plugins[1].ready)
        self.assertIn("isolated runtime", catalog.plugins[1].unavailable_reason or "")
        self.assertEqual(catalog.digest, discover_plugin_catalog(Path("lab/plugins/perception")).digest)
        self.assertEqual(
            catalog.normalize_selection(["floor_continuity", "classical_regions"]),
            ("classical_regions", "floor_continuity"),
        )
        self.assertEqual(catalog.normalize_selection([]), ())

    def test_explicit_catalog_selection_runs_only_selected_plugins(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 1)
            runner = ImageReplayRunner(
                root,
                plugin_dir=Path("lab/plugins/perception"),
                active_plugin_ids=["classical_regions"],
                cadence_ms=0,
            )
            started = runner.start()
            state = runner.wait(10) if started["phase"] == "running" else started

        self.assertEqual(state["phase"], "completed")
        self.assertEqual(state["run_active_plugin_ids"], ["classical_regions"])
        self.assertEqual(state["active_plugin_ids"], ["classical_regions"])
        self.assertEqual(
            [run["plugin_id"] for run in state["perception"]["plugin_runs"]],
            ["classical_regions"],
        )
        self.assertNotEqual(
            state["machine_detail"]["pipeline"]["perception_algorithm"],
            "lightweight_observer",
        )

    def test_explicit_catalog_allows_raw_capture_and_live_replacement(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 3)
            runner = ImageReplayRunner(
                root,
                plugin_dir=Path("lab/plugins/perception"),
                cadence_ms=1000,
            )
            raw_started = runner.start()
            self.assertEqual(raw_started["phase"], "running")
            self.assertEqual(raw_started["run_active_plugin_ids"], [])
            raw_state = runner.wait(10)
            self.assertEqual(raw_state["phase"], "completed")
            self.assertEqual(raw_state["run_active_plugin_ids"], [])
            self.assertEqual(raw_state["active_plugin_ids"], [])
            self.assertEqual(raw_state["perception"]["status"], "empty")
            self.assertEqual(raw_state["perception"]["plugin_runs"], ())
            self.assertEqual(raw_state["perception"]["things"], ())

            runner.dispatch(
                "select_plugins",
                run_id=raw_started["run_id"],
                active_plugin_ids=["classical_regions"],
            )
            started = runner.start()
            run_id = started["run_id"]
            _wait_until(lambda: len(runner.state()["timeline"]) >= 1)
            selected = runner.dispatch(
                "select_plugins",
                run_id=run_id,
                active_plugin_ids=["floor_continuity"],
            )
            self.assertEqual(selected["phase"], "running")
            self.assertEqual(selected["run_active_plugin_ids"], ["floor_continuity"])
            paused = runner.dispatch("pause", run_id=run_id)
            self.assertEqual(paused["phase"], "paused")
            first_detail = runner.frame_detail(
                paused["timeline"][0]["frame"]["frame_id"], run_id=run_id
            )
            self.assertEqual(
                [run["plugin_id"] for run in first_detail["perception"]["plugin_runs"]],
                ["classical_regions"],
            )
            with self.assertRaises(ReplayActionError):
                runner.dispatch(
                    "select_plugins",
                    run_id=run_id,
                    active_plugin_ids=["unknown"],
                )
            self.assertEqual(runner.state()["run_active_plugin_ids"], ["floor_continuity"])
            stepped = runner.dispatch("step", run_id=run_id)
            second_detail = runner.frame_detail(
                stepped["timeline"][1]["frame"]["frame_id"], run_id=run_id
            )
            self.assertEqual(
                [run["plugin_id"] for run in second_detail["perception"]["plugin_runs"]],
                ["floor_continuity"],
            )
            runner.dispatch("cancel", run_id=run_id)

    def test_paused_plugin_toggle_reprocesses_current_frame_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 3)
            runner = ImageReplayRunner(
                root,
                plugin_dir=Path("lab/plugins/perception"),
                cadence_ms=5000,
            )
            runner.dispatch(
                "select_plugins",
                active_plugin_ids=["classical_regions"],
            )
            started = runner.start()
            run_id = started["run_id"]
            _wait_until(lambda: runner.state().get("current_frame") is not None)
            paused = runner.dispatch("pause", run_id=run_id)
            position = paused["position"]
            timeline_len = len(paused["timeline"])
            self.assertEqual(
                [run["plugin_id"] for run in paused["perception"]["plugin_runs"]],
                ["classical_regions"],
            )

            both = runner.dispatch(
                "select_plugins",
                run_id=run_id,
                active_plugin_ids=["classical_regions", "floor_continuity"],
            )
            self.assertEqual(both["phase"], "paused")
            self.assertEqual(both["position"], position)
            self.assertEqual(len(both["timeline"]), timeline_len)
            self.assertEqual(
                [run["plugin_id"] for run in both["perception"]["plugin_runs"]],
                ["classical_regions", "floor_continuity"],
            )

            none = runner.dispatch(
                "select_plugins",
                run_id=run_id,
                active_plugin_ids=[],
            )
            self.assertEqual(none["phase"], "paused")
            self.assertEqual(list(none["perception"]["plugin_runs"] or ()), [])

            one = runner.dispatch(
                "select_plugins",
                run_id=run_id,
                active_plugin_ids=["floor_continuity"],
            )
            self.assertEqual(one["phase"], "paused")
            self.assertEqual(one["position"], position)
            self.assertEqual(
                [run["plugin_id"] for run in one["perception"]["plugin_runs"]],
                ["floor_continuity"],
            )
            runner.dispatch("cancel", run_id=run_id)

    def test_catalog_rejects_unavailable_and_duplicate_selection(self) -> None:
        catalog = discover_plugin_catalog(Path("lab/plugins/perception"))
        with self.assertRaises(PluginCatalogError):
            catalog.normalize_selection(["fastsam"])
        with self.assertRaises(PluginCatalogError):
            catalog.normalize_selection(["classical_regions", "classical_regions"])

    def test_directory_adapter_honors_manifest_order_and_absence(self) -> None:
        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            root = workspace / "lab/plugins/perception/example/runs/fixture-run"
            image_root = workspace / "lab/runs/capture"
            root.mkdir(parents=True)
            image_root.mkdir(parents=True)
            _make_images(image_root, 2)
            (root / "run.json").write_text(
                json.dumps(
                    {
                        "source_id": "fixture.sequence",
                        "run_dir": "lab/plugins/perception/example/runs/fixture-run",
                        "source": {
                            "kind": "apply",
                            "path": "/previous/location/auto-driving/lab/runs/capture",
                        },
                        "frames": [
                            {
                                "frame_id": "capture-b",
                                "frame_index": 5,
                                "captured_at_ms": 500,
                                "image_path": (
                                    "/previous/location/auto-driving/lab/runs/capture/"
                                    "frame_01.png"
                                ),
                            },
                            {
                                "frame_id": "capture-dropout",
                                "frame_index": 6,
                                "captured_at_ms": 600,
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

    def test_directory_adapter_rejects_empty_over_limit_and_nonincreasing_sources(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(SourceValidationError):
                normalize_image_directory(root)

            _make_images(root, 3)
            with self.assertRaises(SourceValidationError):
                normalize_image_directory(root, max_frames=2)
            with self.assertRaises(SourceValidationError):
                normalize_image_directory(root, max_image_bytes=10)

            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "frames": [
                            {
                                "frame_id": "later",
                                "frame_index": 2,
                                "timestamp_ms": 20,
                                "image_path": "frame_00.png",
                            },
                            {
                                "frame_id": "earlier",
                                "frame_index": 1,
                                "timestamp_ms": 30,
                                "image_path": "frame_01.png",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(SourceValidationError):
                normalize_image_directory(root)

            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "frames": [
                            {
                                "frame_id": "first",
                                "frame_index": 1,
                                "timestamp_ms": 40,
                                "image_path": "frame_00.png",
                            },
                            {
                                "frame_id": "second",
                                "frame_index": 2,
                                "timestamp_ms": 40,
                                "image_path": "frame_01.png",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(SourceValidationError):
                normalize_image_directory(root)

    def test_runner_refuses_invalid_and_undecodable_sources_before_pipeline(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            broken = Path(directory) / "broken"
            broken.mkdir()
            (broken / "broken.png").write_bytes(b"not an image")
            broken_mapper = FixtureMapper()
            broken_state = ImageReplayRunner(
                broken,
                cadence_ms=0,
                mapper_factory=lambda: broken_mapper,
            ).start()
            self.assertEqual(broken_state["phase"], "failed")
            self.assertEqual(broken_state["failure_boundary"], "source")
            self.assertEqual(broken_mapper.calls, [])
            self.assertIsNone(broken_state["perception"])

    def test_runner_fails_closed_on_mapper_and_memory_errors(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 1)
            error_mapper = ErrorStatusMapper()
            runner = ImageReplayRunner(
                root,
                cadence_ms=0,
                mapper_factory=lambda: error_mapper,
            )
            started = runner.start()
            state = runner.wait(5) if started["phase"] == "running" else started
            self.assertEqual(state["phase"], "failed")
            self.assertEqual(state["failure_boundary"], "perception")
            self.assertEqual(state["perception"]["status"], "error")

            memory_mapper = FixtureMapper()
            memory_runner = ImageReplayRunner(
                root,
                cadence_ms=0,
                mapper_factory=lambda: memory_mapper,
                memory_stage_factory=lambda: ErrorMemory(),
            )
            memory_started = memory_runner.start()
            memory_state = (
                memory_runner.wait(5)
                if memory_started["phase"] == "running"
                else memory_started
            )
            self.assertEqual(memory_state["phase"], "failed")
            self.assertEqual(memory_state["failure_boundary"], "memory")
            self.assertEqual(memory_state["memory"]["health"], "error")

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
            first_frame_id = state["timeline"][0]["frame"]["frame_id"]
            first_detail = runner.frame_detail(first_frame_id, run_id=state["run_id"])

        self.assertEqual(state["phase"], "completed")
        self.assertEqual(state["sequence_id"], "workbench.image_replay.v1")
        self.assertEqual(state["progress"]["completed"], 2)
        self.assertEqual(len(mapper.calls), 2)
        self.assertEqual(state["observation"]["metadata"]["source"], "workbench.image_replay.v1")
        self.assertEqual(state["memory"]["health"], "healthy")
        self.assertGreaterEqual(state["memory"]["record_count"], 2)
        self.assertNotIn("frames", state["source"])
        self.assertNotIn("perception", state["timeline"][0])
        self.assertEqual(first_detail["perception"]["status"], "ok")
        self.assertIsNotNone(first_detail["observation"]["observation_id"])
        self.assertEqual(first_detail["memory"]["health"], "healthy")
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

    def test_public_state_keeps_frame_and_pipeline_payload_paired(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 2)
            mapper = BlockingSecondMapper()
            runner = ImageReplayRunner(
                root,
                cadence_ms=0,
                mapper_factory=lambda: mapper,
            )
            runner.start()
            self.assertTrue(mapper.second_started.wait(3))
            try:
                active = runner.state()
                self.assertEqual(len(active["timeline"]), 1)
                self.assertEqual(
                    active["current_frame"]["frame_id"],
                    active["timeline"][0]["frame"]["frame_id"],
                )
            finally:
                mapper.release_second.set()
            self.assertEqual(runner.wait(5)["phase"], "completed")

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
            with self.assertRaises(ReplayActionError):
                runner.dispatch("reset")
            reset = runner.dispatch("reset", run_id=run_id)
            self.assertEqual(reset["phase"], "idle")
            self.assertIsNone(reset["run_id"])
            self.assertEqual(reset["progress"]["completed"], 0)
            self.assertEqual(reset["progress"]["total"], 3)
            self.assertIsNotNone(reset["source"])
            self.assertEqual(reset["timeline"], [])
            restarted = runner.start(cadence_ms=0)
            completed_again = runner.wait(5)

        self.assertEqual(restarted["phase"], "running")
        self.assertEqual(completed_again["phase"], "completed")
        self.assertEqual(completed_again["progress"]["completed"], 3)

    def test_seek_jumps_current_frame_and_reuses_processed_history(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 4)
            mapper = FixtureMapper()
            runner = ImageReplayRunner(
                root,
                cadence_ms=5000,
                mapper_factory=lambda: mapper,
            )
            started = runner.start()
            run_id = started["run_id"]
            _wait_until(lambda: len(runner.state()["timeline"]) >= 1)
            paused = runner.dispatch("pause", run_id=run_id)
            first_id = paused["current_frame"]["frame_id"]
            calls_after_first = len(mapper.calls)
            self.assertIn("seek", paused["controls"]["allowed_actions"])

            sought = runner.dispatch("seek", run_id=run_id, position=2)
            self.assertEqual(sought["phase"], "paused")
            self.assertEqual(sought["current_frame"]["position"], 2)
            self.assertEqual(sought["position"], 3)
            self.assertEqual(len(mapper.calls), calls_after_first + 1)
            self.assertEqual(
                sought["current_frame"]["frame_id"],
                sought["timeline"][-1]["frame"]["frame_id"],
            )

            cached = runner.dispatch("seek", run_id=run_id, position=0)
            self.assertEqual(cached["phase"], "paused")
            self.assertEqual(cached["current_frame"]["frame_id"], first_id)
            self.assertEqual(cached["current_frame"]["position"], 0)
            self.assertEqual(len(mapper.calls), calls_after_first + 1)

            with self.assertRaises(ReplayActionError):
                runner.dispatch("seek", run_id=run_id, position=99)
            with self.assertRaises(ReplayActionError):
                runner.dispatch("seek", run_id=run_id)
            runner.dispatch("cancel", run_id=run_id)

        idle = ImageReplayRunner()
        with self.assertRaises(ReplayActionError):
            idle.dispatch("seek", run_id="missing", position=0)

    def test_seek_while_running_pauses_and_serves_frame_bytes_by_position(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 4)
            runner = ImageReplayRunner(root, cadence_ms=5000)
            server = WorkbenchServer(runner).start()
            self.addCleanup(server.stop)
            base = server.url
            self.assertIsNotNone(base)

            def post(payload: dict[str, object]) -> dict[str, object]:
                body = json.dumps(payload).encode("utf-8")
                return json.loads(
                    urlopen(
                        Request(
                            base + "api/action",
                            data=body,
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        ),
                        timeout=10,
                    ).read()
                )

            started = post({"action": "start", "source_dir": str(root), "cadence_ms": 5000})
            run_id = started["state"]["run_id"]
            _wait_until(lambda: len(runner.state()["timeline"]) >= 1)
            sought = post({"action": "seek", "run_id": run_id, "position": 2})
            self.assertEqual(sought["state"]["phase"], "paused")
            self.assertEqual(sought["state"]["current_frame"]["position"], 2)
            query = urlencode({"run_id": run_id, "position": "2"})
            frame = urlopen(base + "api/frame?" + query, timeout=2)
            self.assertEqual(frame.status, 200)
            self.assertTrue(frame.read())
            post({"action": "cancel", "run_id": run_id})

    def test_realtime_pace_honors_recorded_frame_timestamps(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 3)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "source_id": "timed.fixture",
                        "frames": [
                            {"frame_id": "first", "timestamp_ms": 0, "image_path": "frame_00.png"},
                            {"frame_id": "second", "timestamp_ms": 80, "image_path": "frame_01.png"},
                            {"frame_id": "third", "timestamp_ms": 160, "image_path": "frame_02.png"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            runner = ImageReplayRunner(
                root,
                cadence_ms=0,
                pace="realtime",
                mapper_factory=FixtureMapper,
            )
            started = runner.start()
            self.assertEqual(started["controls"]["pace"], "realtime")
            _wait_until(lambda: len(runner.state()["timeline"]) >= 1)
            first_seen = time.monotonic()
            _wait_until(lambda: len(runner.state()["timeline"]) >= 2)
            second_seen = time.monotonic()
            completed = runner.wait(3)

        self.assertGreaterEqual(second_seen - first_seen, 0.06)
        self.assertEqual(completed["phase"], "completed")
        self.assertEqual(completed["controls"]["pace"], "realtime")

    def test_loopback_api_accepts_realtime_pace_selection(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 2)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "source_id": "api-timed.fixture",
                        "frames": [
                            {"frame_id": "first", "timestamp_ms": 0, "image_path": "frame_00.png"},
                            {"frame_id": "second", "timestamp_ms": 20, "image_path": "frame_01.png"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            runner = ImageReplayRunner(
                cadence_ms=5000,
                mapper_factory=FixtureMapper,
            )
            server = WorkbenchServer(runner).start()
            self.addCleanup(server.stop)
            base = server.url
            self.assertIsNotNone(base)

            def post(payload: dict[str, object]) -> dict[str, object]:
                body = json.dumps(payload).encode("utf-8")
                return json.loads(
                    urlopen(
                        Request(
                            base + "api/action",
                            data=body,
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        ),
                        timeout=3,
                    ).read()
                )

            started = post(
                {
                    "action": "start",
                    "source_dir": str(root),
                    "pace": "realtime",
                    "cadence_ms": 5000,
                }
            )
            self.assertEqual(started["state"]["controls"]["pace"], "realtime")
            self.assertEqual(runner.wait(3)["phase"], "completed")

    def test_loopback_api_exposes_and_applies_plugin_selection(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 1)
            runner = ImageReplayRunner(cadence_ms=0)
            server = WorkbenchServer(runner).start()
            self.addCleanup(server.stop)
            base = server.url
            self.assertIsNotNone(base)

            html = urlopen(base, timeout=2).read().decode("utf-8")
            current_payload = html.split("function currentPayload(key) {", 1)[1].split(
                "function clearFrameSelection() {", 1
            )[0]
            self.assertIn("selected.length === 0 && runs.length > 0", current_payload)
            plugin_root = str(Path("lab/plugins/perception").resolve())

            def post(payload: dict[str, object]) -> dict[str, object]:
                body = json.dumps(payload).encode("utf-8")
                return json.loads(
                    urlopen(
                        Request(
                            base + "api/action",
                            data=body,
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        ),
                        timeout=2,
                    ).read()
                )

            inspected = post({"action": "refresh_plugins", "plugin_dir": plugin_root})
            catalog = inspected["state"]["plugin_catalog"]
            self.assertEqual(
                [item["id"] for item in catalog["plugins"]],
                ["classical_regions", "fastsam", "floor_continuity", "floor_continuity_capture"],
            )
            raw_selected = post(
                {"action": "select_plugins", "active_plugin_ids": []}
            )
            self.assertEqual(raw_selected["state"]["active_plugin_ids"], [])
            raw_started = post(
                {
                    "action": "start",
                    "source_dir": str(root),
                    "cadence_ms": 0,
                    "plugin_dir": plugin_root,
                    "active_plugin_ids": [],
                }
            )
            raw_state = runner.wait(5)
            self.assertEqual(raw_started["state"]["run_active_plugin_ids"], [])
            self.assertEqual(raw_state["phase"], "completed")
            self.assertEqual(raw_state["perception"]["status"], "empty")
            self.assertEqual(raw_state["perception"]["plugin_runs"], ())
            self.assertEqual(raw_state["perception"]["things"], ())
            selected = post(
                {
                    "action": "select_plugins",
                    "run_id": raw_started["state"]["run_id"],
                    "active_plugin_ids": ["classical_regions"],
                }
            )
            self.assertEqual(selected["state"]["active_plugin_ids"], ["classical_regions"])
            started = post(
                {
                    "action": "start",
                    "source_dir": str(root),
                    "cadence_ms": 0,
                    "plugin_dir": plugin_root,
                    "active_plugin_ids": ["classical_regions"],
                }
            )
            state = runner.wait(5)

        self.assertEqual(started["state"]["run_active_plugin_ids"], ["classical_regions"])
        self.assertEqual(state["phase"], "completed")
        self.assertEqual(
            [item["plugin_id"] for item in state["perception"]["plugin_runs"]],
            ["classical_regions"],
        )

    def test_loopback_api_allows_live_plugin_selection_at_frame_boundary(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 3)
            runner = ImageReplayRunner(cadence_ms=5000)
            server = WorkbenchServer(runner).start()
            self.addCleanup(server.stop)
            base = server.url
            self.assertIsNotNone(base)
            plugin_root = str(Path("lab/plugins/perception").resolve())

            def post(payload: dict[str, object]) -> dict[str, object]:
                body = json.dumps(payload).encode("utf-8")
                return json.loads(
                    urlopen(
                        Request(
                            base + "api/action",
                            data=body,
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        ),
                        timeout=10,
                    ).read()
                )

            post({"action": "refresh_plugins", "plugin_dir": plugin_root})
            post(
                {
                    "action": "select_plugins",
                    "active_plugin_ids": ["classical_regions"],
                }
            )
            started = post(
                {
                    "action": "start",
                    "source_dir": str(root),
                    "plugin_dir": plugin_root,
                    "active_plugin_ids": ["classical_regions"],
                    "cadence_ms": 5000,
                }
            )
            run_id = started["state"]["run_id"]
            _wait_until(lambda: len(runner.state()["timeline"]) >= 1)
            selected = post(
                {
                    "action": "select_plugins",
                    "run_id": run_id,
                    "active_plugin_ids": ["floor_continuity"],
                }
            )
            self.assertEqual(selected["state"]["run_active_plugin_ids"], ["floor_continuity"])
            paused = post({"action": "pause", "run_id": run_id})
            self.assertEqual(paused["state"]["phase"], "paused")
            stepped = post({"action": "step", "run_id": run_id})
            first_id = stepped["state"]["timeline"][0]["frame"]["frame_id"]
            second_id = stepped["state"]["timeline"][1]["frame"]["frame_id"]
            first_detail = runner.frame_detail(first_id, run_id=run_id)
            second_detail = runner.frame_detail(second_id, run_id=run_id)

        self.assertEqual(
            [run["plugin_id"] for run in first_detail["perception"]["plugin_runs"]],
            ["classical_regions"],
        )
        self.assertEqual(
            [run["plugin_id"] for run in second_detail["perception"]["plugin_runs"]],
            ["floor_continuity"],
        )
        self.assertEqual(stepped["state"]["phase"], "paused")
        post({"action": "cancel", "run_id": run_id})

    def test_loopback_api_persists_after_terminal_state_and_rejects_raw_argv(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 1)
            runner = ImageReplayRunner(cadence_ms=0)
            server = WorkbenchServer(runner).start()
            self.addCleanup(server.stop)
            base = server.url
            self.assertIsNotNone(base)

            served = urlopen(base, timeout=2)
            self.assertEqual(getattr(served, "status", 200), 200)

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
            frame_id = state["timeline"][0]["frame"]["frame_id"]
            query = urlencode({"run_id": run_id, "frame_id": frame_id})
            detail = json.loads(
                urlopen(base + "api/frame-detail?" + query, timeout=2).read()
            )
            self.assertEqual(detail["frame"]["frame_id"], frame_id)
            self.assertEqual(detail["perception"]["status"], "ok")
            self.assertEqual(detail["memory"]["health"], "healthy")
            frame = urlopen(
                base + "api/frame?" + query,
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
            with self.assertRaises(HTTPError) as stale_detail:
                urlopen(base + "api/frame-detail?" + query, timeout=2)
            self.assertEqual(stale_detail.exception.code, 409)

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
            server.stop()
            self.assertIsNone(runner.state()["source"])
            self.assertEqual(runner.state()["timeline"], [])

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
        self.assertEqual(
            payload["machine_detail"]["pipeline"]["perception_algorithm"],
            "lightweight_observer",
        )
        self.assertNotIn("argv", payload)

    def test_cli_replay_accepts_realtime_pace(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 1)
            result = run_automa(
                "vehicles",
                "workbench",
                "replay",
                str(root),
                "--pace",
                "realtime",
                "--cadence-ms",
                "0",
                "--json",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["phase"], "completed")
        self.assertEqual(payload["controls"]["pace"], "realtime")

    def test_cli_replay_human_output_names_recovery_and_cleanup(self) -> None:
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
            )

        self.assertIn("phase: completed", result.stdout)
        self.assertIn("recovery:", result.stdout)
        self.assertIn("cleanup:", result.stdout)
        self.assertIn("source_read_only=True", result.stdout)


if __name__ == "__main__":
    unittest.main()
