from __future__ import annotations
import json
import os
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import urlopen
from unittest.mock import patch
from cli.automa_cli import decision as decision_module
from cli.automa_cli.decision import get_vehicle_decision_info, update_vehicle_decision
from cli.automa_cli.loopback_http import LoopbackHTTPRequestHandler
from cli.automa_cli.runtime_view import RuntimeViewServer
from tests.cli.decision.live_runtime_decision_view_fixtures import (
    LiveRuntimeDecisionViewFixture,
)


class LiveRuntimeDecisionViewTests(LiveRuntimeDecisionViewFixture, unittest.TestCase):
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
                    raise AssertionError(
                        "test did not release the deliberately slow reader"
                    )
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
        info = get_vehicle_decision_info(
            vehicle_id="chase-sim-chaser", json_output=True
        )
        self.assertEqual(info.exit_code, 0, info.message)
        combined = json.loads(info.message)["combined_view"]
        self.assertTrue(combined["available"])
        self.assertEqual(combined["status"], "warming")
        self.assertEqual(
            combined["url"], f"{self.server.url}decision?generation={generation}"
        )

    def test_stale_decision_is_unavailable_through_api(self) -> None:
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

    def test_old_session_is_unavailable_after_public_activation_restaging(self) -> None:
        self._publish_exact_transaction()
        old_generation = self.server.decision.generation_id
        self.assertIsNotNone(old_generation)
        old_latest_url = (
            f"{self.server.url}api/decision/latest?generation={old_generation}"
        )
        with urlopen(old_latest_url, timeout=1.0) as response:
            old_payload = json.loads(response.read().decode("utf-8"))
        old_image_url = (
            f"{self.server.url.rstrip('/')}{old_payload['current_image']['url']}"
        )

        # Use the public update command to replace the active decision
        # generation while the old producer is still serving its URL.
        restage = update_vehicle_decision(
            vehicle_id="chase-sim-chaser",
            engine_id="idle",
            json_output=True,
        )
        self.assertEqual(restage.exit_code, 0, restage.message)
        current_activation = json.loads(
            self.activation_path.read_text(encoding="utf-8")
        )
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

        new_latest_url = (
            f"{replacement.url}api/decision/latest?generation={new_generation}"
        )
        with urlopen(new_latest_url, timeout=1.0) as response:
            replacement_payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(replacement_payload["decision"]["run_id"], "run-replacement")
        self.assertEqual(
            replacement_payload["decision"]["frame_id"], replacement_frame["frame_id"]
        )
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
