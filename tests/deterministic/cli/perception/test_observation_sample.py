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

from cli.automa_cli.hosts import host_publications as publications, streaming


class _Response(io.BytesIO):
    status = 200

    def __init__(self, payload, *, headers=None):
        super().__init__(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
        self.headers = {"ETag": 'W/"test"'} if headers is None else headers


def _publication(frame_id="first"):
    return {"health": "healthy", "frame": {"frame_id": frame_id, "has_image": True}}


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


    def test_unavailable_transaction_invalidates_image_cache_and_can_recover(self):
        cache = publications.TransactionCache()
        unavailable = HTTPError("http://host.invalid", 503, "bad image", {}, io.BytesIO(json.dumps({
            "schema": publications.OBSERVATION_TRANSACTION_SCHEMA,
            "publication": {"health": "error", "error": "bad image"}, "image_base64": None,
        }).encode()))
        # Availability failures invalidate the whole cached pair.
        replies = [_Response(_transaction(), headers={"ETag": 'W/"first"'}), unavailable,
                   _Response(_transaction(), headers={"ETag": 'W/"first"'})]
        with patch.object(publications.urllib.request, "urlopen", side_effect=replies):
            publications.fetch_observation_sample("http://host.invalid", cache=cache)
            publication, jpeg = publications.fetch_observation_sample("http://host.invalid", cache=cache)
            self.assertIsNone(jpeg)
            self.assertIsNone(cache.image)
            self.assertEqual(publication["health"], "error")
            _, jpeg = publications.fetch_observation_sample("http://host.invalid", cache=cache)
        self.assertEqual(jpeg, b"jpeg")

    def test_both_transactions_invalidate_success_before_rejecting_a_later_304(self):
        for fetch in (publications.fetch_observation_sample, publications.fetch_decision_transaction):
            cache = publications.TransactionCache()
            unavailable = HTTPError("http://host.invalid", 503, "unavailable", {}, io.BytesIO(json.dumps({
                "publication": {"health": "warming"}, "image_base64": None,
            }).encode()))
            unchanged = HTTPError("http://host.invalid", 304, "unchanged", {}, io.BytesIO())
            with self.subTest(fetch=fetch.__name__), patch.object(publications.urllib.request, "urlopen",
                    side_effect=[_Response(_transaction()), unavailable, unchanged]) as read:
                fetch("http://host.invalid", cache=cache)
                cache.image = b"jpeg", "image/jpeg"
                fetch("http://host.invalid", cache=cache)
                self.assertEqual((cache.etag, cache.payload, cache.image), (None, None, None))
                with self.assertRaisesRegex(ConnectionError, "without a cached transaction"):
                    fetch("http://host.invalid", cache=cache)
                self.assertIsInstance(read.call_args.args[0], str)
                self.assertEqual(read.call_count, 3)

    def test_a_body_read_timeout_clears_the_cache(self):
        class _Stalled(_Response):
            def read(self, *_args):
                raise TimeoutError()

        for fetch in (publications.fetch_observation_sample, publications.fetch_decision_transaction):
            cache = publications.TransactionCache("old", {"publication": {}}, (b"old", "image/jpeg"))
            with self.subTest(fetch=fetch.__name__), patch.object(
                    publications.urllib.request, "urlopen", return_value=_Stalled(_transaction())):
                with self.assertRaisesRegex(ConnectionError, "timed out"):
                    fetch("http://host.invalid", cache=cache)
                self.assertEqual((cache.etag, cache.payload, cache.image), (None, None, None))

    def test_both_transactions_require_an_updated_host(self):
        for fetch in (publications.fetch_observation_sample, publications.fetch_decision_transaction):
            for response in (_Response(_transaction(), headers={}),
                             HTTPError("http://host.invalid", 404, "missing", {}, io.BytesIO())):
                cache = publications.TransactionCache("old", {"publication": {}}, (b"old", "image/jpeg"))
                with self.subTest(fetch=fetch.__name__, response=response), patch.object(
                        publications.urllib.request, "urlopen", side_effect=[response]) as read:
                    with self.assertRaisesRegex(ConnectionError, "Incompatible host.*update"):
                        fetch("http://host.invalid", cache=cache)
                    self.assertEqual((cache.etag, cache.payload, cache.image), (None, None, None))
                    self.assertEqual(read.call_count, 1)

    def test_atomic_sample_needs_only_one_request(self):
        with patch.object(publications.urllib.request, "urlopen", return_value=_Response(_transaction())) as read:
            publication, jpeg = publications.fetch_observation_sample("http://host.invalid")
        self.assertEqual(publication, _publication())
        self.assertEqual(jpeg, b"jpeg")
        self.assertEqual(read.call_count, 1)
        self.assertEqual(read.call_args.args[0], "http://host.invalid/host/observation/transaction")

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
