"""Happy-path contract for D2's RuntimeViewServer decision surface."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from unittest.mock import patch

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
from cli.automa_cli.loopback_http import LoopbackHTTPRequestHandler
from cli.automa_cli.runtime_view import RuntimeViewServer
from implementations.decision.catalog import create_shadow_proposals_engine


FIXTURES = Path(__file__).resolve().parent / "fixtures"
ACTIVE_RUN = FIXTURES / "apply_active_left"


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, newurl):
        return None


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

    def _accepted_frame(self, *, run_id: str = "run-live", raw: dict | None = None) -> dict:
        if raw is None:
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
            run_id=run_id,
            worker_pid=os.getpid(),
            activation_engine_id=ENGINE_ID,
            activation_activated_at_ms=self.activation["activated_at_ms"],
        )

    def _accepted_frame_with_evidence(self, mutate=None) -> dict:
        raw = deepcopy(
            json.loads((ACTIVE_RUN / "sequence.json").read_text(encoding="utf-8"))["frames"][0]
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
            "sensor_snapshot": {"readings": {"front_camera": {"read_id": stream_frame["frame_id"]}}},
        }
        target.perception.publish_frame(frame_path=frame_path, frame_record=frame_record)

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

    def test_exact_retained_evidence_is_machine_visible_and_renderable(self) -> None:
        stream_frame = self._accepted_frame_with_evidence()
        self._publish_exact_transaction(stream_frame=stream_frame)

        payload = self._latest_payload()
        evidence = payload["evidence"]
        self.assertEqual(evidence["status"], "available")
        self.assertEqual(evidence["frame_id"], payload["current_image"]["frame_id"])
        self.assertEqual(evidence["transaction_id"], payload["transaction_id"])
        self.assertEqual(evidence["available_count"], 1)
        projected = evidence["records"][0]
        self.assertEqual(projected["status"], "available")
        self.assertEqual(projected["reason"], "")
        self.assertEqual(
            projected["record"],
            payload["provenance"]["memory"]["value"]["records"][0],
        )

        generation = self.server.decision.generation_id
        with urlopen(f"{self.server.url}decision?generation={generation}", timeout=1.0) as response:
            page = response.read().decode("utf-8")
        self.assertIn("function evidenceItems()", page)
        self.assertIn('item?.status === "available"', page)

    def test_unmatched_retained_evidence_preserves_provenance_without_overlay(self) -> None:
        def older_frame(_raw, record, _thing) -> None:
            record["provenance"]["frame_id"] = "frame_older"

        def observation_mismatch(_raw, record, _thing) -> None:
            record["provenance"]["observation_id"] = "obs_other"

        def missing_evidence(_raw, _record, thing) -> None:
            thing["thing_id"] = "ev_other"

        def ambiguous_evidence(raw, _record, thing) -> None:
            raw["observation"]["things"].append(deepcopy(thing))

        def provenance_mismatch(_raw, record, _thing) -> None:
            record["provenance"]["source_plugin_id"] = "other_plugin"

        def geometry_mismatch(_raw, _record, thing) -> None:
            thing["location"]["bbox_xyxy_norm"] = [0.1, 0.0, 0.3, 0.5]

        def unsupported_geometry(_raw, record, thing) -> None:
            record["location"]["bbox_xyxy_norm"] = None
            thing["location"]["bbox_xyxy_norm"] = None

        cases = (
            ("source_image_unavailable", older_frame),
            ("observation_mismatch", observation_mismatch),
            ("evidence_missing", missing_evidence),
            ("evidence_ambiguous", ambiguous_evidence),
            ("provenance_mismatch", provenance_mismatch),
            ("geometry_mismatch", geometry_mismatch),
            ("unsupported_geometry", unsupported_geometry),
        )
        for index, (expected_reason, mutate) in enumerate(cases):
            with self.subTest(reason=expected_reason):
                stream_frame = self._accepted_frame_with_evidence(mutate)
                self._publish_exact_transaction(
                    stream_frame=stream_frame,
                    image_name=f"evidence-{index}.png",
                )
                payload = self._latest_payload()
                evidence = payload["evidence"]
                self.assertEqual(evidence["status"], "unavailable")
                self.assertEqual(evidence["available_count"], 0)
                projected = evidence["records"][0]
                self.assertEqual(projected["status"], "unavailable")
                self.assertEqual(projected["reason"], expected_reason)
                self.assertIsNone(projected["record"])
                self.assertEqual(
                    projected["provenance"],
                    payload["provenance"]["memory"]["value"]["records"][0]["provenance"],
                )

    def test_rejected_publication_does_not_serve_cached_success(self) -> None:
        stream_frame, _expected_image = self._publish_exact_transaction()
        generation = self.server.decision.generation_id
        self.assertIsNotNone(generation)
        frame_record = {
            "frame_id": stream_frame["frame_id"],
            "frame_index": stream_frame["frame_index"],
            "captured_at_ms": stream_frame["timestamp_ms"],
            "run_id": "run-live",
            "worker_pid": os.getpid(),
        }
        rejected_frame = {**stream_frame, "cycle": None}
        self.assertFalse(
            self.server.decision.publish(
                stream_frame=rejected_frame,
                frame_record=frame_record,
                image=self.server.perception.frame(stream_frame["frame_id"]),
            )
        )

        with self.assertRaises(HTTPError) as caught:
            urlopen(
                f"{self.server.url}api/decision/latest?generation={generation}",
                timeout=1.0,
            )
        self.assertEqual(caught.exception.code, 503)
        error = json.loads(caught.exception.read().decode("utf-8"))
        self.assertEqual(error["reason"], "decision_warming")
        self.assertNotIn("transaction_id", error)

        recovered, _ = self._publish_exact_transaction(image_name="recovered-frame.png")
        with urlopen(
            f"{self.server.url}api/decision/latest?generation={generation}",
            timeout=1.0,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(payload["status"], "current")
        self.assertEqual(payload["decision"]["frame_id"], recovered["frame_id"])

    def test_reader_bypass_invalidation_preserves_warming_contract(self) -> None:
        self._publish_exact_transaction()
        generation = self.server.decision.generation_id
        self.assertIsNotNone(generation)

        self.server.decision.invalidate_latest()

        with self.assertRaises(HTTPError) as caught:
            urlopen(
                f"{self.server.url}api/decision/latest?generation={generation}",
                timeout=1.0,
            )
        self.assertEqual(caught.exception.code, 503)
        error = json.loads(caught.exception.read().decode("utf-8"))
        self.assertEqual(error["status"], "unavailable")
        self.assertEqual(error["reason"], "decision_warming")
        self.assertNotIn("transaction_id", error)

    def test_slow_image_response_does_not_hold_decision_view_lock(self) -> None:
        """A delayed public image response cannot block the next publication."""

        _stream_frame, expected_image = self._publish_exact_transaction()
        generation = self.server.decision.generation_id
        self.assertIsNotNone(generation)
        payload = self.server.decision.latest_payload(generation=generation)
        image_url = f"{self.server.url.rstrip('/')}{payload['current_image']['url']}"

        send_started = threading.Event()
        release_send = threading.Event()
        publish_done = threading.Event()
        reader_result: dict[str, object] = {}
        publish_errors: list[BaseException] = []
        original_send = LoopbackHTTPRequestHandler._send

        def delayed_send(
            handler,
            status: int,
            body: bytes,
            content_type: str,
            *,
            include_body: bool = True,
        ) -> None:
            if urlparse(handler.path).path == "/api/decision/image":
                send_started.set()
                if not release_send.wait(timeout=2.0):
                    raise AssertionError("test did not release the deliberately slow reader")
            original_send(
                handler,
                status,
                body,
                content_type,
                include_body=include_body,
            )

        def read_image() -> None:
            try:
                with urlopen(image_url, timeout=3.0) as response:
                    reader_result["body"] = response.read()
            except BaseException as exc:  # noqa: BLE001 - surface thread failures
                reader_result["error"] = exc

        def publish_next() -> None:
            try:
                self._publish_exact_transaction(
                    image_name="decision-frame-next.png",
                    image_color=(210, 40, 30),
                )
            except BaseException as exc:  # noqa: BLE001 - surface thread failures
                publish_errors.append(exc)
            finally:
                publish_done.set()

        reader = threading.Thread(target=read_image, daemon=True)
        publisher = threading.Thread(target=publish_next, daemon=True)
        try:
            with patch.object(LoopbackHTTPRequestHandler, "_send", new=delayed_send):
                reader.start()
                self.assertTrue(send_started.wait(timeout=1.0))
                publisher.start()
                self.assertTrue(
                    publish_done.wait(timeout=1.0),
                    "publication blocked behind the delayed HTTP response",
                )
        finally:
            release_send.set()
            reader.join(timeout=2.0)
            publisher.join(timeout=2.0)

        self.assertFalse(publish_errors, publish_errors)
        self.assertNotIn("error", reader_result)
        self.assertEqual(reader_result.get("body"), expected_image)

    def test_info_reports_generation_url_while_warming(self) -> None:
        generation = self.server.decision.generation_id
        self.assertIsNotNone(generation)
        info = get_vehicle_decision_info(vehicle_id="chase-sim-chaser", json_output=True)
        self.assertEqual(info.exit_code, 0, info.message)
        combined = json.loads(info.message)["combined_view"]
        self.assertTrue(combined["available"])
        self.assertEqual(combined["status"], "warming")
        self.assertEqual(combined["url"], f"{self.server.url}decision?generation={generation}")

    def test_stale_api_refusal_leaves_a_visibly_frozen_browser_snapshot(self) -> None:
        stream_frame, _expected_image = self._publish_exact_transaction()
        generation = self.server.decision.generation_id
        self.assertIsNotNone(generation)
        stale_now = (
            stream_frame["published_at_ms"]
            + decision_module.DECISION_STREAM_MAX_AGE_MS
            + 1
        )
        with patch("cli.automa_cli.decision_view._now_ms", return_value=stale_now):
            with self.assertRaises(HTTPError) as caught:
                urlopen(
                    f"{self.server.url}api/decision/latest?generation={generation}",
                    timeout=1.0,
                )
        self.assertEqual(caught.exception.code, 503)
        error = json.loads(caught.exception.read().decode("utf-8"))
        self.assertEqual(error["reason"], "decision_stale")

        with urlopen(f"{self.server.url}decision?generation={generation}", timeout=1.0) as response:
            page = response.read().decode("utf-8")
        self.assertIn("Frozen/stale snapshot", page)
        self.assertIn("Paused | frozen snapshot", page)

    def test_old_session_is_unavailable_after_public_activation_restaging(self) -> None:
        self._publish_exact_transaction()
        old_generation = self.server.decision.generation_id
        self.assertIsNotNone(old_generation)
        old_latest_url = f"{self.server.url}api/decision/latest?generation={old_generation}"
        with urlopen(old_latest_url, timeout=1.0) as response:
            old_payload = json.loads(response.read().decode("utf-8"))
        old_image_url = f"{self.server.url.rstrip('/')}{old_payload['current_image']['url']}"

        # Use the public update command to replace the active decision
        # generation while the old producer is still serving its URL.
        restage = update_vehicle_decision(
            vehicle_id="chase-sim-chaser",
            engine_id="idle",
            json_output=True,
        )
        self.assertEqual(restage.exit_code, 0, restage.message)
        current_activation = json.loads(self.activation_path.read_text(encoding="utf-8"))
        self.assertEqual(current_activation["decision"]["engine_id"], "idle")
        self.assertNotEqual(current_activation, self.activation)

        for old_url in (old_latest_url, old_image_url):
            with self.subTest(old_url=old_url):
                with self.assertRaises(HTTPError) as rejected:
                    urlopen(old_url, timeout=1.0)
                self.assertEqual(rejected.exception.code, 503)
                error = json.loads(rejected.exception.read().decode("utf-8"))
                self.assertEqual(error["status"], "unavailable")
                self.assertEqual(error["reason"], "activation_mismatch")
                self.assertNotIn("transaction_id", error)
                self.assertNotIn("current_image", error)

    def test_old_session_cannot_attach_to_same_port_replacement(self) -> None:
        self._publish_exact_transaction()
        old_url = self.server.url
        old_generation = self.server.decision.generation_id
        self.assertIsNotNone(old_url)
        self.assertIsNotNone(old_generation)
        old_latest_url = f"{old_url}api/decision/latest?generation={old_generation}"
        with urlopen(old_latest_url, timeout=1.0) as response:
            old_payload = json.loads(response.read().decode("utf-8"))
        old_image_url = f"{old_url.rstrip('/')}{old_payload['current_image']['url']}"
        old_port = urlparse(old_url).port
        self.assertIsNotNone(old_port)

        self.server.stop()
        replacement = RuntimeViewServer(
            vehicle_id="chase-sim-chaser",
            automation_dir=self.automation_dir,
            port=old_port,
            run_id="run-replacement",
            worker_pid=os.getpid(),
            decision_activation=self.activation,
            decision_activation_path=self.activation_path,
        ).start()
        self.addCleanup(replacement.stop)
        replacement_frame, replacement_image = self._publish_exact_transaction(
            server=replacement,
            run_id="run-replacement",
            image_name="replacement-frame.png",
            image_color=(210, 40, 30),
        )
        new_generation = replacement.decision.generation_id
        self.assertIsNotNone(new_generation)
        self.assertNotEqual(old_generation, new_generation)

        new_latest_url = f"{replacement.url}api/decision/latest?generation={new_generation}"
        with urlopen(new_latest_url, timeout=1.0) as response:
            replacement_payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(replacement_payload["decision"]["run_id"], "run-replacement")
        self.assertEqual(replacement_payload["decision"]["frame_id"], replacement_frame["frame_id"])
        with urlopen(
            f"{replacement.url.rstrip('/')}{replacement_payload['current_image']['url']}",
            timeout=1.0,
        ) as response:
            self.assertEqual(response.read(), replacement_image)

        for old_session_url in (old_latest_url, old_image_url):
            with self.subTest(old_session_url=old_session_url):
                with self.assertRaises(HTTPError) as rejected:
                    urlopen(old_session_url, timeout=1.0)
                self.assertEqual(rejected.exception.code, 409)
                body = rejected.exception.read().decode("utf-8")
                error = json.loads(body)
                self.assertEqual(error["status"], "unavailable")
                self.assertEqual(error["reason"], "generation_mismatch")
                self.assertNotIn("transaction_id", error)
                self.assertNotIn("current_image", error)
                self.assertNotIn(replacement_payload["transaction_id"], body)
                self.assertNotIn(replacement_payload["current_image"]["sha256"], body)

    def test_rejects_write_method_without_decision_dispatch(self) -> None:
        self._publish_exact_transaction()

        with patch.object(
            self.server.decision,
            "latest_payload",
            side_effect=AssertionError("write request reached the decision publisher"),
        ):
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                with self.subTest(method=method):
                    with self.assertRaises(HTTPError) as write:
                        urlopen(
                            Request(
                                f"{self.server.url}api/decision/latest",
                                data=b"{}",
                                method=method,
                            ),
                            timeout=1.0,
                        )
                    self.assertEqual(write.exception.code, 405)
                    self.assertEqual(write.exception.headers["Cache-Control"], "no-store")

    def test_rejects_traversal_and_url_shaped_image_ids_without_dispatch(self) -> None:
        generation = self.server.decision.generation_id
        self.assertIsNotNone(generation)
        secret_path = Path(self._temporary.name) / "route-boundary-secret.txt"
        secret = "route-boundary-secret"
        secret_path.write_text(secret, encoding="utf-8")
        base_url = self.server.url.rstrip("/")
        targets = (
            f"{base_url}/api/decision/image/%2e%2e/%2e%2e/{secret_path.name}",
            f"{base_url}/api/decision/image?generation={generation}&id=..%2F..%2F{secret_path.name}",
            f"{base_url}/api/decision/image?generation={generation}&id={quote(secret_path.as_uri(), safe='')}",
        )
        opener = build_opener(_NoRedirectHandler)
        with patch.object(
            self.server.decision,
            "image_response",
            side_effect=AssertionError("malformed image request reached the publisher"),
        ):
            for target in targets:
                with self.subTest(target=target):
                    with self.assertRaises(HTTPError) as rejected:
                        opener.open(Request(target, method="GET"), timeout=1.0)
                    self.assertIn(rejected.exception.code, (400, 404))
                    self.assertEqual(rejected.exception.headers["Cache-Control"], "no-store")
                    self.assertNotIn("Location", rejected.exception.headers)
                    self.assertNotIn(secret, rejected.exception.read().decode("utf-8"))

    def test_rejects_non_loopback_runtime_binding_before_start(self) -> None:
        automation_dir = Path(self._temporary.name) / "non-loopback-automation"
        for host in ("0.0.0.0", "192.0.2.1"):
            with self.subTest(host=host):
                with self.assertRaisesRegex(ValueError, "loopback"):
                    RuntimeViewServer(
                        vehicle_id="chase-sim-chaser",
                        automation_dir=automation_dir,
                        host=host,
                        port=0,
                    )
        self.assertFalse(automation_dir.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
