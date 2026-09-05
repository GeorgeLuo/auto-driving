from __future__ import annotations

import json
import os
import unittest

from tests.support.cli_runner import run_automa


_SESSION_FIELDS = (
    "game_id",
    "scenario_id",
    "simulation_epoch",
    "playback",
    "control_source",
    "control_input",
)


def _preserved_session_fingerprint(payload: dict) -> dict:
    passive = payload["layers"]["passive_capture"]
    preservation = passive["session_preservation"]
    if passive["state"] != "available" or preservation["preserved"] is not True:
        raise AssertionError(f"passive session is not proven: {passive}")
    if passive.get("mutation_attempted") is not False:
        raise AssertionError(f"passive capture attempted a mutation: {passive}")
    before = preservation["before"]
    after = preservation["after"]
    fingerprint = {field: before[field] for field in _SESSION_FIELDS}
    if fingerprint != {field: after[field] for field in _SESSION_FIELDS}:
        raise AssertionError(f"passive capture changed the session: {preservation}")
    return fingerprint


class ChaseSimulatorSmokeTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("AUTOMA_TEST_LIVE_SIM") == "1",
        "set AUTOMA_TEST_LIVE_SIM=1 to run live simulator integration",
    )
    def test_scenario_live_simulator_bounded_automation_smoke(self) -> None:
        initial = run_automa(
            "vehicles",
            "status",
            "--id",
            "chase-sim-chaser",
            "--chase-url",
            "http://localhost:5050",
            "--json",
        )
        initial_payload = json.loads(initial.stdout)
        self.assertEqual(
            initial_payload["layers"]["passive_capture"]["state"],
            "available",
        )
        initial_fingerprint = _preserved_session_fingerprint(initial_payload)

        run_automa(
            "vehicles",
            "update",
            "perception",
            "--id",
            "chase-sim-chaser",
            "--algorithm",
            "lightweight_observer",
            "--timeout-s",
            "6",
        )
        try:
            run = run_automa(
                "vehicles",
                "automation",
                "run",
                "--id",
                "chase-sim-chaser",
                "--observe-only",
                "--frames",
                "0",
                "--open-view",
                "--timeout-s",
                "6",
            )
            self.assertIn("Automation ready", run.stdout)
            self.assertIn("Perception view: http://127.0.0.1:", run.stdout)

            status = run_automa(
                "vehicles",
                "status",
                "--id",
                "chase-sim-chaser",
                "--json",
            )
            payload = json.loads(status.stdout)
            self.assertEqual(
                payload["layers"]["automation_worker"]["state"],
                "running",
            )
            self.assertEqual(
                payload["layers"]["perception_view"]["state"],
                "available",
            )
            authority = payload["layers"]["automation_worker"]["details"]["authority"]
            self.assertEqual(authority["action_policy"], "observe_only")
            self.assertEqual(authority["control_application"], "not_applied")
            self.assertEqual(
                _preserved_session_fingerprint(payload),
                initial_fingerprint,
            )
        finally:
            run_automa(
                "vehicles",
                "automation",
                "stop",
                "--id",
                "chase-sim-chaser",
            )

        stopped = json.loads(
            run_automa(
                "vehicles",
                "status",
                "--id",
                "chase-sim-chaser",
                "--json",
            ).stdout
        )
        self.assertEqual(
            stopped["layers"]["automation_worker"]["state"],
            "stopped",
        )
        self.assertNotEqual(
            stopped["layers"]["perception_view"]["state"],
            "available",
        )
        self.assertEqual(
            _preserved_session_fingerprint(stopped),
            initial_fingerprint,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
