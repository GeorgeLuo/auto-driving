"""A fast host must not split the CLI viewer's image and findings."""
from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from PIL import Image

from autonomy.decision_cycle.cycle import CYCLE_RECORD_KEYS
from autonomy.runtime.frame_loop import FRAME_TRANSACTION_SCHEMA, FramePublication
from autonomy.runtime.report import HostReport, ReportApplication
from cli.automa_cli.hosts.publications import host_publications as publications
from cli.automa_cli.hosts.views import view_feed as viewer


class _Response(io.BytesIO):
    status = 200

    def __init__(self, payload, *, headers=None):
        super().__init__(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
        self.headers = {"ETag": 'W/"test"'} if headers is None else headers


def _publication(frame_id="first") -> FramePublication:
    return FramePublication(
        health="healthy",
        read_at_ms=1000,
        result_age_ms=0,
        stale_after_ms=1000,
        report=HostReport(
            host_name="fixture", run_id="run", generation_id="decision-test",
            frame_id=frame_id, frame_index=1, timestamp_ms=900, published_at_ms=1000,
            cycle=dict.fromkeys(CYCLE_RECORD_KEYS),
            application=ReportApplication(False, "observe_only", "idle", 0.0, 0.0),
            values={"source_id": "fixture:local", "stale_after_ms": 1000, "activation": {"steps": {}}},
        ),
    )


def _warming() -> dict[str, object]:
    return {
        "schema": "host_frame_publication",
        "ok": False,
        "health": "warming",
        "read_at_ms": 1000,
        "result_age_ms": None,
        "stale_after_ms": 1000,
        "report": None,
    }


def _jpeg(size, color) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return buffer.getvalue()


def _transaction(frame_id="first"):
    return {
        "schema": FRAME_TRANSACTION_SCHEMA,
        "publication": _publication(frame_id).to_dict(),
        "image_frame_id": frame_id,
        "image_content_type": "image/jpeg",
        "image_base64": base64.b64encode(b"jpeg").decode(),
        "image_error": None,
    }


class ObservationSampleTests(unittest.TestCase):
    def _feed(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        with patch.object(viewer, "runtime_view_dir", return_value=root):
            feed = viewer.ViewFeed("fixture", base_url="http://host.invalid", timeout_s=1.0)
        self.addCleanup(feed.stop)
        self.assertIsNone(feed.error)
        self.enterContext(patch("cli.automa_cli.hosts.views.decision_live.fetch_host_telemetry_latest",
                                side_effect=ConnectionError("no telemetry")))
        return feed

    def test_conditional_observation_read_reuses_image_without_downloading_it(self):
        cache = publications.TransactionCache()
        first = _Response(_transaction(), headers={"ETag": 'W/"first"'})
        unchanged = HTTPError("http://host.invalid", 304, "unchanged", {"X-Read-At-Ms": "2000"}, io.BytesIO())
        with patch.object(publications.urllib.request, "urlopen", side_effect=[first, unchanged]) as read:
            publication, jpeg = publications.fetch_frame_sample("http://host.invalid", cache=cache)
            publication, again = publications.fetch_frame_sample("http://host.invalid", cache=cache)
        self.assertIs(again, jpeg)
        self.assertEqual(publication.read_at_ms, 2000)
        self.assertEqual(read.call_args.args[0].get_header("If-none-match"), 'W/"first"')

    def test_unavailable_transaction_invalidates_image_cache_and_can_recover(self):
        cache = publications.TransactionCache()
        unavailable = HTTPError("http://host.invalid", 503, "warming", {}, io.BytesIO(json.dumps({
            "schema": FRAME_TRANSACTION_SCHEMA,
            "publication": _warming(),
            "image_base64": None,
        }).encode()))
        replies = [_Response(_transaction(), headers={"ETag": 'W/"first"'}), unavailable,
                   _Response(_transaction(), headers={"ETag": 'W/"first"'})]
        with patch.object(publications.urllib.request, "urlopen", side_effect=replies):
            publications.fetch_frame_sample("http://host.invalid", cache=cache)
            publication, jpeg = publications.fetch_frame_sample("http://host.invalid", cache=cache)
            self.assertIsNone(jpeg)
            self.assertIsNone(cache.image)
            self.assertEqual(publication.health, "warming")
            self.assertIsNone(publication.report)
            _, jpeg = publications.fetch_frame_sample("http://host.invalid", cache=cache)
        self.assertEqual(jpeg, b"jpeg")

    def test_a_refused_transaction_clears_the_cache_before_a_later_304(self):
        cache = publications.TransactionCache()
        unavailable = HTTPError("http://host.invalid", 503, "unavailable", {}, io.BytesIO(json.dumps({
            "publication": _warming(), "image_base64": None,
        }).encode()))
        unchanged = HTTPError("http://host.invalid", 304, "unchanged", {}, io.BytesIO())
        with patch.object(publications.urllib.request, "urlopen",
                side_effect=[_Response(_transaction()), unavailable, unchanged]) as read:
            publications.fetch_frame_sample("http://host.invalid", cache=cache)
            cache.image = b"jpeg", "image/jpeg"
            publications.fetch_frame_sample("http://host.invalid", cache=cache)
            self.assertEqual((cache.etag, cache.payload, cache.image), (None, None, None))
            with self.assertRaisesRegex(ConnectionError, "without a cached transaction"):
                publications.fetch_frame_sample("http://host.invalid", cache=cache)
            self.assertIsInstance(read.call_args.args[0], str)
            self.assertEqual(read.call_count, 3)

    def test_a_body_read_timeout_clears_the_cache(self):
        class _Stalled(_Response):
            def read(self, *_args):
                raise TimeoutError()

        cache = publications.TransactionCache("old", {"publication": {}}, (b"old", "image/jpeg"))
        with patch.object(publications.urllib.request, "urlopen", return_value=_Stalled(_transaction())):
            with self.assertRaisesRegex(ConnectionError, "did not answer within"):
                publications.fetch_frame_sample("http://host.invalid", cache=cache)
            self.assertEqual((cache.etag, cache.payload, cache.image), (None, None, None))

    def test_observation_transaction_requires_an_updated_host(self):
        for response in (_Response(_transaction(), headers={}),
                         HTTPError("http://host.invalid", 404, "missing", {}, io.BytesIO())):
            cache = publications.TransactionCache("old", {"publication": {}}, (b"old", "image/jpeg"))
            with self.subTest(response=response), patch.object(
                    publications.urllib.request, "urlopen", side_effect=[response]) as read:
                with self.assertRaisesRegex(ConnectionError, "Incompatible host.*update"):
                    publications.fetch_frame_sample("http://host.invalid", cache=cache)
                self.assertEqual((cache.etag, cache.payload, cache.image), (None, None, None))
                self.assertEqual(read.call_count, 1)

    def test_atomic_sample_needs_only_one_request(self):
        with patch.object(publications.urllib.request, "urlopen", return_value=_Response(_transaction())) as read:
            publication, jpeg = publications.fetch_frame_sample("http://host.invalid")
        self.assertEqual(publication, _publication())
        self.assertEqual(jpeg, b"jpeg")
        self.assertEqual(read.call_count, 1)
        self.assertEqual(read.call_args.args[0], "http://host.invalid/host/observation/transaction")

    def test_warming_returns_no_image(self):
        payload = _transaction()
        payload.update(publication=_warming(), image_base64=None)
        with patch.object(publications.urllib.request, "urlopen", return_value=_Response(payload)):
            publication, jpeg = publications.fetch_frame_sample("http://host.invalid")
        self.assertEqual(publication.health, "warming")
        self.assertIsNone(publication.report)
        self.assertIsNone(jpeg)

    def test_invalid_transactions_do_not_hide_a_report(self):
        for change in ({"schema": "wrong"}, {"publication": None}):
            with self.subTest(change=change):
                with patch.object(publications.urllib.request, "urlopen",
                                  return_value=_Response({**_transaction(), **change})) as read:
                    with self.assertRaises(ConnectionError):
                        publications.fetch_frame_sample("http://host.invalid")
                self.assertEqual(read.call_count, 1)
        for change in (
            {"image_frame_id": "second"},
            {"image_content_type": "image/png"},
            {"image_base64": "!"},
            {"image_base64": ""},
            {"image_base64": None},
            {"image_error": "image_unavailable"},
        ):
            with self.subTest(change=change):
                with patch.object(publications.urllib.request, "urlopen",
                                  return_value=_Response({**_transaction(), **change})) as read:
                    publication, jpeg = publications.fetch_frame_sample("http://host.invalid")
                self.assertEqual(read.call_count, 1)
                self.assertIsNone(jpeg)
                self.assertEqual(publication.report.frame_id, "first")

    def test_transport_and_server_errors_do_not_fall_back(self):
        failures = (
            URLError("offline"), TimeoutError(),
            HTTPError("http://host.invalid", 503, "failed", {}, io.BytesIO(b'{"error":"bad image"}')),
        )
        for failure in failures:
            with self.subTest(failure=failure):
                with patch.object(publications.urllib.request, "urlopen", side_effect=failure) as read:
                    with self.assertRaises(ConnectionError):
                        publications.fetch_frame_sample("http://host.invalid")
                self.assertEqual(read.call_count, 1)

    def test_stream_view_uses_the_transaction_metadata_when_the_host_advances(self):
        feed = self._feed()
        jpeg = _jpeg((4, 4), (1, 2, 3))
        with patch.object(viewer, "fetch_frame_sample", return_value=(_publication("second"), jpeg)):
            feed.publish(_publication("first"))
        payload = json.loads(feed.server.perception.latest_json())
        self.assertEqual(payload["report"]["frame_id"], "second")
        self.assertEqual(payload["image"]["frame_id"], "second")
        self.assertIsNone(feed.error)
        self.assertIsNone(feed.decision_error)

    def test_stream_terminal_and_view_share_one_read_and_skip_unchanged_publication(self):
        feed = self._feed()
        jpeg = _jpeg((4, 4), (1, 2, 3))
        with (
            patch.object(viewer, "fetch_frame_sample", return_value=(_publication(), jpeg)) as read,
            patch.object(feed.server.perception, "publish", wraps=feed.server.perception.publish) as publish,
        ):
            publication, error = feed.read_observation()
            self.assertIsNone(error)
            feed.publish(publication)
            first_count = publish.call_count
            feed.publish(publication)
        self.assertEqual(read.call_count, 1)
        self.assertGreater(first_count, 0)
        self.assertEqual(publish.call_count, first_count)
        self.assertIsNone(feed.decision_error)

    def test_an_unavailable_capture_keeps_a_readable_report(self):
        publication, jpeg = _publication(), _jpeg((4, 4), (1, 2, 3))
        for name, unavailable in (
            ("bad image", (_publication("second"), b"not-an-image")),
            ("failed fetch", ConnectionError("offline")),
        ):
            samples = [(publication, jpeg), unavailable, (publication, jpeg)]
            with self.subTest(name), patch.object(viewer, "fetch_frame_sample", side_effect=samples):
                feed = self._feed()
                feed.publish(feed.read_observation()[0])
                self.assertIsNotNone(feed.server.perception.latest_json())
                feed.publish(feed.read_observation()[0])
                if name == "failed fetch":
                    self.assertIsNone(feed.server.perception.latest_json())
                else:
                    self.assertIsNotNone(feed.server.perception.latest_json())
                    self.assertIsNone(json.loads(feed.server.perception.latest_json())["image"]["url"])
                feed.publish(feed.read_observation()[0])
                self.assertEqual(feed.server.perception.frame(), (jpeg, "image/jpeg"))
                self.assertIsNone(feed.error)
                self.assertIsNone(feed.decision_error)
        for name, unavailable in (
            ("missing image", (publication, None)),
            ("stale frame", (replace(publication, health="stale"), jpeg)),
        ):
            samples = [(publication, jpeg), unavailable, (publication, jpeg)]
            with self.subTest(name), patch.object(viewer, "fetch_frame_sample", side_effect=samples):
                feed = self._feed()
                feed.publish(feed.read_observation()[0])
                feed.publish(feed.read_observation()[0])
                payload = json.loads(feed.server.perception.latest_json())
                self.assertEqual(payload["report"]["frame_id"], publication.report.frame_id)
                self.assertIsNone(feed.error)
                self.assertIsNone(feed.decision_error)
                if name == "stale frame":
                    self.assertEqual(payload["health"], "stale")
                    self.assertEqual(feed.server.perception.frame(), (jpeg, "image/jpeg"))
                else:
                    self.assertEqual(feed.server.perception.frame(), (jpeg, "image/jpeg"))

    def test_a_cleared_view_shows_the_same_frame_when_the_host_answers_again(self):
        publication = _publication()
        pair = publication, _jpeg((4, 4), (1, 2, 3))
        feed = self._feed()
        with patch.object(viewer, "fetch_frame_sample", side_effect=[pair, ConnectionError("down"), pair]):
            feed.publish(feed.read_observation()[0])
            self.assertIsNone(feed.decision_error)
            self.assertIsNotNone(feed.server.perception.latest_json())
            feed.read_observation()
            self.assertIsNone(feed.server.perception.latest_json())
            feed.publish(feed.read_observation()[0])
        self.assertIsNotNone(feed.server.perception.latest_json())

    def test_observation_remains_available_when_decision_refresh_fails(self):
        feed = self._feed()
        publication, jpeg = _publication(), _jpeg((4, 4), (1, 2, 3))
        feed._observation = publication, jpeg
        with patch.object(feed.decision, "publish_publication", side_effect=ConnectionError("no decision")):
            feed.publish(publication)
        self.assertEqual(feed.server.perception.frame(), (jpeg, "image/jpeg"))
        payload = json.loads(feed.server.perception.latest_json())
        self.assertEqual(payload["report"]["frame_id"], publication.report.frame_id)
        self.assertEqual(payload["image"]["frame_id"], publication.report.frame_id)
        self.assertIn("no decision", feed.decision_error)
