"""Field rules name the field that broke them, including inside a nested document."""

from __future__ import annotations

import unittest

from autonomy.fields import (
    FieldError,
    require_choice,
    require_keys,
    require_nested,
    require_number,
    require_schema,
)


class FieldRuleTests(unittest.TestCase):
    def test_key_set_errors_name_the_missing_or_extra_key(self) -> None:
        cases = (
            ({"b": 1}, "a", "a is missing"),
            ({"a": 1, "b": 2, "c": 3}, "c", "c is not one of ['a', 'b']"),
            ([], "$", "$ must be an object"),
        )
        for payload, field_name, message in cases:
            with self.subTest(payload=payload), self.assertRaises(FieldError) as raised:
                require_keys(payload, ("a", "b"))
            self.assertEqual((raised.exception.field_name, str(raised.exception)), (field_name, message))

    def test_a_nested_error_is_placed_under_its_field(self) -> None:
        def parse(value: object) -> object:
            return require_keys(value, ("steering",))

        for value, field_name in (({}, "application.steering"), (None, "application")):
            with self.subTest(value=value), self.assertRaises(FieldError) as raised:
                require_nested(parse, value, field_name="application")
            self.assertEqual(raised.exception.field_name, field_name)

    def test_another_schema_is_named_before_the_key_set(self) -> None:
        with self.assertRaises(FieldError) as raised:
            require_schema({"schema": "previous", "obsolete": 1}, "current", ("schema", "value"))
        self.assertEqual(raised.exception.field_name, "schema")

    def test_numbers_are_finite_never_bool_and_inside_bounds(self) -> None:
        self.assertEqual(require_number(1, field_name="steering", bounds=(-1.0, 1.0)), 1.0)
        for value in (True, "0.5", float("nan"), float("inf"), 10**400, 1.5):
            with self.subTest(value=value), self.assertRaisesRegex(FieldError, r"^steering must be a finite number in \[-1, 1\]$"):
                require_number(value, field_name="steering", bounds=(-1.0, 1.0))

    def test_a_choice_accepts_only_a_listed_string(self) -> None:
        for value in ({}, [], " manual", None):
            with self.subTest(value=value), self.assertRaises(FieldError):
                require_choice(value, frozenset({"manual"}), field_name="mode")


if __name__ == "__main__":
    unittest.main()
