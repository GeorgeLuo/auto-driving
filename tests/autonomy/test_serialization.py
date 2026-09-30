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

    def test_strict_rejection_messages_stay_wrapped_once(self) -> None:
        strict_prefix = "value is not strictly JSON-serializable: "

        with self.assertRaises(ValueError) as nan_error:
            canonical_json_utf8(float("nan"))
        nan_cause = nan_error.exception.__cause__
        self.assertIsInstance(nan_cause, ValueError)
        self.assertEqual(
            str(nan_error.exception),
            f"{strict_prefix}{type(nan_cause).__name__}: {nan_cause}",
        )
        self.assertNotIn(strict_prefix, str(nan_cause))

        with self.assertRaises(ValueError) as set_error:
            canonical_json_size_bytes({1})
        set_cause = set_error.exception.__cause__
        self.assertIsInstance(set_cause, TypeError)
        self.assertEqual(
            str(set_error.exception),
            f"{strict_prefix}{type(set_cause).__name__}: {set_cause}",
        )
        self.assertNotIn(strict_prefix, str(set_cause))

        with self.assertRaises(ValueError) as freeze_error:
            deep_freeze(float("nan"), field_name="metadata")
        freeze_cause = freeze_error.exception.__cause__
        self.assertIsInstance(freeze_cause, ValueError)
        self.assertEqual(
            str(freeze_error.exception),
            f"metadata is not strict JSON: {freeze_cause}",
        )
        json_cause = freeze_cause.__cause__
        self.assertIsInstance(json_cause, ValueError)
        self.assertEqual(
            str(freeze_cause),
            f"{strict_prefix}{type(json_cause).__name__}: {json_cause}",
        )
        self.assertNotIn(strict_prefix, str(json_cause))

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

    def test_freeze_rejects_non_string_keys_before_encoding(self) -> None:
        with self.assertRaises(ValueError) as key_error:
            deep_freeze({"ok": {2: "nested"}})
        self.assertEqual(
            str(key_error.exception),
            "value.ok JSON object keys must be strings; got int",
        )
        self.assertIsNone(key_error.exception.__cause__)
