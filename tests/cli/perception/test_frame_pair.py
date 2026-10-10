from __future__ import annotations

import unittest

from cli.automa_cli.picar_observation import frame_id_from_headers


class FramePairHelpersTests(unittest.TestCase):
    def test_frame_id_from_headers(self) -> None:
        self.assertEqual(frame_id_from_headers({"x-frame-id": "xyz"}), "xyz")
        self.assertIsNone(frame_id_from_headers({}))


if __name__ == "__main__":
    unittest.main()
