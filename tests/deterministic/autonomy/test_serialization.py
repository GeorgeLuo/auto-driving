"""Shared strict-JSON encoding and frozen storage."""

from __future__ import annotations

import unittest
from collections import UserDict
from copy import deepcopy
from enum import IntEnum
from unittest.mock import patch

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

    def test_freeze_matches_json_normalization_without_round_trip(self) -> None:
        class Number(IntEnum):
            ONE = 1

        class Text(str):
            pass

        class Decimal(float):
            pass

        source = {"z": (Number.ONE, Text("hi"), Decimal(1.5)), "a": {"b": []}}
        expected = ensure_strict_json_value(source)
        with patch("autonomy.serialization.ensure_strict_json_value", side_effect=AssertionError):
            frozen = deep_freeze(source)
        self.assertEqual(list(frozen), ["a", "z"])
        self.assertEqual(frozen_mapping_to_dict(frozen), expected)
        self.assertEqual([type(item) for item in frozen["z"]], [int, str, float])
        source["a"]["b"].append("changed")
        exported = frozen_mapping_to_dict(frozen)
        exported["a"]["b"].append("changed")
        self.assertEqual(frozen_mapping_to_dict(frozen), expected)
        self.assertEqual(canonical_json_utf8(frozen_mapping_to_dict(frozen)), canonical_json_utf8(expected))

    def test_freeze_rejects_non_json_inputs_with_field_context(self) -> None:
        for value in (float("nan"), float("inf"), {1, 2}, UserDict({"x": 1}), object()):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, r"metadata\.x\[0\]"):
                deep_freeze({"x": [value]}, field_name="metadata")
        with self.assertRaisesRegex(ValueError, r"metadata\.x\[0\].*keys must be strings"):
            deep_freeze({"x": [{1: "bad"}]}, field_name="metadata")

    def test_freeze_normalizes_surrogate_pairs_like_json_decoding(self) -> None:
        value = {"\ud83d\ude00": "\ud83d\ude00", "😀": "\ud800"}
        expected = ensure_strict_json_value(value)
        frozen = deep_freeze(value)
        self.assertEqual(frozen_mapping_to_dict(frozen), expected)
        self.assertEqual(list(frozen), list(expected))

