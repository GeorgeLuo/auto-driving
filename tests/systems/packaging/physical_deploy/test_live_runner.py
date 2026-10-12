from __future__ import annotations

import os
import socket
import subprocess
import sys
import unittest

from tests.support.cli_runner import WORKSPACE_ROOT as ROOT


class PiLiveRunnerTests(unittest.TestCase):


    def test_unreachable_pi_is_an_explicit_nonzero_unavailable_result(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = int(reserve.getsockname()[1])
            env = dict(os.environ)
            env.pop("AUTOMA_TEST_LIVE_PI", None)
            env["AUTOMA_TEST_LIVE_SIM"] = "0"
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            result = subprocess.run(
                [
                    sys.executable,
                    "tests/run.py",
                    "--live-pi",
                    "--picar-url",
                    f"http://127.0.0.1:{port}",
                    "--pi-timeout-s",
                    "0.2",
                ],
                cwd=ROOT,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2, output)
        self.assertIn("result: unavailable", output)
        self.assertIn("side effects: none", output)
        self.assertIn("No drive, mode-change, restart, or SSH command was sent.", output)
        self.assertNotIn("Traceback", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
