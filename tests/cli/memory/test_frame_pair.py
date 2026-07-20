from __future__ import annotations

import unittest
from unittest import mock

from cli.automa_cli.physical_observation import (
    fetch_matched_observation_pair,
    frame_id_from_headers,
    frame_id_from_publication,
)


class FramePairHelpersTests(unittest.TestCase):
    def test_frame_id_helpers(self) -> None:
        self.assertEqual(
            frame_id_from_publication({"frame": {"frame_id": "abc"}}),
            "abc",
        )
        self.assertIsNone(frame_id_from_publication({"frame": {}}))
        self.assertEqual(frame_id_from_headers({"x-frame-id": "xyz"}), "xyz")
        self.assertIsNone(frame_id_from_headers({}))

    def test_matched_pair_accepts_matching_ids(self) -> None:
        publication = {
            "health": "healthy",
            "frame": {"frame_id": "frame_7", "has_image": True},
        }
        with mock.patch(
            "cli.automa_cli.physical_observation.fetch_observation_publication",
            return_value=publication,
        ), mock.patch(
            "cli.automa_cli.physical_observation.fetch_observation_frame",
            return_value=(b"jpeg", {"x-frame-id": "frame_7"}),
        ):
            pair = fetch_matched_observation_pair(
                "http://piracer.test:8887",
                timeout_s=0.2,
                match_timeout_s=0.5,
            )
        self.assertTrue(pair["matched"])
        self.assertEqual(pair["frame_id"], "frame_7")
        self.assertEqual(pair["frame_bytes"], b"jpeg")
        self.assertEqual(pair["attempts"], 1)

    def test_matched_pair_retries_until_match(self) -> None:
        pubs = [
            {"frame": {"frame_id": "old", "has_image": True}},
            {"frame": {"frame_id": "new", "has_image": True}},
        ]
        frames = [
            (b"a", {"x-frame-id": "stale"}),
            (b"b", {"x-frame-id": "new"}),
        ]
        with mock.patch(
            "cli.automa_cli.physical_observation.fetch_observation_publication",
            side_effect=pubs,
        ), mock.patch(
            "cli.automa_cli.physical_observation.fetch_observation_frame",
            side_effect=frames,
        ), mock.patch(
            "cli.automa_cli.physical_observation.time.sleep",
            return_value=None,
        ):
            pair = fetch_matched_observation_pair(
                "http://piracer.test:8887",
                timeout_s=0.2,
                match_timeout_s=2.0,
            )
        self.assertEqual(pair["frame_id"], "new")
        self.assertEqual(pair["frame_bytes"], b"b")
        self.assertGreaterEqual(pair["attempts"], 2)

    def test_matched_pair_times_out_on_persistent_mismatch(self) -> None:
        with mock.patch(
            "cli.automa_cli.physical_observation.fetch_observation_publication",
            return_value={"frame": {"frame_id": "a", "has_image": True}},
        ), mock.patch(
            "cli.automa_cli.physical_observation.fetch_observation_frame",
            return_value=(b"jpeg", {"x-frame-id": "b"}),
        ), mock.patch(
            "cli.automa_cli.physical_observation.time.sleep",
            return_value=None,
        ), mock.patch(
            "cli.automa_cli.physical_observation.time.monotonic",
            side_effect=[0.0, 0.1, 0.2, 5.0],
        ):
            with self.assertRaises(TimeoutError) as ctx:
                fetch_matched_observation_pair(
                    "http://piracer.test:8887",
                    timeout_s=0.1,
                    match_timeout_s=1.0,
                )
        self.assertIn("mismatch", str(ctx.exception))

    def test_require_image_does_not_succeed_when_has_image_false(self) -> None:
        jpeg_calls = {"n": 0}

        def no_jpeg(*_args, **_kwargs):
            jpeg_calls["n"] += 1
            raise AssertionError("JPEG must not be fetched when has_image is false")

        with mock.patch(
            "cli.automa_cli.physical_observation.fetch_observation_publication",
            return_value={"frame": {"frame_id": "f1", "has_image": False}},
        ), mock.patch(
            "cli.automa_cli.physical_observation.fetch_observation_frame",
            side_effect=no_jpeg,
        ), mock.patch(
            "cli.automa_cli.physical_observation.time.sleep",
            return_value=None,
        ), mock.patch(
            "cli.automa_cli.physical_observation.time.monotonic",
            side_effect=[0.0, 0.1, 0.2, 5.0],
        ):
            with self.assertRaises(TimeoutError) as ctx:
                fetch_matched_observation_pair(
                    "http://piracer.test:8887",
                    timeout_s=0.1,
                    match_timeout_s=1.0,
                    require_image=True,
                )
        self.assertIn("has_image=false", str(ctx.exception))
        self.assertEqual(jpeg_calls["n"], 0)

    def test_after_frame_id_waits_for_newer_matched_pair(self) -> None:
        # First two publications stay on "old" (no JPEG fetch). Third is "new".
        pubs = [
            {"frame": {"frame_id": "old", "has_image": True}},
            {"frame": {"frame_id": "old", "has_image": True}},
            {"frame": {"frame_id": "new", "has_image": True}},
        ]
        with mock.patch(
            "cli.automa_cli.physical_observation.fetch_observation_publication",
            side_effect=pubs,
        ), mock.patch(
            "cli.automa_cli.physical_observation.fetch_observation_frame",
            return_value=(b"c", {"x-frame-id": "new"}),
        ), mock.patch(
            "cli.automa_cli.physical_observation.time.sleep",
            return_value=None,
        ):
            pair = fetch_matched_observation_pair(
                "http://piracer.test:8887",
                timeout_s=0.2,
                match_timeout_s=2.0,
                require_image=True,
                after_frame_id="old",
            )
        self.assertEqual(pair["frame_id"], "new")
        self.assertEqual(pair["frame_bytes"], b"c")
        self.assertGreaterEqual(pair["attempts"], 3)

    def test_require_image_rejects_empty_jpeg_body(self) -> None:
        with mock.patch(
            "cli.automa_cli.physical_observation.fetch_observation_publication",
            return_value={"frame": {"frame_id": "f1", "has_image": True}},
        ), mock.patch(
            "cli.automa_cli.physical_observation.fetch_observation_frame",
            return_value=(b"", {"x-frame-id": "f1"}),
        ), mock.patch(
            "cli.automa_cli.physical_observation.time.sleep",
            return_value=None,
        ), mock.patch(
            "cli.automa_cli.physical_observation.time.monotonic",
            side_effect=[0.0, 0.1, 0.2, 5.0],
        ):
            with self.assertRaises(TimeoutError) as ctx:
                fetch_matched_observation_pair(
                    "http://piracer.test:8887",
                    timeout_s=0.1,
                    match_timeout_s=1.0,
                    require_image=True,
                )
        self.assertIn("empty", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
