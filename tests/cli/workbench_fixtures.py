from __future__ import annotations
import json
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator
from urllib.request import Request, urlopen
from PIL import Image
from autonomy.perception import (
    PERCEPTION_TEXT_SCHEMA,
    PerceptionText,
    PerceptionSignal,
    PerceivedThing,
    ViewLocation,
)
from autonomy.memory.values import MemoryBounds, MemorySnapshot
from cli.automa_cli.workbench import (
    ImageReplayRunner as ProductionImageReplayRunner,
    WorkbenchServer,
)


@contextmanager
def image_source(count: int) -> Iterator[Path]:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        _make_images(root, count)
        yield root


def write_manifest(root: Path, payload: dict, name: str = "manifest.json") -> None:
    (root / name).write_text(json.dumps(payload), encoding="utf-8")


def serve_workbench(testcase, runner: ProductionImageReplayRunner) -> str:
    server = WorkbenchServer(runner).start()
    testcase.addCleanup(server.stop)
    base = server.url
    if base is None:
        raise AssertionError("workbench server has no URL")
    return base


def post_action(base: str, payload: dict[str, object], *, timeout: float = 2) -> dict:
    response = urlopen(
        Request(
            base + "api/action",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        ),
        timeout=timeout,
    )
    return json.loads(response.read())


class PluginCatalogFixture:
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.plugin_root = Path(temporary.name)
        source_root = Path(__file__).resolve().parents[2] / "lab/plugins/perception"
        # Exercise real manifests and entrypoints without scanning local models,
        # virtual environments, recorded runs, or unrelated candidate packages.
        for name in (
            "classical_regions",
            "fastsam",
            "floor_continuity",
            "floor_continuity_capture",
        ):
            source = source_root / name
            paths = [source / "plugin.json", *sorted((source / "src").glob("*.py"))]
            for path in paths:
                target = self.plugin_root / name / path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)


class ImageReplayRunner(ProductionImageReplayRunner):
    """Deterministic tests default loop off so wait() can observe completed."""

    def __init__(self, *args, loop=False, **kwargs):
        super().__init__(*args, loop=loop, **kwargs)


class FixtureMapper:
    plugin_id = "fixture_mapper"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.reset_count = 0

    def reset(self, shared_memory=None) -> None:
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


class DecisionFixtureMapper(FixtureMapper):
    def perceive(self, request) -> PerceptionText:
        reading = request.sensor("front_camera")
        path = reading.path if reading is not None else ""
        self.calls.append(str(path))
        return PerceptionText(
            schema=PERCEPTION_TEXT_SCHEMA,
            plugin_id=self.plugin_id,
            status="ok",
            lines=("decision evidence",),
            signals=(),
            things=(
                PerceivedThing(
                    thing_id="obstruction",
                    kind="floor_boundary",
                    label="floor boundary",
                    location=ViewLocation(
                        frame="image",
                        zone="left",
                        bbox_xyxy_norm=(0.1, 0.1, 0.3, 0.4),
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

    def reset(self, shared_memory=None) -> None:
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
