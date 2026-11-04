"""Frozen JSON storage."""

from __future__ import annotations

import pickle
import unittest

from autonomy.serialization import FrozenJsonObject, deep_freeze


class JsonStorageTests(unittest.TestCase):
    def test_nested_frozen_json_pickles_without_a_mapping_proxy(self) -> None:
        frozen = deep_freeze({"a": {"b": []}})
        self.assertIsInstance(frozen, FrozenJsonObject)
        restored = pickle.loads(pickle.dumps(frozen))
        self.assertIsInstance(restored, FrozenJsonObject)
        self.assertIsInstance(restored["a"], FrozenJsonObject)
        self.assertEqual(restored["a"]["b"], ())
        self.assertEqual(restored, frozen)
        with self.assertRaises(TypeError):
            restored["a"] = {}


if __name__ == "__main__":
    unittest.main()
