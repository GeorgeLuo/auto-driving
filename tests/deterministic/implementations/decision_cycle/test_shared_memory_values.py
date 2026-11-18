"""Shared perception values that a plugin keeps in shared memory read back from a document."""

from __future__ import annotations

import unittest
import json
from autonomy.fields import FieldError
from autonomy.shared_memory import shared_memory_document, shared_memory_from_document

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

    def test_stored_images_load_from_seeds_with_exact_shape_dtype_and_bytes(self) -> None:
        for pixels in (np.arange(6, dtype=np.uint8).reshape(2, 3), np.zeros((0, 3), dtype=np.float32), np.array(7, dtype=np.int16)):
            with self.subTest(shape=pixels.shape):
                image = StoredImage(pixels)
                seed = json.loads(json.dumps(shared_memory_document({"perception.motion_tracks.previous": image})))
                restored = shared_memory_from_document(seed)["perception.motion_tracks.previous"]
                self.assertEqual(restored, image)
                self.assertTrue(restored.pixels.flags.writeable)
                self.assertFalse(np.shares_memory(restored.pixels, pixels))

    def test_stored_image_rejects_coerced_shape_and_noncanonical_encoding(self) -> None:
        original = StoredImage(np.arange(6, dtype=np.uint8).reshape(2, 3)).to_dict()
        for field, value, error_field in (
            ("shape", [True, 6], "shape.0"), ("shape", [2, "3"], "shape.1"),
            ("shape", [2, 3.0], "shape.1"), ("shape", [2, -3], "shape.1"),
            ("dtype", "uint8", "dtype"), ("dtype", "|O", "dtype"),
            ("base64", "AA==", "base64"), ("base64", "!!!!!!", "base64"),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(FieldError) as invalid:
                StoredImage.from_dict({**original, field: value})
            self.assertEqual(invalid.exception.field_name, error_field)

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
