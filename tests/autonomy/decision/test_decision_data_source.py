"""DecisionDataSource contract tests (M006-01)."""

from __future__ import annotations

import unittest
from copy import deepcopy

from autonomy.decision.decision_data import (
    DecisionDataSource,
    build_decision_data_source,
    memory_envelope_from_snapshot,
    ready_envelope,
    unavailable_envelope,
)
from autonomy.decision.memory import (
    MemoryBounds,
    MemoryProvenance,
    MemorySnapshot,
    RetainedEvidence,
    empty_memory_snapshot,
    error_memory_snapshot,
    unavailable_memory_snapshot,
)
from autonomy.perception import ViewLocation


def _bounds() -> MemoryBounds:
    return MemoryBounds(max_records=8, max_age_ms=10_000)


def _record(*, frame_id: str = "frame_001", kind: str = "floor_boundary") -> RetainedEvidence:
    return RetainedEvidence(
        record_id=f"thing:1:{kind}",
        kind=kind,
        label=kind,
        confidence=0.9,
        provenance=MemoryProvenance(
            observation_id="obs",
            evidence_id="ev",
            coordinate_frame="image",
            observed_at_ms=1000,
            updated_at_ms=1000,
            source_plugin_id="plugin",
            frame_id=frame_id,
        ),
        location=ViewLocation(
            frame="image", zone="left", bbox_xyxy_norm=(0.0, 0.0, 0.2, 0.5)
        ),
        properties={},
    )


class DecisionDataSourceTests(unittest.TestCase):
    def test_memory_health_mapping(self) -> None:
        empty = empty_memory_snapshot(
            memory_id="m",
            epoch_id="e",
            created_at_ms=1,
            bounds=_bounds(),
            implementation_id="bounded_evidence",
        )
        env = memory_envelope_from_snapshot(empty)
        self.assertEqual(env.status, "ready")
        self.assertIsInstance(env.value, MemorySnapshot)

        healthy = MemorySnapshot(
            memory_id="m",
            epoch_id="e",
            health="healthy",
            bounds=_bounds(),
            created_at_ms=1,
            records=(_record(),),
            implementation_id="bounded_evidence",
        )
        self.assertEqual(memory_envelope_from_snapshot(healthy).status, "ready")

        unavail = unavailable_memory_snapshot(
            memory_id="m",
            epoch_id="e",
            created_at_ms=1,
            bounds=_bounds(),
            implementation_id="bounded_evidence",
            reason="gone",
        )
        env = memory_envelope_from_snapshot(unavail)
        self.assertEqual(env.status, "unavailable")
        self.assertIsNone(env.value)

        err = error_memory_snapshot(
            memory_id="m",
            epoch_id="e",
            created_at_ms=1,
            bounds=_bounds(),
            implementation_id="bounded_evidence",
            error="boom",
        )
        env = memory_envelope_from_snapshot(err)
        self.assertEqual(env.status, "error")
        self.assertIsNone(env.value)
        self.assertIn("memory_error:", env.reason)

    def test_frozen_against_mutation(self) -> None:
        source = build_decision_data_source(
            frame_id="frame_001",
            frame_index=0,
            timestamp_ms=10,
            memory=empty_memory_snapshot(
                memory_id="m",
                epoch_id="e",
                created_at_ms=10,
                bounds=_bounds(),
                implementation_id="bounded_evidence",
            ),
        )
        payload = source.to_dict()
        payload["memory"]["status"] = "error"
        again = source.to_dict()
        self.assertEqual(again["memory"]["status"], "ready")
        # Dataclass freeze rejects attribute rebinding through normal assignment.
        with self.assertRaises(Exception):
            source.frame_id = "hijacked"  # type: ignore[misc]

    def test_prior_host_applied_default_unavailable(self) -> None:
        source = build_decision_data_source(
            frame_id="f1", frame_index=0, timestamp_ms=1
        )
        self.assertEqual(source.prior_host_applied_command.status, "unavailable")
        self.assertEqual(
            source.prior_host_applied_command.reason,
            "host_did_not_report_applied_command",
        )

    def test_rejects_bad_frame_id(self) -> None:
        with self.assertRaises(ValueError):
            build_decision_data_source(
                frame_id="😀" * 10, frame_index=0, timestamp_ms=1
            )

    def test_rejects_wrong_schema(self) -> None:
        with self.assertRaises(ValueError):
            DecisionDataSource(
                frame_id="f1",
                frame_index=0,
                timestamp_ms=1,
                observation=unavailable_envelope("x"),
                memory=unavailable_envelope("y"),
                patterns=unavailable_envelope("p"),
                projections=unavailable_envelope("q"),
                capabilities=ready_envelope({"max_abs_steering": 1.0}),
                prior_host_applied_command=unavailable_envelope("h"),
                schema="wrong",
            )

    def test_plugin_cannot_mutate_shared_capabilities(self) -> None:
        from autonomy.decision.shadow_runner import ShadowProposalsConfig, ShadowProposalsEngine
        from autonomy.decision.action_proposal import ActionProposal
        from autonomy.decision.decision_data import DecisionDataSource

        seen: list[object] = []

        def plugin_a(source: DecisionDataSource) -> ActionProposal:
            seen.append(source.capabilities.value)
            value = source.capabilities.value
            # Frozen mapping rejects item assignment; plain dict would mutate a
            # shared view — neither path may change peer observations.
            if isinstance(value, dict):
                value["max_abs_steering"] = 0.01
            else:
                try:
                    value["max_abs_steering"] = 0.01  # type: ignore[index]
                except TypeError:
                    pass
            return ActionProposal(
                plugin_id="a",
                frame_id=source.frame_id,
                lifecycle="inactive",
                freshness="none",
                confidence=0.0,
                reason="noop_a",
                command=None,
                available=False,
            )

        def plugin_b(source: DecisionDataSource) -> ActionProposal:
            seen.append(source.capabilities.value)
            return ActionProposal(
                plugin_id="b",
                frame_id=source.frame_id,
                lifecycle="inactive",
                freshness="none",
                confidence=0.0,
                reason="noop_b",
                command=None,
                available=False,
            )

        engine = ShadowProposalsEngine(
            config=ShadowProposalsConfig(
                enabled_plugins=("a", "b"),
            ),
            plugins={"a": plugin_a, "b": plugin_b},
        )
        engine.run_cycle(frame_id="frame_001", frame_index=0, timestamp_ms=1)
        self.assertEqual(len(seen), 2)
        # Peer still sees original frozen capabilities, not a mutated mapping.
        self.assertEqual(seen[0], seen[1])

    def test_ready_envelope_rejects_live_handles(self) -> None:
        class LiveClient:
            pass

        with self.assertRaises(TypeError):
            ready_envelope(LiveClient(), updated_at_ms=1)

        # Cycle must not return ok with a non-replayable source.
        from implementations.decision.catalog import create_shadow_proposals_engine

        with self.assertRaises(TypeError):
            create_shadow_proposals_engine().run_cycle(
                frame_id="f",
                frame_index=0,
                timestamp_ms=1,
                capabilities=ready_envelope(LiveClient(), updated_at_ms=1),
            )

    def test_rejects_evaluator_and_map_metadata(self) -> None:
        from autonomy.decision.memory import canonical_json_bytes

        with self.assertRaises(ValueError):
            build_decision_data_source(
                frame_id="f",
                frame_index=0,
                timestamp_ms=1,
                metadata={"evaluator": {"ground_truth": "privileged"}},
            )
        with self.assertRaises(ValueError):
            build_decision_data_source(
                frame_id="f",
                frame_index=0,
                timestamp_ms=1,
                metadata={"map": {"lanes": []}},
            )
        with self.assertRaises(ValueError):
            build_decision_data_source(
                frame_id="f",
                frame_index=0,
                timestamp_ms=1,
                metadata={"nested": {"reference_decision": 1}},
            )
        # Legal sources remain serializable end-to-end.
        source = build_decision_data_source(
            frame_id="f", frame_index=0, timestamp_ms=1
        )
        canonical_json_bytes(source.to_dict())


if __name__ == "__main__":
    unittest.main()
