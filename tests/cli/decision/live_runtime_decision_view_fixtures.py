from __future__ import annotations
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from urllib.request import HTTPRedirectHandler, urlopen
from PIL import Image
from autonomy.decision import ComponentEnvelope
from cli.automa_cli import decision as decision_module
from cli.automa_cli.decision import (
    ENGINE_ID,
    build_decision_stream_frame,
    strict_decode_apply_memory,
    strict_decode_apply_observation,
    update_vehicle_decision,
)
from cli.automa_cli.runtime_view import RuntimeViewServer
from implementations.decision.catalog import create_shadow_proposals_engine


SOURCES = Path(__file__).resolve().parents[1] / "sources" / "json"
ACTIVE_RUN = SOURCES / "apply_active_left"


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, newurl):
        return None


class LiveRuntimeDecisionViewFixture:
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.runtime_root = Path(self._temporary.name) / "vehicles"
        self.runtime_root.mkdir()
        self._old_runtime_root = decision_module.RUNTIME_ROOT
        decision_module.RUNTIME_ROOT = self.runtime_root
        self.addCleanup(
            setattr, decision_module, "RUNTIME_ROOT", self._old_runtime_root
        )
        staged = update_vehicle_decision(
            vehicle_id="chase-sim-chaser", engine_id=ENGINE_ID, json_output=True
        )
        self.assertEqual(staged.exit_code, 0, staged.message)
        self.vehicle_runtime = self.runtime_root / "chase-sim-chaser"
        self.runtime_dir = self.vehicle_runtime / "bundle" / "runtime"
        self.automation_dir = self.runtime_dir / "automation"
        self.activation_path = self.runtime_dir / "decision" / "active.json"
        self.activation = json.loads(self.activation_path.read_text(encoding="utf-8"))
        self.server = RuntimeViewServer(
            vehicle_id="chase-sim-chaser",
            automation_dir=self.automation_dir,
            port=0,
            run_id="run-live",
            worker_pid=os.getpid(),
            decision_activation=self.activation,
            decision_activation_path=self.activation_path,
        ).start()
        self.addCleanup(self.server.stop)

    def _accepted_frame(
        self,
        *,
        run_id: str = "run-live",
        raw: dict | None = None,
        host_application: ComponentEnvelope | None = None,
    ) -> dict:
        if raw is None:
            raw = json.loads(
                (ACTIVE_RUN / "sequence.json").read_text(encoding="utf-8")
            )["frames"][0]
        cycle, _control = create_shadow_proposals_engine().run_cycle(
            frame_id=raw["frame_id"],
            frame_index=raw["frame_index"],
            timestamp_ms=raw["timestamp_ms"],
            observation=strict_decode_apply_observation(raw["observation"]),
            memory=strict_decode_apply_memory(raw["memory"]),
            host_application=host_application,
        )
        return build_decision_stream_frame(
            cycle,
            vehicle_id="chase-sim-chaser",
            run_id=run_id,
            worker_pid=os.getpid(),
            activation_engine_id=ENGINE_ID,
            activation_activated_at_ms=self.activation["activated_at_ms"],
        )

    def _accepted_frame_with_evidence(self, mutate=None) -> dict:
        raw = deepcopy(
            json.loads((ACTIVE_RUN / "sequence.json").read_text(encoding="utf-8"))[
                "frames"
            ][0]
        )
        record = raw["memory"]["records"][0]
        provenance = record["provenance"]
        thing = {
            "thing_id": provenance["evidence_id"],
            "kind": record["kind"],
            "label": record["label"],
            "location": deepcopy(record["location"]),
            "confidence": record["confidence"],
            "properties": deepcopy(record["properties"]),
            "source_plugin_id": provenance["source_plugin_id"],
        }
        raw["observation"]["things"] = [thing]
        if mutate is not None:
            mutate(raw, record, thing)
        return self._accepted_frame(raw=raw)

    def _publish_exact_transaction(
        self,
        *,
        server: RuntimeViewServer | None = None,
        run_id: str = "run-live",
        image_name: str = "decision-frame.png",
        image_color: tuple[int, int, int] = (20, 80, 150),
        stream_frame: dict | None = None,
    ) -> tuple[dict, bytes]:
        target = self.server if server is None else server
        stream_frame = stream_frame or self._accepted_frame(run_id=run_id)
        frame_path = Path(self._temporary.name) / image_name
        Image.new("RGB", (40, 30), image_color).save(frame_path)
        frame_record = {
            "frame_id": stream_frame["frame_id"],
            "frame_index": stream_frame["frame_index"],
            "captured_at_ms": stream_frame["timestamp_ms"],
            "run_id": run_id,
            "worker_pid": os.getpid(),
            "sensor_snapshot": {
                "readings": {"front_camera": {"read_id": stream_frame["frame_id"]}}
            },
        }
        target.perception.publish_frame(
            frame_path=frame_path, frame_record=frame_record
        )

        # A newer camera capture becomes perception's default frame first. The
        # decision must still select the exact buffered frame above by ID.
        newer_path = Path(self._temporary.name) / f"{image_name}.later.png"
        Image.new("RGB", (40, 30), (210, 40, 30)).save(newer_path)
        target.perception.publish_frame(
            frame_path=newer_path,
            frame_record={
                **frame_record,
                "frame_id": f"{run_id}-later",
                "frame_index": frame_record["frame_index"] + 1,
            },
        )
        exact_image = target.perception.frame(stream_frame["frame_id"])
        self.assertIsNotNone(exact_image)
        self.assertTrue(
            target.decision.publish(
                stream_frame=stream_frame,
                frame_record=frame_record,
                image=exact_image,
            )
        )
        return stream_frame, frame_path.read_bytes()

    def _latest_payload(self) -> dict:
        generation = self.server.decision.generation_id
        self.assertIsNotNone(generation)
        with urlopen(
            f"{self.server.url}api/decision/latest?generation={generation}",
            timeout=1.0,
        ) as response:
            return json.loads(response.read().decode("utf-8"))
