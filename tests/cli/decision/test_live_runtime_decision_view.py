"""Happy-path contract for D2's RuntimeViewServer decision surface."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

from cli.automa_cli import decision as decision_module
from cli.automa_cli.automation import _read_latest_decision_frame_for_view
from cli.automa_cli.decision import (
    ENGINE_ID,
    build_decision_stream_frame,
    get_vehicle_decision_info,
    publish_shadow_decision_frame,
    strict_decode_apply_memory,
    strict_decode_apply_observation,
    update_vehicle_decision,
)
from cli.automa_cli.runtime_view import RuntimeViewServer
from implementations.decision.catalog import create_shadow_proposals_engine


FIXTURES = Path(__file__).resolve().parent / "fixtures"
ACTIVE_RUN = FIXTURES / "apply_active_left"


class LiveRuntimeDecisionViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.runtime_root = Path(self._temporary.name) / "vehicles"
        self.runtime_root.mkdir()
        self._old_runtime_root = decision_module.RUNTIME_ROOT
        decision_module.RUNTIME_ROOT = self.runtime_root
        self.addCleanup(setattr, decision_module, "RUNTIME_ROOT", self._old_runtime_root)
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

    def _accepted_frame(self) -> dict:
        raw = json.loads((ACTIVE_RUN / "sequence.json").read_text(encoding="utf-8"))["frames"][0]
        cycle, _control = create_shadow_proposals_engine().run_cycle(
            frame_id=raw["frame_id"],
            frame_index=raw["frame_index"],
            timestamp_ms=raw["timestamp_ms"],
            observation=strict_decode_apply_observation(raw["observation"]),
            memory=strict_decode_apply_memory(raw["memory"]),
        )
        return build_decision_stream_frame(
            cycle,
            vehicle_id="chase-sim-chaser",
            run_id="run-live",
            worker_pid=os.getpid(),
            activation_engine_id=ENGINE_ID,
            activation_activated_at_ms=self.activation["activated_at_ms"],
        )

    def _publish_exact_transaction(self) -> tuple[dict, bytes]:
        stream_frame = self._accepted_frame()
        frame_path = Path(self._temporary.name) / "decision-frame.png"
        Image.new("RGB", (40, 30), (20, 80, 150)).save(frame_path)
        frame_record = {
            "frame_id": stream_frame["frame_id"],
            "frame_index": stream_frame["frame_index"],
            "captured_at_ms": stream_frame["timestamp_ms"],
            "run_id": "run-live",
            "worker_pid": os.getpid(),
            "sensor_snapshot": {"readings": {"front_camera": {"read_id": stream_frame["frame_id"]}}},
        }
        self.server.perception.publish_frame(frame_path=frame_path, frame_record=frame_record)

        # A newer camera capture becomes perception's default frame first. The
        # decision must still select the exact buffered frame above by ID.
        newer_path = Path(self._temporary.name) / "later-frame.png"
        Image.new("RGB", (40, 30), (210, 40, 30)).save(newer_path)
        self.server.perception.publish_frame(
            frame_path=newer_path,
            frame_record={**frame_record, "frame_id": "frame_later", "frame_index": frame_record["frame_index"] + 1},
        )
        exact_image = self.server.perception.frame(stream_frame["frame_id"])
        self.assertIsNotNone(exact_image)
        self.assertTrue(
            self.server.decision.publish(
                stream_frame=stream_frame,
                frame_record=frame_record,
                image=exact_image,
            )
        )
        return stream_frame, frame_path.read_bytes()

    def test_serves_exact_decision_image_and_separate_authority_facts(self) -> None:
        stream_frame, expected_image = self._publish_exact_transaction()
        generation = self.server.decision.generation_id
        self.assertIsNotNone(generation)
        with urlopen(f"{self.server.url}api/decision/latest?generation={generation}", timeout=1.0) as response:
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(payload["status"], "current")
        self.assertEqual(payload["decision"]["frame_id"], stream_frame["frame_id"])
        self.assertEqual(payload["current_image"]["frame_id"], stream_frame["frame_id"])
        self.assertFalse(payload["authority"]["proposed_applied"])
        self.assertIn("authorized_output", payload["authority"])
        self.assertIn("host_application", payload["authority"])

        with urlopen(f"{self.server.url.rstrip('/')}{payload['current_image']['url']}", timeout=1.0) as response:
            self.assertEqual(response.headers.get_content_type(), "image/png")
            self.assertEqual(response.read(), expected_image)

        with urlopen(f"{self.server.url}decision?generation={generation}", timeout=1.0) as response:
            page = response.read().decode("utf-8")
        self.assertIn("Automa Decision", page)
        self.assertIn('id="pauseButton"', page)
        with urlopen(self.server.url, timeout=1.0) as response:
            self.assertIn(f"/decision?generation={generation}", response.read().decode("utf-8"))

        info = get_vehicle_decision_info(vehicle_id="chase-sim-chaser", json_output=True)
        self.assertEqual(info.exit_code, 0, info.message)
        combined = json.loads(info.message)["combined_view"]
        self.assertTrue(combined["available"])
        self.assertEqual(combined["status"], "current")
        self.assertEqual(combined["url"], f"{self.server.url}decision?generation={generation}")
        self.assertEqual(
            payload["freshness"]["captured_at_ms"],
            stream_frame["timestamp_ms"],
        )
        self.assertNotEqual(
            payload["freshness"]["captured_at_ms"],
            payload["freshness"]["published_at_ms"],
        )

    def test_shadow_publish_joins_the_exact_buffered_capture(self) -> None:
        raw = json.loads((ACTIVE_RUN / "sequence.json").read_text(encoding="utf-8"))["frames"][0]
        cycle, _control = create_shadow_proposals_engine().run_cycle(
            frame_id=raw["frame_id"],
            frame_index=raw["frame_index"],
            timestamp_ms=raw["timestamp_ms"],
            observation=strict_decode_apply_observation(raw["observation"]),
            memory=strict_decode_apply_memory(raw["memory"]),
        )
        self.assertTrue(
            publish_shadow_decision_frame(
                cycle_result=cycle,
                context_frame_id=raw["frame_id"],
                vehicle_id="chase-sim-chaser",
                vehicle_runtime_dir=self.vehicle_runtime,
                run_id="run-live",
                worker_pid=os.getpid(),
                activation=self.activation,
                staged_engine_id=ENGINE_ID,
            )
        )
        latest = _read_latest_decision_frame_for_view(
            self.automation_dir / "latest_decision.json",
            frame_id=raw["frame_id"],
            run_id="run-live",
            worker_pid=os.getpid(),
            activation_activated_at_ms=self.activation["activated_at_ms"],
        )
        if latest is None:
            self.fail("shadow publish did not write a matching latest decision frame")
        frame_path = Path(self._temporary.name) / "joined-frame.png"
        Image.new("RGB", (40, 30), (20, 80, 150)).save(frame_path)
        frame_record = {
            "frame_id": latest["frame_id"],
            "frame_index": latest["frame_index"],
            "captured_at_ms": latest["timestamp_ms"],
            "run_id": "run-live",
            "worker_pid": os.getpid(),
            "sensor_snapshot": {"readings": {"front_camera": {"read_id": latest["frame_id"]}}},
        }
        self.server.perception.publish_frame(frame_path=frame_path, frame_record=frame_record)
        self.assertTrue(
            self.server.decision.publish(
                stream_frame=latest,
                frame_record=frame_record,
                image=self.server.perception.frame(latest["frame_id"]),
            )
        )
        generation = self.server.decision.generation_id
        with urlopen(f"{self.server.url}api/decision/latest?generation={generation}", timeout=1.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(payload["current_image"]["frame_id"], latest["frame_id"])
        self.assertEqual(payload["decision"]["frame_id"], latest["frame_id"])

    def test_info_reports_generation_url_while_warming(self) -> None:
        generation = self.server.decision.generation_id
        self.assertIsNotNone(generation)
        info = get_vehicle_decision_info(vehicle_id="chase-sim-chaser", json_output=True)
        self.assertEqual(info.exit_code, 0, info.message)
        combined = json.loads(info.message)["combined_view"]
        self.assertTrue(combined["available"])
        self.assertEqual(combined["status"], "warming")
        self.assertEqual(combined["url"], f"{self.server.url}decision?generation={generation}")

    def test_refuses_mismatched_generation_and_write_method(self) -> None:
        self._publish_exact_transaction()
        with self.assertRaises(HTTPError) as mismatch:
            urlopen(f"{self.server.url}api/decision/latest?generation={'0' * 64}", timeout=1.0)
        self.assertEqual(mismatch.exception.code, 409)
        self.assertEqual(json.loads(mismatch.exception.read().decode("utf-8"))["reason"], "generation_mismatch")

        with self.assertRaises(HTTPError) as write:
            urlopen(Request(f"{self.server.url}api/decision/latest", data=b"{}"), timeout=1.0)
        self.assertEqual(write.exception.code, 405)
        self.assertEqual(write.exception.headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main(verbosity=2)
