"""Shared strict-JSON encoding and frozen storage."""

from __future__ import annotations

import unittest
from copy import deepcopy

from autonomy.serialization import (
    FrozenJsonObject,
    canonical_json_size_bytes,
    canonical_json_utf8,
    deep_freeze,
    ensure_strict_json_value,
    frozen_mapping_to_dict,
)


class SerializationTests(unittest.TestCase):
    def test_canonical_encoding_and_round_trip(self) -> None:
        value = {"b": 1, "a": [True, None, 2]}
        encoded = b'{"a":[true,null,2],"b":1}'
        self.assertEqual(canonical_json_utf8(value), encoded)
        self.assertEqual(canonical_json_size_bytes(value), len(encoded))
        self.assertEqual(ensure_strict_json_value(value), {"a": [True, None, 2], "b": 1})
        self.assertEqual(
            ensure_strict_json_value((1, {"b": 1, "a": 2})),
            [1, {"a": 2, "b": 1}],
        )
        self.assertIs(ensure_strict_json_value(True), True)
        self.assertEqual(ensure_strict_json_value({1: "a"}), {"1": "a"})


    def test_freeze_preserves_object_and_array_identity(self) -> None:
        frozen = deep_freeze({"a": [], "b": {}})
        self.assertIsInstance(frozen, FrozenJsonObject)
        self.assertEqual(frozen["a"], ())
        self.assertIsInstance(frozen["b"], FrozenJsonObject)
        self.assertEqual(deep_freeze([]), ())
        self.assertIsInstance(deep_freeze({}), FrozenJsonObject)
        self.assertEqual(frozen_mapping_to_dict(frozen), {"a": [], "b": {}})

        cloned = deepcopy(frozen)
        self.assertIsNot(cloned, frozen)
        self.assertIsNot(cloned["b"], frozen["b"])
        self.assertEqual(cloned, frozen)
        with self.assertRaises(TypeError):
            frozen._data["a"] = (1,)  # type: ignore[index]
        with self.assertRaises(AttributeError):
            frozen.extra = 1  # type: ignore[attr-defined]

