from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.run import prepare_live_pi


ROOT = Path(__file__).resolve().parents[3]


def _runtime_status_response(
    *,
    drive_mode: str,
    perception_algorithm: str | None = "lightweight_observer",
) -> MagicMock:
    components = {}
    if perception_algorithm is not None:
        components["perception"] = {"algorithm": perception_algorithm}
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(
        {
            "ok": True,
            "drive_mode": drive_mode,
            "autonomy": {
                "engine": "autonomy.runtime.engine:IdleAutonomyEngine",
                "components": components,
            },
        }
    ).encode("utf-8")
    return response


class PiLiveRunnerTests(unittest.TestCase):
    def test_ready_pi_reports_loaded_activation_and_enables_live_test(self) -> None:
        response = _runtime_status_response(drive_mode="user")
        output = io.StringIO()

        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "cli.automa_cli.deploy.urllib_request.urlopen",
                return_value=response,
            ) as urlopen,
            redirect_stdout(output),
        ):
            ready = prepare_live_pi(
                base_url="http://piracer.test:8887",
                timeout_s=0.5,
            )
            self.assertEqual(os.environ["AUTOMA_TEST_LIVE_PI"], "1")
            self.assertEqual(
                os.environ["AUTOMA_TEST_PICAR_URL"],
                "http://piracer.test:8887",
            )
            urlopen.assert_called_once_with(
                "http://piracer.test:8887/autonomy/status",
                timeout=0.5,
            )

        self.assertTrue(ready)
        self.assertIn("result: ready", output.getvalue())
        self.assertIn("drive mode: user", output.getvalue())
        self.assertIn(
            "decision engine: autonomy.runtime.engine:IdleAutonomyEngine",
            output.getvalue(),
        )
        self.assertIn("perception: lightweight_observer", output.getvalue())

    def test_non_manual_pi_is_unavailable_and_does_not_enable_live_test(self) -> None:
        response = _runtime_status_response(drive_mode="local")
        output = io.StringIO()

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("cli.automa_cli.deploy.urllib_request.urlopen", return_value=response),
            redirect_stdout(output),
        ):
            ready = prepare_live_pi(
                base_url="http://piracer.test:8887",
                timeout_s=0.5,
            )
            self.assertNotIn("AUTOMA_TEST_LIVE_PI", os.environ)

        self.assertFalse(ready)
        self.assertIn("result: unavailable", output.getvalue())
        self.assertIn("drive mode is 'local'; expected 'user'", output.getvalue())
        self.assertIn(
            "No drive, mode-change, restart, or SSH command was sent.",
            output.getvalue(),
        )

    def test_missing_perception_activation_is_unavailable(self) -> None:
        response = _runtime_status_response(
            drive_mode="user",
            perception_algorithm=None,
        )
        output = io.StringIO()

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("cli.automa_cli.deploy.urllib_request.urlopen", return_value=response),
            redirect_stdout(output),
        ):
            ready = prepare_live_pi(
                base_url="http://piracer.test:8887",
                timeout_s=0.5,
            )
            self.assertNotIn("AUTOMA_TEST_LIVE_PI", os.environ)

        self.assertFalse(ready)
        self.assertIn("result: unavailable", output.getvalue())
        self.assertIn("did not report an active perception algorithm", output.getvalue())

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
