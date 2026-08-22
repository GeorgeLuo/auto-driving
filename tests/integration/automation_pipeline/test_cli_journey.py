from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.support.cli_runner import run_automa
from tests.support.fake_metrics_ui import fake_metrics_ui_server


def _session_fingerprint(payload: dict) -> dict:
    passive = payload["layers"]["passive_capture"]
    preservation = passive["session_preservation"]
    if passive["state"] != "available" or preservation["preserved"] is not True:
        raise AssertionError(f"passive session is not proven: {passive}")
    if passive["mutation_attempted"] is not False:
        raise AssertionError(f"passive capture attempted a mutation: {passive}")
    if preservation["before"] != preservation["after"]:
        raise AssertionError(f"passive capture changed the session: {preservation}")
    return preservation["before"]


class SimulatorPerceptionCliJourneyTests(unittest.TestCase):
    def test_primary_journey_uses_shared_passive_gates_and_cleans_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            with fake_metrics_ui_server() as ws_url:
                env = {
                    "BROWSER": "true",
                    "CHASE_UI_WS_URL": ws_url,
                }
                http_url = "http" + ws_url.removeprefix("ws").removesuffix(
                    "/ws/control"
                )

                initial = json.loads(
                    run_automa(
                        "vehicles",
                        "status",
                        "--chase-url",
                        http_url,
                        "--json",
                        runtime_root=runtime_root,
                        extra_env=env,
                    ).stdout
                )
                self.assertEqual(len(initial["vehicles"]), 1)
                initial_vehicle = initial["vehicles"][0]
                self.assertEqual(
                    initial_vehicle["layers"]["passive_capture"]["state"],
                    "available",
                )
                self.assertEqual(
                    initial_vehicle["layers"]["automation_deployment"]["state"],
                    "not_deployed",
                )
                initial_fingerprint = _session_fingerprint(initial_vehicle)

                update = run_automa(
                    "vehicles",
                    "update",
                    "perception",
                    "--id",
                    "chase-sim-chaser",
                    "--algorithm",
                    "lightweight_observer",
                    runtime_root=runtime_root,
                    extra_env=env,
                )
                self.assertIn(
                    "Ready for: observation-only automation",
                    update.stdout,
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
                        runtime_root=runtime_root,
                        extra_env=env,
                    )
                    self.assertIn("Automation ready", run.stdout)
                    self.assertIn(
                        "Ready for: inspect perception and stop automation",
                        run.stdout,
                    )

                    running = json.loads(
                        run_automa(
                            "vehicles",
                            "status",
                            "--id",
                            "chase-sim-chaser",
                            "--json",
                            runtime_root=runtime_root,
                            extra_env=env,
                        ).stdout
                    )
                    self.assertEqual(
                        running["layers"]["automation_worker"]["state"],
                        "running",
                    )
                    self.assertEqual(
                        running["layers"]["perception_view"]["state"],
                        "available",
                        json.dumps(running, indent=2, sort_keys=True),
                    )
                    authority = running["layers"]["automation_worker"]["details"][
                        "authority"
                    ]
                    self.assertEqual(authority["action_policy"], "observe_only")
                    self.assertEqual(
                        authority["control_application"],
                        "not_applied",
                    )
                    self.assertEqual(
                        _session_fingerprint(running),
                        initial_fingerprint,
                    )
                finally:
                    stopped = run_automa(
                        "vehicles",
                        "automation",
                        "stop",
                        "--id",
                        "chase-sim-chaser",
                        runtime_root=runtime_root,
                        extra_env=env,
                        check=False,
                    )

                self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
                self.assertIn(
                    "Ready for: inspect stopped deployment",
                    stopped.stdout,
                )

                final = json.loads(
                    run_automa(
                        "vehicles",
                        "status",
                        "--id",
                        "chase-sim-chaser",
                        "--json",
                        runtime_root=runtime_root,
                        extra_env=env,
                    ).stdout
                )
                self.assertEqual(
                    final["layers"]["automation_deployment"]["state"],
                    "deployed",
                )
                self.assertEqual(
                    final["layers"]["automation_worker"]["state"],
                    "stopped",
                )
                self.assertNotEqual(
                    final["layers"]["perception_view"]["state"],
                    "available",
                )
                self.assertEqual(
                    _session_fingerprint(final),
                    initial_fingerprint,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
