"""Public D2 contracts on disposable loopback servers, with real owned PIDs.

Coverage receipt: these are deterministic d2-fixture records, not Chase/Pi live
evidence. Tests cover current/retained spatial association, record 13, pinning
beyond the perception cache, lifecycle/integrity refusal, partial components,
HTTP framing, CLI discovery, and representative count/item/file bounds.
Deferred: browser rendering/transactions/resize, automation scheduler wiring,
port reuse, concurrent assembly races, redirects, exact millisecond freshness
endpoints, total byte/metadata saturation, pin exhaustion/release, and every
provenance mutation. The browser preview interaction is covered here at its
HTTP contract boundary; visual acceptance remains deferred. Those cases are
not implicitly passed by this representative contract suite.
No acceptance predicate, liveness check, clock, policy, or projector is patched.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import urlsplit
import zlib
from unittest.mock import patch

from PIL import Image

from autonomy.decision.decision_data import unavailable_envelope
from autonomy.decision.memory import canonical_json_utf8
from cli.automa_cli.decision import build_decision_stream_frame, write_latest_decision_frame
from cli.automa_cli import decision_view
from cli.automa_cli.decision_view import probe_decision_view
from implementations.decision.catalog import create_shadow_proposals_engine
from tests.cli.decision.live_view_fixture import (
    DecisionFixture, LABEL, LEFT_BOX, RIGHT_BOX, REPOSITORY, SCENARIOS,
    synthetic_png, write_json,
)
from tests.support.cli_runner import run_automa


class LiveDecisionViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="automa-d2-contract-")
        self.addCleanup(self.tmp.cleanup)
        self.fixture = DecisionFixture(Path(self.tmp.name) / "vehicles")
        self.addCleanup(self.fixture.close)

    def request(
        self,
        path: str | None = None,
        *,
        method: str = "GET",
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ):
        parsed = urlsplit(self.fixture.server.url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
        try:
            connection.request(
                method,
                path or self.fixture.api_path,
                body=body,
                headers=headers or {},
            )
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def payload(self) -> dict:
        status, headers, body = self.request()
        self.assertEqual(status, 200, body)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertLessEqual(len(body), 8 * 1024 * 1024)
        return json.loads(body)

    def publish_cycle_dict(self, cycle: dict) -> dict:
        """Republish a deliberately arranged typed cycle through D2's public path."""
        f = self.fixture
        frame = build_decision_stream_frame(
            cycle,
            vehicle_id=f.vehicle_id,
            run_id=f.run_id,
            worker_pid=os.getpid(),
            activation_engine_id="shadow-proposals",
            activation_activated_at_ms=f.activation["activated_at_ms"],
        )
        write_latest_decision_frame(f.latest_path, frame)
        accepted_frame = json.loads(f.latest_path.read_text(encoding="utf-8"))
        self.assertTrue(f.server.publish_decision_frame(accepted_frame))
        f.frame = accepted_frame
        return accepted_frame

    def assert_refusal(self, status: int, reason: str, *, path: str | None = None,
                       method: str = "GET") -> None:
        actual, headers, body = self.request(path, method=method)
        self.assertEqual(actual, status, body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        payload = json.loads(body)
        self.assertEqual(payload["schema"], "automa_decision_view_v1")
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["reason"], reason)
        self.assertIsNone(payload["decision"])
        self.assertIsNone(payload["decision_sha256"])
        self.assertIsNone(payload["expires_at_ms"])
        self.assertEqual(payload["evidence"], [])
        self.assertEqual(payload["current_image"]["status"], "unavailable")
        self.assertIsNone(payload["current_image"]["url"])
        self.assertIsNone(payload["presentation"])
        self.assertIsNone(payload["host_observation"]["value"])

    def assert_image(self, descriptor: dict, expected: bytes) -> None:
        status, headers, body = self.request(descriptor["url"])
        self.assertEqual(status, 200, body)
        self.assertEqual(body, expected)
        digest = hashlib.sha256(expected).hexdigest()
        self.assertEqual(descriptor["sha256"], digest)
        self.assertEqual(headers["X-Automa-Image-Sha256"], digest)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(int(headers["Content-Length"]), len(expected))
        self.assertEqual(descriptor["byte_length"], len(expected))
        self.assertEqual((descriptor["width_px"], descriptor["height_px"]), (320, 180))
        identity = {k: v for k, v in descriptor.items() if k not in {"status", "reason", "image_id", "url"}}
        self.assertEqual(descriptor["image_id"], hashlib.sha256(canonical_json_utf8(identity)).hexdigest())
        head_status, head_headers, head_body = self.request(descriptor["url"], method="HEAD")
        self.assertEqual(head_status, 200)
        self.assertEqual(head_body, b"")
        for key in ("Content-Length", "Content-Type", "X-Automa-Image-Sha256", "Cache-Control"):
            self.assertEqual(head_headers[key], headers[key])

    def test_probe_requires_established_identity_and_accepted_decision(self) -> None:
        f = self.fixture
        f.arrange("current-left")
        payload = self.payload()

        class ProbeResponse:
            status = 200

            def __init__(self, body):
                self.body = canonical_json_utf8(body)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size=-1):
                body, self.body = self.body, b""
                return body

        class ProbeOpener:
            def __init__(self, body):
                self.body = body

            def open(self, _request, timeout):
                self.timeout = timeout
                return ProbeResponse(self.body)

        cases = []
        missing_identity = deepcopy(payload)
        missing_identity["identity"] = None
        missing_identity["generation_id"] = None
        cases.append(("missing identity", missing_identity, "generation_mismatch"))
        malformed_decision = deepcopy(payload)
        malformed_decision["decision"] = {"not": "an accepted stream frame"}
        cases.append(("malformed decision", malformed_decision, "decision_invalid"))
        inflated_freshness = deepcopy(payload)
        inflated_freshness["max_age_ms"] *= 2
        inflated_freshness["expires_at_ms"] = (
            inflated_freshness["decision"]["published_at_ms"]
            + inflated_freshness["max_age_ms"]
        )
        cases.append(("inflated freshness", inflated_freshness, "decision_invalid"))
        for label, response_payload, reason in cases:
            with self.subTest(label=label), patch(
                "cli.automa_cli.decision_view.build_opener",
                return_value=ProbeOpener(response_payload),
            ):
                result = probe_decision_view(
                    automation_dir=f.automation_dir,
                    vehicle_id=f.vehicle_id,
                    activation=f.activation,
                )
                self.assertFalse(result["available"])
                self.assertIsNone(result["url"])
                self.assertEqual(result["reason"], reason)

        with patch(
            "cli.automa_cli.decision_view.build_opener",
            return_value=ProbeOpener(payload),
        ), patch("cli.automa_cli.decision_view.is_pid_alive", return_value=False):
            result = probe_decision_view(
                automation_dir=f.automation_dir,
                vehicle_id=f.vehicle_id,
                activation=f.activation,
            )
        self.assertFalse(result["available"])
        self.assertIsNone(result["url"])
        self.assertEqual(result["reason"], "decision_stale")

    def test_bounded_probe_body_stops_a_trickling_response_at_deadline(self) -> None:
        class TricklingResponse:
            def __init__(self):
                self.reads = 0

            def read(self, _size=-1):
                self.reads += 1
                return b"x"

        response = TricklingResponse()
        with patch(
            "cli.automa_cli.decision_view.time.monotonic",
            side_effect=(0.0, 0.04, 0.06),
        ):
            with self.assertRaisesRegex(
                TimeoutError, "exceeded its total budget"
            ):
                decision_view._bounded_response_body(response, deadline=0.05)
        self.assertEqual(response.reads, 1)

    def test_public_probe_bounds_real_response_and_error_trickle(self) -> None:
        f = self.fixture
        f.arrange("current-left")
        record = json.loads(f.server.record_path.read_text(encoding="utf-8"))
        body = b"x" * 400
        response_status = {"value": 200}

        class TricklingHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:
                self.send_response(response_status["value"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                for offset in range(0, len(body), 20):
                    try:
                        self.wfile.write(body[offset : offset + 20])
                        self.wfile.flush()
                    except OSError:
                        return
                    time.sleep(0.03)

            def log_message(self, *_args) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), TricklingHandler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            write_json(
                f.server.record_path,
                {**record, "url": f"http://127.0.0.1:{server.server_port}/"},
            )
            for status in (200, 503):
                response_status["value"] = status
                started = time.monotonic()
                result = probe_decision_view(
                    automation_dir=f.automation_dir,
                    vehicle_id=f.vehicle_id,
                    activation=f.activation,
                )
                elapsed = time.monotonic() - started
                with self.subTest(status=status):
                    self.assertFalse(result["available"])
                    self.assertEqual(result["reason"], "view_unreachable")
                    self.assertGreaterEqual(elapsed, 0.20)
                    self.assertLess(
                        elapsed,
                        0.45,
                        f"probe exceeded its 250 ms total budget: {elapsed:.3f}s",
                    )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_image_pixel_header_limit_is_checked_before_full_decode(self) -> None:
        def chunk(kind: bytes, data: bytes) -> bytes:
            return (
                struct.pack(">I", len(data))
                + kind
                + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
            )

        oversized_png = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 5000, 4000, 8, 2, 0, 0, 0))
            + chunk(b"IEND", b"")
        )
        with patch.object(Image.Image, "load", side_effect=AssertionError("decoded")):
            with self.assertRaisesRegex(
                decision_view._CaptureRefusal, "max_pixels_per_image"
            ):
                decision_view._decode_image_dimensions(oversized_png)

    def test_unsupported_image_format_is_refused_before_full_decode(self) -> None:
        output = io.BytesIO()
        Image.new("RGB", (20, 20), color=(20, 40, 60)).save(output, format="BMP")
        with patch.object(Image.Image, "load", side_effect=AssertionError("decoded")):
            with self.assertRaisesRegex(
                decision_view._CaptureRefusal, "only PNG/JPEG decision images"
            ):
                decision_view._decode_image_dimensions(output.getvalue())

    def test_non_identity_exif_orientation_is_refused_at_image_ingress(self) -> None:
        image = Image.new("RGB", (320, 180), color=(20, 40, 60))
        exif = Image.Exif()
        exif[274] = 6
        output = io.BytesIO()
        image.save(output, format="JPEG", exif=exif)
        with patch.object(Image.Image, "load", side_effect=AssertionError("decoded")):
            with self.assertRaisesRegex(
                decision_view._CaptureRefusal, "image_orientation_unsupported"
            ) as refusal:
                decision_view._decode_image_dimensions(output.getvalue())
        self.assertEqual(refusal.exception.reason, "image_orientation_unsupported")

    def test_html_freshness_contract_is_monotonic_and_has_independent_expiry(self) -> None:
        html = (REPOSITORY / "cli" / "automa_cli" / "decision_view.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("Date.now()", html)
        self.assertIn("performance.now()", html)
        self.assertIn("payload.expires_at_ms - payload.served_at_ms", html)
        self.assertIn("displayExpiryTimer", html)
        self.assertIn("Unavailable: decision_expired", html)
        self.assertIn("serial !== transactionSerial", html)

    def test_html_leads_with_black_box_inputs_and_outputs(self) -> None:
        html = (REPOSITORY / "cli" / "automa_cli" / "decision_view.html").read_text(
            encoding="utf-8"
        )
        for landmark in (
            'section.id = "decision-overview"',
            "PR execution path",
            "Decision black box",
            "Input · structured observation",
            "Input · memory snapshot",
            "Decision · shadow proposal",
            "Output · authority boundary",
            "function detailsSection",
            "Supporting data · raw decision input envelopes",
            "Supporting evidence · source images, retained refs, and geometry",
            "Supporting record · publication, identity, freshness, and raw decision",
        ):
            with self.subTest(landmark=landmark):
                self.assertIn(landmark, html)
        self.assertLess(
            html.index("fragment.appendChild(blackBoxOverview"),
            html.index('fragment.appendChild(detailsSection("Supporting record'),
        )

    def test_html_exposes_only_a_bounded_shadow_preview_control(self) -> None:
        html = (REPOSITORY / "cli" / "automa_cli" / "decision_view.html").read_text(
            encoding="utf-8"
        )
        for landmark in (
            'id="shadow-preview"',
            'id="preview-memory"',
            'value="empty">Clear retained memory</option>',
            'id="preview-submit"',
            'method: "POST"',
            '"/api/decision/preview?generation="',
            '"automa_decision_preview_request_v0"',
            '"automa_decision_preview_v0"',
            "live decision remains unchanged",
        ):
            with self.subTest(landmark=landmark):
                self.assertIn(landmark, html)
        self.assertLess(html.index('id="shadow-preview"'), html.index('id="live-content"'))

    def test_html_preserves_disclosure_state_during_live_refresh(self) -> None:
        html = (REPOSITORY / "cli" / "automa_cli" / "decision_view.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("function captureDisclosureState", html)
        self.assertIn("function restoreDisclosureState", html)
        self.assertIn("details.dataset.disclosureKey", html)
        self.assertIn(
            "const disclosureState = captureDisclosureState(liveRoot);",
            html,
        )
        self.assertIn(
            "liveRoot.replaceChildren(fragment);\n          restoreDisclosureState(liveRoot, disclosureState);",
            html,
        )

    def test_current_payload_preserves_cycle_and_exact_image_bytes(self) -> None:
        f = self.fixture
        f.arrange("host-zero")
        payload = self.payload()
        self.assertEqual(payload["status"], "current")
        self.assertIsNone(payload["reason"])
        self.assertEqual(payload["decision"], f.frame)
        self.assertEqual(payload["decision_sha256"], hashlib.sha256(canonical_json_utf8(f.frame)).hexdigest())
        self.assertEqual(payload["identity"]["worker_pid"], os.getpid())
        self.assertEqual(payload["generation_id"], hashlib.sha256(canonical_json_utf8(payload["identity"])).hexdigest())
        self.assertEqual(payload["expires_at_ms"], f.frame["published_at_ms"] + 30000)
        evidence, = payload["evidence"]
        self.assertEqual(evidence["association"], "current")
        self.assertEqual(evidence["geometry"]["bbox_xyxy_norm"], list(LEFT_BOX))
        self.assertEqual(evidence["source_image"], payload["current_image"])
        self.assertTrue(evidence["selected"])
        self.assertEqual(evidence["source_ref"], f.frame["cycle"]["plan"]["candidates"][0]["source_refs"][0])
        self.assert_image(payload["current_image"], f.images[f.frame["frame_id"]])

    def test_presentation_exposes_canonical_inputs_selected_candidate_and_full_command(self) -> None:
        f = self.fixture
        f.arrange("host-zero")
        payload = self.payload()
        cycle = f.frame["cycle"]
        source = cycle["source"]
        presentation = payload["presentation"]

        self.assertEqual(presentation["observation"], f.frame["observation_summary"])
        memory = presentation["memory"]
        memory_value = source["memory"]["value"]
        self.assertEqual(memory["status"], source["memory"]["status"])
        self.assertEqual(memory["reason"], source["memory"]["reason"])
        self.assertEqual(memory["health"], memory_value["health"])
        self.assertEqual(memory["record_count"], memory_value["record_count"])
        self.assertEqual(memory["preview_records"], memory_value["records"][:4])
        self.assertEqual(memory["omitted_record_count"], 0)

        selected_id = cycle["plan"]["selected_proposal_id"]
        selected = next(
            candidate
            for candidate in cycle["plan"]["candidates"]
            if candidate["proposal_id"] == selected_id
        )
        self.assertEqual(presentation["selected_candidate"], selected)
        self.assertEqual(
            presentation["selected_candidate"]["command"],
            cycle["authority"]["proposed"],
        )
        self.assertIn("gear", presentation["selected_candidate"]["command"])
        self.assertEqual(
            payload["decision_sha256"],
            hashlib.sha256(canonical_json_utf8(f.frame)).hexdigest(),
        )

    def test_ready_empty_and_unavailable_memory_presentation_are_distinct(self) -> None:
        f = self.fixture
        f.arrange("inactive")
        empty_payload = self.payload()
        empty_memory = empty_payload["presentation"]["memory"]
        self.assertEqual(empty_memory["status"], "ready")
        self.assertEqual(empty_memory["health"], "empty")
        self.assertEqual(empty_memory["record_count"], 0)
        self.assertEqual(empty_memory["preview_records"], [])
        self.assertEqual(empty_memory["omitted_record_count"], 0)

        cycle = deepcopy(f.frame["cycle"])
        cycle["source"]["memory"] = unavailable_envelope(
            "memory_not_available", updated_at_ms=cycle["source"]["timestamp_ms"]
        ).to_dict()
        self.publish_cycle_dict(cycle)
        unavailable_payload = self.payload()
        unavailable_memory = unavailable_payload["presentation"]["memory"]
        self.assertEqual(unavailable_memory["status"], "unavailable")
        self.assertEqual(unavailable_memory["reason"], "memory_not_available")
        for field in ("health", "record_count", "preview_records", "omitted_record_count"):
            with self.subTest(field=field):
                self.assertIsNone(unavailable_memory[field])

    def test_memory_preview_is_bounded_and_retained_age_is_server_computed(self) -> None:
        f = self.fixture
        f.engine = create_shadow_proposals_engine(
            config=replace(f.engine.config, retained_max_age_ms=12000)
        )
        decorations = []
        for index in range(1, 13):
            record = f.capture(index)
            decorations.append(replace(record, kind="d2-fixture-decoration"))
        selected = f.capture(13)
        # Archive the selected record at its producer timestamp before using it
        # from the later current frame. Its provenance update remains 1000 ms.
        f.publish(13, (selected,))
        f.capture(14, timestamp=13000)
        f.publish(14, tuple(decorations + [selected]), timestamp=13000)
        payload = self.payload()
        source_memory = payload["decision"]["cycle"]["source"]["memory"]["value"]
        presentation_memory = payload["presentation"]["memory"]
        self.assertEqual(presentation_memory["record_count"], 13)
        self.assertEqual(presentation_memory["preview_records"], source_memory["records"][:4])
        self.assertEqual(presentation_memory["omitted_record_count"], 9)
        self.assertEqual(payload["decision"]["cycle"]["source"]["timestamp_ms"], 13000)
        selected_candidate = payload["presentation"]["selected_candidate"]
        self.assertIsNotNone(selected_candidate)
        self.assertEqual(selected_candidate["lifecycle"], "retained")
        self.assertEqual(selected_candidate["source_refs"][0]["id"], selected.record_id)
        evidence, = payload["evidence"]
        self.assertTrue(evidence["selected"])
        self.assertEqual(evidence["record_id"], "d2-fixture-record-013")
        self.assertEqual(evidence["association"], "retained")
        self.assertEqual(evidence["retained_age_ms"], 13000 - selected.provenance.updated_at_ms)
        self.assertEqual(evidence["retained_age_ms"], 12000)
        self.assertEqual(evidence["source_image"]["frame_id"], "d2-fixture-frame-013")

    def test_stale_selection_is_null_and_retained_age_preserves_producer_state(self) -> None:
        f = self.fixture
        f.arrange("stale")
        payload = self.payload()
        self.assertIsNone(payload["presentation"]["selected_candidate"])
        self.assertEqual(payload["evidence"][0]["retained_age_ms"], 1501)
        self.assertEqual(
            payload["decision"]["cycle"]["plan"]["candidates"][0]["freshness"],
            "stale",
        )

    def test_error_cycle_keeps_null_plan_without_summary_fallback(self) -> None:
        f = self.fixture
        f.arrange("current-left")
        cycle, _ = f.engine.run_cycle(
            frame_id="d2-fixture-error-frame",
            frame_index=99,
            timestamp_ms=2000,
            observation=f.observations[f.frame["frame_id"]],
            memory=None,
            host_application=object(),
        )
        self.publish_cycle_dict(cycle.to_dict())
        payload = self.payload()
        self.assertEqual(payload["decision"]["cycle"]["status"], "engine_error")
        self.assertIsNone(payload["decision"]["cycle"]["plan"])
        self.assertIsNone(payload["presentation"]["selected_candidate"])
        self.assertIsNone(payload["decision"]["plan_summary"]["selected_proposal_id"])

    def test_right_partial_has_real_geometry_and_unavailable_host_not_zero(self) -> None:
        self.fixture.arrange("current-right")
        payload = self.payload()
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["evidence"][0]["geometry"]["bbox_xyxy_norm"], list(RIGHT_BOX))
        self.assertEqual(payload["host_observation"]["status"], "unavailable")
        self.assertIsNone(payload["host_observation"]["value"])
        self.assertEqual(payload["reason"], payload["host_observation"]["reason"])
        self.assert_image(payload["current_image"], synthetic_png("right"))

    def test_retained_source_survives_newer_captures_and_source_file_removal(self) -> None:
        f = self.fixture
        f.arrange("retained")
        before = self.payload()
        for index in range(3, 13):
            f.capture(index, side="right", timestamp=1500 + index)
        # Both retained and current images must be ingress bytes even after source loss.
        for path in f.source_dir.iterdir():
            path.unlink()
        payload = self.payload()
        self.assertEqual(payload["current_image"], before["current_image"])
        self.assertEqual(payload["evidence"], before["evidence"])
        source, = payload["evidence"]
        self.assertEqual(source["association"], "retained")
        self.assertEqual(source["source_image"]["frame_id"], "d2-fixture-frame-001")
        self.assertEqual(payload["current_image"]["frame_id"], "d2-fixture-frame-002")
        self.assertEqual(source["geometry"]["bbox_xyxy_norm"], list(LEFT_BOX))
        self.assertNotEqual(source["source_image"]["image_id"], payload["current_image"]["image_id"])
        self.assert_image(source["source_image"], synthetic_png("left"))
        self.assert_image(payload["current_image"], synthetic_png("right"))
        self.assertEqual(self.request("/frame?v=d2-fixture-frame-001")[0], 404)

    def test_thirteenth_memory_record_resolves_outside_summary(self) -> None:
        f = self.fixture
        records = []
        for index in range(1, 14):
            record = f.capture(index)
            records.append(replace(record, kind="d2-fixture-decoration") if index < 13 else record)
        f.publish(13, tuple(records))
        payload = self.payload()
        self.assertEqual(len(payload["decision"]["cycle"]["source"]["memory"]["value"]["records"]), 13)
        evidence, = payload["evidence"]
        self.assertEqual(evidence["record_id"], "d2-fixture-record-013")
        self.assertEqual(evidence["status"], "available")
        self.assert_image(evidence["source_image"], synthetic_png("left"))

    def test_stale_memory_keeps_historical_ref_but_no_selected_command(self) -> None:
        self.fixture.arrange("stale")
        payload = self.payload()
        candidate, = payload["decision"]["cycle"]["plan"]["candidates"]
        self.assertEqual(candidate["lifecycle"], "stale")
        self.assertIsNone(candidate["command"])
        self.assertIsNone(payload["decision"]["cycle"]["plan"]["selected_proposal_id"])
        evidence, = payload["evidence"]
        self.assertFalse(evidence["selected"])
        self.assertEqual(evidence["association"], "retained")
        self.assert_image(evidence["source_image"], synthetic_png("left"))

    def test_inactive_cycle_clears_prior_selection(self) -> None:
        self.fixture.arrange("inactive")
        payload = self.payload()
        self.assertEqual(payload["evidence"], [])
        candidate, = payload["decision"]["cycle"]["plan"]["candidates"]
        self.assertEqual(candidate["lifecycle"], "inactive")
        self.assertIsNone(candidate["command"])

    def test_missing_ingress_never_reads_existing_observation_artifact(self) -> None:
        f = self.fixture
        f.arrange("missing-image")
        self.assertTrue((f.source_dir / "d2-fixture-frame-001.png").exists())
        payload = self.payload()
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["decision"], f.frame)
        self.assertEqual(payload["current_image"]["status"], "unavailable")
        evidence, = payload["evidence"]
        self.assertIsNotNone(evidence["provenance"])
        self.assertEqual(evidence["geometry"]["status"], "unavailable")
        self.assertIsNone(evidence["source_image"]["url"])

    def test_zone_only_geometry_is_partial_without_invented_box(self) -> None:
        self.fixture.arrange("missing-geometry")
        payload = self.payload()
        evidence, = payload["evidence"]
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(evidence["association"], "current")
        self.assertEqual(evidence["source_image"]["status"], "available")
        self.assertEqual(evidence["reason"], "geometry_missing")
        self.assertEqual(evidence["geometry"]["reason"], "geometry_missing")
        self.assertIsNone(evidence["geometry"]["bbox_xyxy_norm"])
        self.assertIsNone(evidence["geometry"]["polygon_xy_norm"])
        self.assertIsNone(evidence["geometry"]["transform"])

    def test_observed_nonzero_is_separate_from_proposed_and_authorized_idle(self) -> None:
        self.fixture.arrange("host-nonzero")
        payload = self.payload()
        authority = payload["decision"]["cycle"]["authority"]
        self.assertFalse(authority["proposed_applied"])
        self.assertNotEqual(authority["proposed"]["steering"], 0)
        self.assertEqual(authority["authorized_output"]["steering"], 0)
        self.assertEqual(authority["authorized_output"]["throttle"], 0)
        self.assertEqual(payload["host_observation"]["status"], "available")
        self.assertEqual(payload["host_observation"]["value"]["host_output"]["steering"], 0.2)
        self.assertEqual(payload["host_observation"]["value"], authority["host_application"]["value"])

    def test_mismatched_or_generic_host_report_is_partial_and_raw_preserved(self) -> None:
        f = self.fixture
        f.arrange("host-zero")
        for mutation, reason in (("frame", "host_observation_frame_mismatch"),
                                 ("generic", "host_observation_unsupported")):
            with self.subTest(mutation=mutation):
                cycle = deepcopy(f.cycle.to_dict())
                value = cycle["authority"]["host_application"]["value"]
                if mutation == "frame":
                    value["frame_id"] = "d2-fixture-other-frame"
                else:
                    cycle["authority"]["host_application"]["value"] = {"fixture": LABEL}
                frame = build_decision_stream_frame(
                    cycle, vehicle_id=f.vehicle_id, run_id=f.run_id, worker_pid=os.getpid(),
                    activation_engine_id="shadow-proposals",
                    activation_activated_at_ms=f.activation["activated_at_ms"],
                )
                write_latest_decision_frame(f.latest_path, frame)
                # The accepted decision is the actual JSON publication.  Its
                # decoded representation canonicalizes tuple fields to lists.
                accepted_frame = json.loads(f.latest_path.read_text(encoding="utf-8"))
                payload = self.payload()
                self.assertEqual(payload["status"], "partial")
                self.assertEqual(payload["host_observation"]["reason"], reason)
                self.assertIsNone(payload["host_observation"]["value"])
                self.assertEqual(payload["decision"], accepted_frame)

    def test_conflicting_capture_bytes_refuse_association(self) -> None:
        f = self.fixture
        f.arrange("current-left")
        prior = self.payload()["current_image"]
        f.publisher.publish_capture(frame_bytes=synthetic_png("right"),
                                    frame_record=f.captures[f.frame["frame_id"]], content_type="image/png")
        payload = self.payload()
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["current_image"]["reason"], "source_conflict")
        self.assertEqual(payload["evidence"][0]["geometry"]["status"], "unavailable")
        self.assertNotEqual(self.request(prior["url"])[0], 200)

    def test_publication_missing_malformed_future_and_stale_refuse_cached_data(self) -> None:
        f = self.fixture
        f.arrange("current-left")
        image_url = self.payload()["current_image"]["url"]
        original = deepcopy(f.frame)
        cases = (
            ("missing", 503, "decision_missing"),
            ("malformed", 422, "decision_invalid"),
            ("future", 503, "decision_stale"),
            ("stale", 503, "decision_stale"),
        )
        for case, status, reason in cases:
            with self.subTest(case=case):
                frame = deepcopy(original)
                if case == "missing":
                    f.latest_path.unlink()
                elif case == "malformed":
                    frame["plan_summary"] = {}
                    write_latest_decision_frame(f.latest_path, frame)
                else:
                    frame["published_at_ms"] = int(time.time() * 1000) + (60000 if case == "future" else -60000)
                    write_latest_decision_frame(f.latest_path, frame)
                self.assert_refusal(status, reason)
                self.assert_refusal(status, reason, path=image_url)
                info = f.info()["combined_view"]
                self.assertFalse(info["available"])
                self.assertIsNone(info["url"])
                self.assertEqual(info["reason"], reason)
                write_latest_decision_frame(f.latest_path, original)

    def test_state_and_config_generation_mutations_refuse_data_and_images(self) -> None:
        f = self.fixture
        f.arrange("current-left")
        image_url = self.payload()["current_image"]["url"]
        for field, value, reason in (("run_id", "d2-fixture-other-run", "generation_mismatch"),
                                     ("vehicle_id", "d2-fixture-other-vehicle", "vehicle_mismatch"),
                                     ("pid", os.getpid() + 1, "generation_mismatch")):
            with self.subTest(field=field):
                write_json(f.state_path, {**f.state, field: value})
                self.assert_refusal(409, reason)
                self.assert_refusal(409, reason, path=image_url)
                write_json(f.state_path, f.state)
        activation = deepcopy(f.activation)
        activation["decision"]["engine_config"]["steer_magnitude"] = 0.45
        write_json(f.activation_path, activation)
        self.assert_refusal(409, "generation_mismatch")
        self.assert_refusal(409, "generation_mismatch", path=image_url)
        view = f.info()["combined_view"]
        self.assertFalse(view["available"])
        self.assertIsNone(view["url"])

    def test_stopped_completed_and_absent_state_refuse_while_listener_is_alive(self) -> None:
        f = self.fixture
        f.arrange("current-left")
        image_url = self.payload()["current_image"]["url"]
        for status in ("stopped", "completed", "error"):
            with self.subTest(status=status):
                write_json(f.state_path, {**f.state, "status": status})
                self.assert_refusal(503, "producer_unavailable")
                self.assert_refusal(503, "producer_unavailable", path=image_url)
        f.state_path.unlink()
        self.assert_refusal(503, "producer_unavailable")
        write_json(f.state_path, f.state)
        f.publisher.stop()
        self.assert_refusal(503, "producer_unavailable")

    def test_http_methods_queries_hashes_and_headers(self) -> None:
        f = self.fixture
        f.arrange("current-left")
        generation = f.publisher.generation_id
        for path in ("/api/decision/latest", "/api/decision/latest?generation=bad",
                     f.api_path + "&generation=" + generation, f.api_path + "&extra=1",
                     "/api/decision/images/../active.json?generation=" + generation,
                     "/api/decision/images/%2e%2e%2factive.json?generation=" + generation,
                     "/api/decision/images/https%3A%2F%2Fexample.com?generation=" + generation):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 400)
        self.assert_refusal(409, "generation_mismatch", path="/api/decision/latest?generation=" + "0" * 64)
        self.assertEqual(self.request("/api/decision/images/" + "0" * 64 + "?generation=" + generation)[0], 404)
        for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS", "TRACE", "CONNECT"):
            with self.subTest(method=method):
                self.assert_refusal(405, "method_not_allowed", method=method)
                self.assertEqual(self.request(method=method)[1]["Allow"], "GET, HEAD")
        for path in (f.api_path, "/decision?generation=" + generation):
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 200)
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
                self.assertIn("img-src 'self' blob:", headers["Content-Security-Policy"])
                self.assertEqual(int(headers["Content-Length"]), len(body))
                head_status, head_headers, head_body = self.request(path, method="HEAD")
                self.assertEqual(head_status, 200)
                self.assertEqual(head_body, b"")
                self.assertEqual(head_headers["Content-Type"], headers["Content-Type"])
        # This validates shell delivery only, not JavaScript execution or visual acceptance.
        self.assertEqual(self.request("/no-such-d2-route")[0], 404)

    def test_shadow_preview_changes_plan_without_replacing_live_decision(self) -> None:
        f = self.fixture
        f.arrange("host-zero")
        baseline = self.payload()
        latest_bytes = f.latest_path.read_bytes()
        generation = f.publisher.generation_id
        request = {
            "schema": "automa_decision_preview_request_v0",
            "base_decision_sha256": baseline["decision_sha256"],
            "memory_mode": "empty",
        }
        status, headers, body = self.request(
            f"/api/decision/preview?generation={generation}",
            method="POST",
            body=json.dumps(request).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        preview = json.loads(body)
        self.assertEqual(preview["schema"], "automa_decision_preview_v0")
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(preview["generation_id"], generation)
        self.assertEqual(preview["base_decision_sha256"], baseline["decision_sha256"])
        self.assertEqual(preview["preview"]["memory_mode"], "empty")
        self.assertTrue(preview["preview"]["live_decision_unchanged"])
        self.assertEqual(
            baseline["decision"]["cycle"]["plan"]["status"],
            "selected",
        )
        self.assertEqual(preview["decision"]["cycle"]["plan"]["status"], "idle")
        self.assertIsNone(preview["decision"]["cycle"]["plan"]["selected_proposal_id"])
        self.assertIsNone(preview["decision"]["cycle"]["authority"]["proposed"])
        self.assertEqual(
            preview["decision"]["cycle"]["authority"]["authorized_output"]["reason"],
            "shadow-only-idle",
        )
        self.assertEqual(f.latest_path.read_bytes(), latest_bytes)
        after = self.payload()
        self.assertEqual(after["decision"], baseline["decision"])
        self.assertEqual(after["decision_sha256"], baseline["decision_sha256"])

    def test_shadow_preview_is_generation_and_body_bound(self) -> None:
        f = self.fixture
        f.arrange("current-left")
        generation = f.publisher.generation_id
        preview_path = f"/api/decision/preview?generation={generation}"
        for body, expected_status, expected_reason in (
            (b"{}", 400, "preview_invalid"),
            (
                json.dumps(
                    {
                        "schema": "automa_decision_preview_request_v0",
                        "base_decision_sha256": "0" * 64,
                        "memory_mode": "current",
                    }
                ).encode("utf-8"),
                409,
                "preview_stale",
            ),
        ):
            with self.subTest(expected_reason=expected_reason):
                status, _headers, raw = self.request(
                    preview_path,
                    method="POST",
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(status, expected_status, raw)
                self.assertEqual(json.loads(raw)["reason"], expected_reason)
        for method in ("GET", "HEAD", "PUT", "DELETE"):
            with self.subTest(method=method):
                status, headers, _body = self.request(preview_path, method=method)
                self.assertEqual(status, 405)
                self.assertEqual(headers["Allow"], "POST")

    def test_count_item_and_decision_file_bounds_are_publicly_visible(self) -> None:
        f = self.fixture
        f.arrange("current-left")
        source = self.payload()["current_image"]
        for index in range(2, 68):
            f.capture(index, timestamp=1000 + index)
        f.publisher.publish_capture(
            frame_bytes=b"x" * (8388608 + 1),
            frame_record={**f.captures[f.frame["frame_id"]], "frame_id": "d2-fixture-too-large"},
            content_type="image/png",
        )
        payload = self.payload()
        limits = payload["limits"]
        for key, expected in {"max_images": 64, "max_image_bytes": 8388608,
                              "max_total_image_bytes": 33554432, "max_pixels_per_image": 16000000,
                              "max_metadata_bytes": 8388608, "max_response_bytes": 8388608}.items():
            self.assertEqual(limits[key], expected)
        self.assertLessEqual(limits["image_count"], 64)
        self.assertLessEqual(limits["image_bytes"], limits["max_total_image_bytes"])
        self.assertLessEqual(limits["metadata_bytes"], limits["max_metadata_bytes"])
        self.assertGreaterEqual(limits["size_refusals"], 1)
        self.assert_image(source, synthetic_png("left"))
        # Valid JSON padded over the D2 file ceiling, not a fake huge cycle schema.
        f.latest_path.write_bytes(canonical_json_utf8(f.frame) + b" " * 8388608)
        self.assert_refusal(503, "view_payload_too_large")

    def test_info_cli_exposes_only_probed_urls_and_preserves_unavailable_success(self) -> None:
        f = self.fixture
        view = f.info()["combined_view"]
        self.assertFalse(view["available"])
        self.assertIsNone(view["url"])
        self.assertEqual(view["reason"], "decision_missing")
        f.arrange("current-left")
        info = f.info()
        view = info["combined_view"]
        self.assertEqual(info["schema"], "vehicle_decision_info_v0")
        self.assertTrue(view["available"])
        self.assertEqual(view["status"], "partial")
        self.assertEqual(view["api_url"], f.server.url.rstrip("/") + f.api_path)
        human = run_automa("vehicles", "info", "decision", "--id", f.vehicle_id,
                            runtime_root=f.runtime_root)
        self.assertIn(view["url"], human.stdout)
        stream = run_automa("vehicles", "stream", "decision", "--id", f.vehicle_id,
                             "--once", "--json", runtime_root=f.runtime_root)
        self.assertEqual(json.loads(stream.stdout), f.frame)
        f.server.stop()
        unavailable = f.info()["combined_view"]
        self.assertFalse(unavailable["available"])
        self.assertIsNone(unavailable["url"])
        self.assertIsNone(unavailable["api_url"])
        self.assertTrue(unavailable["reason"])

    def test_probe_rejects_external_origin_and_ignores_record_supplied_api_url(self) -> None:
        f = self.fixture
        f.arrange("current-left")
        record = json.loads(f.server.record_path.read_text())
        write_json(f.server.record_path, {**record, "url": "https://example.invalid/"})
        result = probe_decision_view(automation_dir=f.automation_dir, vehicle_id=f.vehicle_id,
                                     activation=f.activation)
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "view_unreachable")
        self.assertIsNone(result["url"])
        write_json(f.server.record_path, {**record, "api_url": "https://example.invalid/"})
        result = probe_decision_view(automation_dir=f.automation_dir, vehicle_id=f.vehicle_id,
                                     activation=f.activation)
        self.assertTrue(result["available"])
        self.assertEqual(result["api_url"], f.server.url.rstrip("/") + f.api_path)


class DisposableFixtureProcessTests(unittest.TestCase):
    def test_required_arguments_and_preexisting_runtime_refusal(self) -> None:
        script = REPOSITORY / "tests/cli/decision/live_view_fixture.py"
        result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--runtime-root", result.stderr)
        self.assertIn("--vehicle-id", result.stderr)
        with tempfile.TemporaryDirectory(prefix="automa-d2-refusal-") as tmp:
            vehicle = Path(tmp) / "d2-fixture"
            vehicle.mkdir()
            marker = vehicle / "keep.txt"
            marker.write_text("owned preexisting bytes")
            with self.assertRaisesRegex(ValueError, "preexisting"):
                DecisionFixture(Path(tmp))
            self.assertEqual(marker.read_text(), "owned preexisting bytes")
            self.assertEqual(list(vehicle.iterdir()), [marker])
            for bad_id in ("../elsewhere", "/absolute", "a/b"):
                with self.subTest(vehicle_id=bad_id), self.assertRaises(ValueError):
                    DecisionFixture(Path(tmp), bad_id)

    def test_real_fixture_process_prints_probed_url_and_stops_only_its_listener(self) -> None:
        with tempfile.TemporaryDirectory(prefix="automa-d2-process-") as tmp:
            runtime_root = Path(tmp) / "vehicles"
            env = {k: v for k, v in os.environ.items()
                   if k not in {"AUTOMA_TEST_LIVE_SIM", "AUTOMA_TEST_LIVE_PI"}}
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            process = subprocess.Popen([
                sys.executable, str(REPOSITORY / "tests/cli/decision/live_view_fixture.py"),
                "--runtime-root", str(runtime_root), "--vehicle-id", "d2-fixture",
                "--scenario", "retained", "--port", "0", "--duration-s", "30",
            ], cwd=REPOSITORY, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                ready, _, _ = select.select([process.stdout], [], [], 20)
                self.assertTrue(ready, "fixture did not emit a bounded startup receipt")
                line = process.stdout.readline()
                self.assertTrue(line, "fixture exited before its startup receipt")
                receipt = json.loads(line)
                self.assertEqual(receipt["fixture"], LABEL)
                self.assertEqual(receipt["pid"], process.pid)
                self.assertEqual(receipt["runtime_root"], str(runtime_root.resolve()))
                self.assertIn(str(runtime_root.resolve()), receipt["info_command"])
                self.assertIsNotNone(receipt["url"])
                info = run_automa("vehicles", "info", "decision", "--id", "d2-fixture",
                                  "--json", runtime_root=runtime_root)
                self.assertEqual(json.loads(info.stdout)["combined_view"]["url"], receipt["url"])
                process.terminate()
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stdout + stderr)
                stopped = run_automa("vehicles", "info", "decision", "--id", "d2-fixture",
                                     "--json", runtime_root=runtime_root)
                self.assertIsNone(json.loads(stopped.stdout)["combined_view"]["url"])
                self.assertTrue(Path(receipt["source_records"]).exists())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()


if __name__ == "__main__":
    unittest.main()
