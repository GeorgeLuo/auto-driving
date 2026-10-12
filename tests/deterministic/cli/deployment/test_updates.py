from __future__ import annotations

import unittest
from io import StringIO
from unittest.mock import patch

from cli.automa_cli.deploy import _resolve_picar_target


class DeploymentUpdateTests(unittest.TestCase):


    def test_core_can_bootstrap_the_configured_picar_when_http_is_down(self) -> None:
        output = StringIO()
        with patch(
            "cli.automa_cli.vehicles.discover_active_vehicles",
            return_value={"vehicles": []},
        ) as discover:
            target, error = _resolve_picar_target(
                vehicle_id="piracer",
                timeout_s=0.1,
                ssh_target=None,
                pi_home=None,
                skip_discovery=False,
                output=output,
                operation="core deploy",
                allow_offline_default=True,
            )

        self.assertIsNone(error)
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.ssh_target, "piracer@piracer.local")
        self.assertFalse(discover.call_args.kwargs["include_chase_sim"])
        self.assertIn("HTTP readiness is unavailable", output.getvalue())
        self.assertIn("SSH will determine deploy reachability", output.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
