"""Shared-memory documents encode JSON data and records, and decode a fresh map."""

from __future__ import annotations

import json
import unittest

from autonomy.decision_cycle.memory.evidence import MemoryOrigin, RetainedEvidence
from autonomy.decision_cycle.perception.evidence.values import ViewLocation
from autonomy.shared_memory import (
    SHARED_MEMORY_SCHEMA,
    SharedMemoryValueError,
    shared_memory_document,
    shared_memory_from_document,
)
from implementations.decision_cycle.memory.shared.evidence_ledger.ledger import (
    EvidenceLedger,
    LedgerBounds,
    empty_ledger,
)


class NoFromDict:
    """A class a document can name that cannot rebuild itself."""


class RaisesFromDict:
    @classmethod
    def from_dict(cls, data: dict) -> RaisesFromDict:
        del data
        raise ValueError("boom")


def _retained() -> RetainedEvidence:
    return RetainedEvidence(
        record_id="rec_1",
        kind="floor_boundary",
        label="first-hit boundary",
        confidence=0.8,
        origin=MemoryOrigin(
            observation_id="obs_1",
            observed_id="floor_boundary_000",
            coordinate_frame="image",
            observed_at_ms=100,
            updated_at_ms=150,
            source_plugin_id="floor_plane",
            frame_id="frame_000100",
        ),
        location=ViewLocation(
            frame="image",
            zone="center",
            bbox_xyxy_norm=(0.4, 0.5, 0.6, 0.9),
        ),
        properties={"width_fraction": 0.2},
    )


def _ledger() -> EvidenceLedger:
    return empty_ledger(
        memory_id="memory-1",
        epoch_id="epoch-1",
        bounds=LedgerBounds(max_records=4),
        created_at_ms=10,
        plugin_id="bounded_evidence",
    )


def _document(values: dict) -> dict:
    return {"schema": SHARED_MEMORY_SCHEMA, "values": values}


class SharedMemoryDocumentTests(unittest.TestCase):
    def test_json_passes_through(self) -> None:
        original = {
            "flag": True,
            "count": 2,
            "name": "a",
            "empty": None,
            "nested": {"k": [1, 2.5]},
        }

        restored = shared_memory_from_document(
            json.loads(json.dumps(shared_memory_document(original)))
        )

        self.assertEqual(restored, original)

    def test_tuples_come_back_as_lists(self) -> None:
        restored = shared_memory_from_document(shared_memory_document({"box": (1, 2, 3)}))

        self.assertEqual(restored["box"], [1, 2, 3])
        self.assertIsInstance(restored["box"], list)

    def test_records_round_trip_nested_in_a_list(self) -> None:
        retained = _retained()
        ledger = _ledger()

        restored = shared_memory_from_document(
            json.loads(json.dumps(shared_memory_document({"items": [retained, ledger]})))
        )

        self.assertEqual(restored["items"][0], retained)
        self.assertEqual(restored["items"][1], ledger)
        self.assertIsInstance(restored["items"][1], EvidenceLedger)

    def test_a_plain_dict_stays_a_dict(self) -> None:
        payload = _ledger().to_dict()

        restored = shared_memory_from_document(_document({"ledger": payload}))

        self.assertIsInstance(restored["ledger"], dict)
        self.assertNotIsInstance(restored["ledger"], EvidenceLedger)

    def test_each_decode_returns_a_fresh_map(self) -> None:
        document = json.loads(
            json.dumps(shared_memory_document({"items": [_retained()], "nested": {"a": [1]}}))
        )

        first = shared_memory_from_document(document)
        second = shared_memory_from_document(document)
        first["nested"]["a"].append(2)
        first["items"][0].properties["width_fraction"] = 0.1

        self.assertIsNot(first, second)
        self.assertIsNot(first["items"][0], second["items"][0])
        self.assertEqual(second["nested"]["a"], [1])
        self.assertEqual(second["items"][0].properties["width_fraction"], 0.2)

    def test_a_value_outside_the_rule_names_its_key(self) -> None:
        with self.assertRaises(SharedMemoryValueError) as caught:
            shared_memory_document({"bad": {1, 2}})

        self.assertIn("bad", str(caught.exception))

    def test_a_malformed_record_names_its_key(self) -> None:
        with self.assertRaises(SharedMemoryValueError) as caught:
            shared_memory_from_document(_document({"slot": {"$record": "x"}}))

        self.assertIn("slot", str(caught.exception))

    def test_an_unknown_module_names_its_key(self) -> None:
        with self.assertRaises(SharedMemoryValueError) as caught:
            shared_memory_from_document(
                _document({"slot": {"$record": "not.a.real.module:Thing", "data": {}}})
            )

        self.assertIn("slot", str(caught.exception))
        self.assertIn("not.a.real.module", str(caught.exception))

    def test_an_unknown_class_names_its_key(self) -> None:
        with self.assertRaises(SharedMemoryValueError) as caught:
            shared_memory_from_document(
                _document({"slot": {"$record": "autonomy.shared_memory:Missing", "data": {}}})
            )

        self.assertIn("slot", str(caught.exception))

    def test_a_class_without_from_dict_names_its_key(self) -> None:
        spec = f"{__name__}:NoFromDict"

        with self.assertRaises(SharedMemoryValueError) as caught:
            shared_memory_from_document(_document({"slot": {"$record": spec, "data": {}}}))

        self.assertIn("slot", str(caught.exception))
        self.assertIn("from_dict", str(caught.exception))

    def test_from_dict_raising_names_its_key(self) -> None:
        spec = f"{__name__}:RaisesFromDict"

        with self.assertRaises(SharedMemoryValueError) as caught:
            shared_memory_from_document(_document({"slot": {"$record": spec, "data": {}}}))

        self.assertIn("slot", str(caught.exception))
        self.assertIn("boom", str(caught.exception))

    def test_a_wrong_schema_names_schema(self) -> None:
        with self.assertRaises(SharedMemoryValueError) as caught:
            shared_memory_from_document({"schema": "other", "values": {}})

        self.assertIn("schema", str(caught.exception))

    def test_values_that_are_not_an_object_name_values(self) -> None:
        with self.assertRaises(SharedMemoryValueError) as caught:
            shared_memory_from_document({"schema": SHARED_MEMORY_SCHEMA, "values": []})

        self.assertIn("values", str(caught.exception))
