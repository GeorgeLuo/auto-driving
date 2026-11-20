from __future__ import annotations
import io
import json
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from PIL import Image

from autonomy.runtime.frame_loop import FRAME_PUBLICATION_SCHEMA, FramePublication
from cli.automa_cli.hosts.publications.decision_records import DecisionRunners
from cli.automa_cli.frontend.perception_view import PerceptionView
from cli.automa_cli.hosts.step_activations import decision_identity
from cli.automa_cli.releases.paths import bundle_paths
from tests.shared.cli.decision.decision_surfaces_fixtures import (
    published_cycle,
    frame_inputs,
    left_obstruction_frame,
    packaged_decision_steps,
    vehicle_report_for_records,
)



SOURCE_ID = "runtime-host"


def _jpeg_bytes(color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(buffer, format="JPEG")
    return buffer.getvalue()


class LiveRuntimeDecisionViewFixture:
    @classmethod
    def setUpClass(cls) -> None:
        # Tests read the staged runtime but never write it, so a class stages it once.
        super().setUpClass()
        cls._temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._temporary.cleanup)
        cls.runtime_root = Path(cls._temporary.name) / "vehicles"
        cls.runtime_root.mkdir()
        from tests.support.runtime_fixtures import runtime_root_env, stage_step
        cls.enterClassContext(runtime_root_env(cls.runtime_root))
        for step in ("proposal", "action"):
            stage_step(cls.runtime_root, "chase-sim-chaser", step)
        cls.vehicle_runtime = cls.runtime_root / "chase-sim-chaser"
        cls.runtime_dir = cls.vehicle_runtime / "bundle" / "runtime"
        cls.automation_dir = cls.runtime_dir / "automation"
        cls.activation = decision_identity(bundle_paths("chase-sim-chaser"), host_name="chase-sim-chaser")

    def setUp(self) -> None:
        # The view v2's host frame routes read a host's latest frame through.
        self.view = PerceptionView(host_name="chase-sim-chaser")

    def _accepted_frame(
        self,
        *,
        run_id: str = "run-live",
        raw: dict | None = None,
    ) -> dict:
        if raw is None:
            raw = left_obstruction_frame()
        records = DecisionRunners.from_payloads(packaged_decision_steps()).run(
            frame_id=raw["frame_id"],
            frame_index=raw["frame_index"],
            timestamp_ms=raw["timestamp_ms"],
            **frame_inputs(raw),
        )
        observation, memory = published_cycle(raw)
        return vehicle_report_for_records(
            records,
            host_name="chase-sim-chaser",
            run_id=run_id,
            generation_id=self.activation["generation_id"],
            frame_id=raw["frame_id"],
            frame_index=raw["frame_index"],
            timestamp_ms=raw["timestamp_ms"],
            published_at_ms=int(time.time() * 1000),
            values={"source_id": SOURCE_ID},
            observation=observation,
            memory=memory,
        )

    def _accepted_frame_with_evidence(self, mutate=None) -> dict:
        raw = deepcopy(
            left_obstruction_frame()
        )
        record = raw["evidence"][0]
        origin = record["origin"]
        thing = {
            "thing_id": origin["observed_id"],
            "kind": record["kind"],
            "label": record["label"],
            "location": deepcopy(record["location"]),
            "confidence": record["confidence"],
            "properties": deepcopy(record["properties"]),
            "source_plugin_id": origin["source_plugin_id"],
        }
        raw["observation"]["things"] = [thing]
        if mutate is not None:
            mutate(raw, record, thing)
        return self._accepted_frame(raw=raw)

    def _publish_exact_transaction(
        self,
        *,
        view: PerceptionView | None = None,
        run_id: str = "run-live",
        image_color: tuple[int, int, int] = (20, 80, 150),
        stream_frame: dict | None = None,
    ) -> tuple[dict, bytes]:
        target = self.view if view is None else view
        stream_frame = stream_frame or self._accepted_frame(run_id=run_id)
        frame_bytes = _jpeg_bytes(image_color)
        target.publish(
            publication=FramePublication.from_dict({
                "schema": FRAME_PUBLICATION_SCHEMA,
                "ok": True,
                "health": "healthy",
                "read_at_ms": stream_frame["published_at_ms"],
                "result_age_ms": 0,
                "stale_after_ms": stream_frame["values"]["stale_after_ms"],
                "report": stream_frame,
            }),
            frame_bytes=frame_bytes,
        )
        return stream_frame, frame_bytes

    def _latest_payload(self) -> dict:
        return json.loads(self.view.latest_json())

    def _image(self, url: str) -> tuple[bytes, str]:
        """The buffered image a payload's ``image.url`` names."""

        return self.view.frame(parse_qs(urlsplit(url).query)["v"][0])
