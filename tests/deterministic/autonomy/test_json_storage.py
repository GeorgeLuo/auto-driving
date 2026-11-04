"""Frozen JSON storage and the shared exact-key check."""

from __future__ import annotations

import pickle
import unittest

from autonomy.serialization import FrozenJsonObject, deep_freeze, require_exact_keys


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

    def test_exact_keys_name_the_missing_and_extra_fields(self) -> None:
        with self.assertRaises(ValueError) as raised:
            require_exact_keys(
                {"schema": "record", "obsolete": 1},
                frozenset({"schema", "frame_id"}),
                field="value",
            )
        message = str(raised.exception)
        self.assertIn("missing=['frame_id']", message)
        self.assertIn("extra=['obsolete']", message)


if __name__ == "__main__":
    unittest.main()
