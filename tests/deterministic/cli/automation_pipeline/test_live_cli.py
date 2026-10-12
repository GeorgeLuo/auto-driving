"""The public live commands against a running runtime host.

Every vehicle's host serves the same routes and the CLI finds it only by
``base_url``, so streams, viability and memory reset run here against an
in-process host; a PiCar differs only in where its ``base_url`` comes from.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch
from urllib.request import urlopen

from cli.automa_cli.streaming import _ViewFeed, _read_publication
from tests.shared.cli.automation_pipeline.pipeline_fixtures import VEHICLE_ID
from tests.shared.integration.live_host import LiveHostTestCase


class LiveCommandsTests(LiveHostTestCase):


    def test_the_perception_view_stays_live_while_the_decision_is_unavailable(self) -> None:
        self.observe()
        with patch("cli.automa_cli.streaming.runtime_view_dir", return_value=self.runtime_root / "view"):
            feed = _ViewFeed(VEHICLE_ID, base_url=self.host.base_url, timeout_s=3.0)
        self.addCleanup(feed.stop)
        feed.decision.refresh = Mock(side_effect=ConnectionError("decision route down"))
        publication = _read_publication(self.host.base_url, timeout_s=3.0)[0]
        feed.publish(publication)
        self.assertIn("ConnectionError: decision route down", feed.decision_line())
        with urlopen(f"{feed.url.rstrip('/')}/api/latest", timeout=5) as response:
            self.assertEqual(json.load(response)["frame"]["frame_id"], publication["frame"]["frame_id"])


if __name__ == "__main__":
    unittest.main()
