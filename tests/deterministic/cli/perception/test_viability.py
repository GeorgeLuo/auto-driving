"""The viability gate measures the loop's achievable rate, capture included."""
from __future__ import annotations

import unittest

from cli.automa_cli.hosts.viability import _compute_perception_metrics, _evaluate_perception_gates


def _sample(captured_at_ms: int, processed_count: int) -> dict:
    return {
        "health": "healthy",
        "captured_at_ms": captured_at_ms,
        "processed_count": processed_count,
        "duration_ms": 450.0,
        "result_age_ms": 100.0,
        "control_applied": False,
        "mode": "observe_only",
    }


class PerceptionViabilityTests(unittest.TestCase):
    def test_the_achievable_rate_spans_capture_and_cycle(self) -> None:
        # A 150 ms capture and a 450 ms cycle start one capture every 600 ms.
        # The poll repeats the first sample and misses the third cycle.
        samples = [_sample(0, 1), _sample(0, 1), _sample(600, 2), _sample(1800, 4), _sample(2400, 5)]
        metrics = _compute_perception_metrics(
            samples=samples, host_samples=[], elapsed_s=2.4, fresh_transitions=3,
        )
        self.assertEqual((metrics["cycle_period_ms"], metrics["cycle_hz"]), (600.0, 1.6667))
        cadence = next(
            gate for gate in _evaluate_perception_gates(metrics)
            if gate["id"] == "fresh_results_meet_configured_cadence"
        )
        self.assertTrue(cadence["passed"], cadence["detail"])


if __name__ == "__main__":
    unittest.main()
