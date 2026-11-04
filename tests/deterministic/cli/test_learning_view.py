from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from autonomy.decision_cycle.memory.beliefs import Belief, Trial
from cli.automa_cli.hosts.views.runtime_view import runtime_view_server
from cli.automa_cli.hosts.views.view_feed import ViewFeed


def _host(t: int, belief: Belief) -> dict:
    return {"last_cycle": {"context": {"timestamp_ms": t}}, "steps": {"memory": {
        "reset_count": 0, "beliefs": {belief.belief_id: belief.to_dict()}, "plugins": [],
    }}}


class LearningViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = runtime_view_server(host_name="learner", automation_dir=None, port=0).start()
        self.addCleanup(self.server.stop)

    def test_history_api_keeps_checks_and_accepts_a_cursor_and_head(self) -> None:
        belief = Belief("learner.value", 2, "learner", 100).opened(Trial("first", "stop", 100, 2), 4)
        self.server.learning.ingest(_host(100, belief))
        first = self.read()
        self.assertEqual(first["host_name"], "learner")
        self.server.learning.ingest(_host(200, belief.resolved("first", "support", 2, 200, 4)))
        later = self.read(f"?since={first['seq']}")
        track = later["beliefs"][belief.belief_id]
        self.assertEqual([check["outcome"] for check in track["checks"]], ["support"])
        self.assertEqual(track["trials"][0]["end"], "resolved")
        self.assertNotIn("/value", track["series"])
        with urlopen(Request(f"{self.server.url}api/learning", method="HEAD"), timeout=1) as response:
            self.assertEqual(response.status, 200)
            self.assertGreater(int(response.headers["Content-Length"]), 0)
            self.assertEqual(response.read(), b"")

    def test_bad_cursors_are_rejected(self) -> None:
        for query in ("?since=-1", "?since=bad", "?since=", "?since=1&since=2"):
            with self.subTest(query=query), self.assertRaises(HTTPError) as caught:
                self.read(query)
            self.assertEqual(caught.exception.code, 400)

    def test_source_outage_keeps_history_for_reconnection(self) -> None:
        belief = Belief("learner.value", 2, "learner", 100)
        source = mock.Mock(return_value=_host(100, belief))
        self.server.learning_source = source
        first = self.read()
        source.side_effect = ConnectionError("host is unavailable")
        with self.assertRaises(HTTPError) as caught:
            self.read()
        self.assertEqual(caught.exception.code, 503)
        self.assertEqual(json.loads(caught.exception.read())["error"], "host is unavailable")
        source.side_effect = None
        again = self.read(f"?since={first['seq']}")
        self.assertEqual(again["history_id"], first["history_id"])
        self.assertEqual(again["beliefs"][belief.belief_id]["series"], {})

    def test_host_view_reads_learning_without_polling_the_other_pages(self) -> None:
        belief = Belief("learner.value", 2, "learner", 100)
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("cli.automa_cli.hosts.views.view_feed.runtime_view_dir", return_value=Path(tmp)),
            mock.patch("cli.automa_cli.hosts.views.view_feed.runtime_view_server",
                       side_effect=lambda **kw: runtime_view_server(port=0, **kw)),
            mock.patch("cli.automa_cli.hosts.views.view_feed.fetch_autonomy_status",
                       return_value={"host": _host(100, belief)}) as source,
        ):
            feed = ViewFeed("learner", base_url="http://host.invalid", timeout_s=1)
            try:
                source.assert_not_called()
                with urlopen(f"{feed.url}api/learning", timeout=1) as response:
                    payload = json.loads(response.read())
                self.assertEqual(payload["beliefs"][belief.belief_id]["current"]["value"], 2)
                source.assert_called_once_with("http://host.invalid", timeout_s=1)
                source.return_value = {"http_status": 503, "error": "host status failed"}
                with self.assertRaises(HTTPError) as caught:
                    urlopen(f"{feed.url}api/learning", timeout=1)
                self.assertEqual(caught.exception.code, 503)
                self.assertEqual(json.loads(caught.exception.read())["error"], "host status failed")
            finally:
                feed.stop()

    def read(self, query: str = "") -> dict:
        with urlopen(f"{self.server.url}api/learning{query}", timeout=1) as response:
            return json.loads(response.read())
