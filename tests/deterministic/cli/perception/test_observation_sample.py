"""A fast host must not split the CLI viewer's image and findings."""
from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from cli.automa_cli import host_publications as publications, streaming


class _Response(io.BytesIO):
    status = 200

    def __init__(self, payload, *, headers=None):
        super().__init__(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
        self.headers = headers or {}


def _publication(frame_id="first"):
    return {"frame": {"frame_id": frame_id, "has_image": True}}


def _transaction():
    return {
        "schema": publications.OBSERVATION_TRANSACTION_SCHEMA,
        "publication": _publication(),
        "image_frame_id": "first",
        "image_content_type": "image/jpeg",
        "image_base64": base64.b64encode(b"jpeg").decode(),
    }


class ObservationSampleTests(unittest.TestCase):
    def test_conditional_observation_read_reuses_image_without_downloading_it(self):
        cache = publications.TransactionCache()
        first = _Response(_transaction(), headers={"ETag": 'W/"first"'})
        unchanged = HTTPError("http://host.invalid", 304, "unchanged", {"X-Read-At-Ms": "2000"}, io.BytesIO())
        with patch.object(publications.urllib.request, "urlopen", side_effect=[first, unchanged]) as read:
            publication, jpeg = publications.fetch_observation_sample("http://host.invalid", cache=cache)
            publication, again = publications.fetch_observation_sample("http://host.invalid", cache=cache)
        self.assertIs(again, jpeg)
        self.assertEqual(publication["read_at_ms"], 2000)
        self.assertEqual(read.call_args.args[0].get_header("If-none-match"), 'W/"first"')

    def test_legacy_host_checks_metadata_without_redownloading_an_unchanged_image(self):
        cache = publications.TransactionCache()
        first = _transaction()
        first["publication"].update(frames_captured=1, frames_superseded=0)
        replies = [_Response(first), _Response({**_publication(), "frames_captured": 8, "frames_superseded": 6})]
        with patch.object(publications.urllib.request, "urlopen", side_effect=replies) as read:
            _, jpeg = publications.fetch_observation_sample("http://host.invalid", cache=cache)
            _, again = publications.fetch_observation_sample("http://host.invalid", cache=cache)
        self.assertIs(again, jpeg)
        self.assertEqual(read.call_args.args[0], "http://host.invalid/autonomy/observation/latest")

    def test_unavailable_transaction_invalidates_image_cache_and_can_recover(self):
        cache = publications.TransactionCache()
        unavailable = HTTPError("http://host.invalid", 503, "bad image", {}, io.BytesIO(json.dumps({
            "schema": publications.OBSERVATION_TRANSACTION_SCHEMA,
            "publication": {"health": "error", "error": "bad image"}, "image_base64": None,
        }).encode()))
        # An unavailable response has no ETag; the next poll probes metadata
        # before retrying its transaction, without falling back to split images.
        replies = [_Response(_transaction(), headers={"ETag": 'W/"first"'}), unavailable,
                   _Response(_publication()), _Response(_transaction(), headers={"ETag": 'W/"first"'})]
        with patch.object(publications.urllib.request, "urlopen", side_effect=replies):
            publications.fetch_observation_sample("http://host.invalid", cache=cache)
            publication, jpeg = publications.fetch_observation_sample("http://host.invalid", cache=cache)
            self.assertIsNone(jpeg)
            self.assertIsNone(cache.image)
            self.assertEqual(publication["health"], "error")
            _, jpeg = publications.fetch_observation_sample("http://host.invalid", cache=cache)
        self.assertEqual(jpeg, b"jpeg")

    def test_atomic_sample_needs_only_one_request(self):
        with patch.object(publications.urllib.request, "urlopen", return_value=_Response(_transaction())) as read:
            publication, jpeg = publications.fetch_observation_sample("http://host.invalid")
        self.assertEqual(publication, _publication())
        self.assertEqual(jpeg, b"jpeg")
        self.assertEqual(read.call_count, 1)
        self.assertEqual(read.call_args.args[0], "http://host.invalid/autonomy/observation/transaction")

    def test_warming_returns_no_image(self):
        payload = _transaction()
        payload.update(publication={"frame": None, "health": "warming"}, image_base64=None)
        with patch.object(publications.urllib.request, "urlopen", return_value=_Response(payload)):
            publication, jpeg = publications.fetch_observation_sample("http://host.invalid")
        self.assertEqual(publication["health"], "warming")
        self.assertIsNone(jpeg)

    def test_invalid_transactions_do_not_fall_back(self):
        changes = (
            {"schema": "wrong"}, {"publication": None}, {"image_frame_id": "second"},
            {"image_content_type": "image/png"}, {"image_base64": "!"},
            {"image_base64": ""}, {"image_base64": None},
        )
        for change in changes:
            with self.subTest(change=change):
                with patch.object(publications.urllib.request, "urlopen", return_value=_Response({**_transaction(), **change})) as read:
                    with self.assertRaises(ValueError):
                        publications.fetch_observation_sample("http://host.invalid")
                self.assertEqual(read.call_count, 1)

    def test_missing_route_uses_a_matched_legacy_pair(self):
        for body in (b"", b"<html>404</html>", b'{"error":"not found"}'):
            with self.subTest(body=body):
                missing = HTTPError("http://host.invalid", 404, "missing", {}, io.BytesIO(body))
                replies = [missing, _Response(_publication()), _Response(b"jpeg", headers={"X-Frame-Id": "first"})]
                with patch.object(publications.urllib.request, "urlopen", side_effect=replies) as read:
                    publication, jpeg = publications.fetch_observation_sample("http://host.invalid")
                self.assertEqual(publication, {**_publication(), "http_status": 200})
                self.assertEqual(jpeg, b"jpeg")
                self.assertEqual(read.call_count, 3)

    def test_legacy_frame_advance_skips_image_without_retrying(self):
        missing = HTTPError("http://host.invalid", 404, "missing", {}, io.BytesIO())
        replies = [missing, _Response(_publication()), _Response(b"newer jpeg", headers={"x-frame-id": "second"})]
        with patch.object(publications.urllib.request, "urlopen", side_effect=replies) as read:
            publication, jpeg = publications.fetch_observation_sample("http://host.invalid")
        self.assertEqual(publication["frame"]["frame_id"], "first")
        self.assertIsNone(jpeg)
        self.assertEqual(read.call_count, 3)

    def test_legacy_publication_without_identity_cannot_publish_an_image(self):
        missing = HTTPError("http://host.invalid", 404, "missing", {}, io.BytesIO())
        replies = [missing, _Response({"frame": {"has_image": True}})]
        with patch.object(publications.urllib.request, "urlopen", side_effect=replies) as read:
            _, jpeg = publications.fetch_observation_sample("http://host.invalid")
        self.assertIsNone(jpeg)
        self.assertEqual(read.call_count, 2)

    def test_transport_and_server_errors_do_not_fall_back(self):
        failures = (
            URLError("offline"), TimeoutError(),
            HTTPError("http://host.invalid", 503, "failed", {}, io.BytesIO(b'{"error":"bad image"}')),
        )
        for failure in failures:
            with self.subTest(failure=failure):
                with patch.object(publications.urllib.request, "urlopen", side_effect=failure) as read:
                    with self.assertRaises(ConnectionError):
                        publications.fetch_observation_sample("http://host.invalid")
                self.assertEqual(read.call_count, 1)

    def test_stream_view_uses_the_transaction_metadata_when_the_host_advances(self):
        with tempfile.TemporaryDirectory() as tmp:
            feed = streaming._ViewFeed.__new__(streaming._ViewFeed)
            feed._observation_cache = publications.TransactionCache()
            feed._observation = feed._last_capture = None
            feed.base_url, feed.timeout_s = "http://host.invalid", 1.0
            feed.frame_path = Path(tmp) / "frame.jpg"
            feed.server, feed.decision = Mock(), Mock()
            with patch.object(streaming, "fetch_observation_sample", return_value=(_publication("second"), b"jpeg")):
                feed.publish(_publication("first"))
            frame = feed.server.perception.publish_matched.call_args.kwargs["frame_record"]
            self.assertEqual(frame["frame_id"], "second")
            self.assertEqual(feed.frame_path.read_bytes(), b"jpeg")
            self.assertIsNone(feed.error)

    def test_stream_terminal_and_view_share_one_read_and_skip_unchanged_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            feed = streaming._ViewFeed.__new__(streaming._ViewFeed)
            feed._observation_cache = publications.TransactionCache()
            feed._observation = feed._last_capture = None
            feed.base_url, feed.timeout_s = "http://host.invalid", 1.0
            feed.frame_path = Path(tmp) / "frame.jpg"
            feed.server, feed.decision = Mock(), Mock()
            with patch.object(streaming, "fetch_observation_sample", return_value=(_publication(), b"jpeg")) as read:
                publication, error = feed.read_observation()
                self.assertIsNone(error)
                feed.publish(publication)
                feed.publish(publication)
            self.assertEqual(read.call_count, 1)
            self.assertEqual(feed.server.perception.publish_matched.call_count, 1)
