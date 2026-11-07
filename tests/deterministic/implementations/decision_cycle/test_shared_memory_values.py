"""Shared perception values that a plugin keeps in shared memory read back from a document."""

from __future__ import annotations

import unittest

import numpy as np

from implementations.decision_cycle.perception.shared.image.stored import StoredImage
from implementations.decision_cycle.perception.shared.serialization.history import (
    history_float,
    history_int,
    next_history_id,
)


class SharedMemoryValueTests(unittest.TestCase):
    def test_stored_image_decodes_to_a_writable_copy(self) -> None:
        image = StoredImage(np.arange(6, dtype=np.uint8).reshape(2, 3))

        restored = StoredImage.from_dict(image.to_dict())
        restored.pixels[0, 0] = 9

        self.assertTrue(restored.pixels.flags.writeable)
        self.assertEqual(int(image.pixels[0, 0]), 0)

    def test_a_history_counter_takes_only_a_json_integer(self) -> None:
        """Regression: plugins read "9", True or 9.5 as a next id."""

        for recorded, valid in ((9, True), ("9", False), (True, False), (9.5, False)):
            with self.subTest(next_id=recorded):
                self.assertEqual(history_int(recorded), recorded if valid else None)
                self.assertEqual(next_history_id(recorded, ()), 9 if valid else 1)
                self.assertEqual(next_history_id(recorded, (), first=0), 9 if valid else 0)
        self.assertEqual(next_history_id(2, (4, 7)), 8)

    def test_a_history_number_takes_only_a_finite_json_number(self) -> None:
        cases = ((2, 2.0), (0.5, 0.5), ("0.5", None), (True, None), (float("inf"), None), (10**400, None))
        for recorded, expected in cases:
            with self.subTest(recorded=recorded):
                self.assertEqual(history_float(recorded), expected)


if __name__ == "__main__":
    unittest.main()
