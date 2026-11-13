"""``hosts still`` saves one processed frame and opens it."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.providers.metadata import CHASE_SIM, PICAR
from cli.automa_cli.shared.browser_result import BrowserOpenResult
from cli.automa_cli.shared.paths import display_path
from tests.shared.cli.invoke import invoke_automa

_JPEG = b"\xff\xd8\xff\xd9"
_URL = "http://127.0.0.1:9"


def _publication(**extra: object) -> dict[str, object]:
    publication: dict[str, object] = {
        "health": "healthy",
        "result_age_ms": 40,
        "report": {"frame_id": "frame-7"},
    }
    publication.update(extra)
    return publication


class HostStillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.view = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.runtime = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.opened: list[str] = []

    def _open(self, url: str) -> BrowserOpenResult:
        self.opened.append(url)
        return BrowserOpenResult(url, True)

    def _still(self, *patches: patch, host_name: str = "chase"):
        with ExitStack() as stack:
            stack.enter_context(patch("cli.automa_cli.hosts.views.still.runtime_view_dir", return_value=self.view))
            stack.enter_context(patch("cli.automa_cli.hosts.views.still.launch_browser", side_effect=self._open))
            for applied in patches:
                stack.enter_context(applied)
            return invoke_automa(
                "hosts", "still", "--name", host_name, runtime_root=self.runtime, check=False,
            )

    def test_a_frame_is_saved_and_opened(self) -> None:
        sample = patch(
            "cli.automa_cli.hosts.views.still.fetch_frame_sample",
            return_value=(_publication(), _JPEG),
        )
        resolve = patch("cli.automa_cli.hosts.views.still.runtime_base_url", return_value=_URL)
        result = self._still(sample, resolve)

        path = self.view / "still.jpg"
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(path.read_bytes(), _JPEG)
        self.assertEqual(self.opened, [path.resolve().as_uri()])
        self.assertEqual(
            result.stdout,
            "\n".join([
                f"Still of host chase at {_URL}",
                "frame: frame-7",
                "age: 40 ms",
                "health: healthy",
                display_path(path),
                f"Browser opened: {path.resolve().as_uri()}",
                "",
            ]),
        )

    def test_a_stopped_chase_host_keeps_its_last_frame(self) -> None:
        sample = patch(
            "cli.automa_cli.hosts.views.still.fetch_frame_sample",
            return_value=(_publication(health="stale", result_age_ms=900), _JPEG),
        )
        resolve = patch("cli.automa_cli.hosts.views.still.runtime_base_url", return_value=_URL)
        state = patch("cli.automa_cli.hosts.views.still.host_state", return_value={"state": "stopped"})
        result = self._still(sample, resolve, state)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("health: stale", result.stdout)
        self.assertNotIn("Start a run.", result.stdout)
        self.assertEqual((self.view / "still.jpg").read_bytes(), _JPEG)

    def test_an_idle_chase_host_is_told_to_start_a_run(self) -> None:
        sample = patch(
            "cli.automa_cli.hosts.views.still.fetch_frame_sample",
            return_value=(_publication(health="warming", report=None, result_age_ms=None), None),
        )
        resolve = patch("cli.automa_cli.hosts.views.still.runtime_base_url", return_value=_URL)
        state = patch("cli.automa_cli.hosts.views.still.host_state", return_value={"state": "pre_start"})
        provider = patch(
            "cli.automa_cli.hosts.views.still.staged_host",
            return_value={"provider": CHASE_SIM.name},
        )
        result = self._still(sample, resolve, state, provider)

        self.assertEqual((result.returncode, result.stdout), (2, "No frame from host chase. Start a run.\n"))
        self.assertEqual(self.opened, [])
        self.assertFalse((self.view / "still.jpg").exists())

    def test_a_running_chase_host_without_an_image_reports_its_health(self) -> None:
        sample = patch(
            "cli.automa_cli.hosts.views.still.fetch_frame_sample",
            return_value=(_publication(health="warming", report=None), None),
        )
        resolve = patch("cli.automa_cli.hosts.views.still.runtime_base_url", return_value=_URL)
        state = patch("cli.automa_cli.hosts.views.still.host_state", return_value={"state": "running"})
        provider = patch(
            "cli.automa_cli.hosts.views.still.staged_host",
            return_value={"provider": CHASE_SIM.name},
        )
        result = self._still(sample, resolve, state, provider)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("health is 'warming'", result.stdout)
        self.assertNotIn("Start a run.", result.stdout)
        self.assertEqual(self.opened, [])

    def test_a_picar_without_an_image_is_camera_not_ready(self) -> None:
        sample = patch(
            "cli.automa_cli.hosts.views.still.fetch_frame_sample",
            return_value=(_publication(health="warming", report=None), None),
        )
        resolve = patch("cli.automa_cli.hosts.views.still.runtime_base_url", return_value=_URL)
        provider = patch(
            "cli.automa_cli.hosts.views.still.staged_host",
            return_value={"provider": PICAR.name},
        )
        state = patch(
            "cli.automa_cli.hosts.views.still.host_state",
            side_effect=AssertionError("a PiCar still does not read the run"),
        )
        result = self._still(sample, resolve, provider, state, host_name="piracer")

        self.assertEqual(
            (result.returncode, result.stdout),
            (2, "No frame from host piracer. The camera is not ready.\n"),
        )
        self.assertEqual(self.opened, [])
        self.assertFalse((self.view / "still.jpg").exists())

    def test_a_missing_host_exits_2(self) -> None:
        result = invoke_automa("hosts", "still", "--name", "missing", runtime_root=self.runtime, check=False)
        self.assertEqual((result.returncode, result.stdout), (2, "No host 'missing'. No hosts exist.\n"))

    def test_a_silent_host_exits_2(self) -> None:
        sample = patch(
            "cli.automa_cli.hosts.views.still.fetch_frame_sample",
            side_effect=ConnectionError("timed out"),
        )
        resolve = patch("cli.automa_cli.hosts.views.still.runtime_base_url", return_value=_URL)
        result = self._still(sample, resolve)

        self.assertEqual(
            (result.returncode, result.stdout),
            (2, f"Cannot read a frame from host chase at {_URL}: timed out\n"),
        )
        self.assertEqual(self.opened, [])

    def test_hosts_help_lists_still(self) -> None:
        summary = invoke_automa("hosts", "help", runtime_root=self.runtime)
        self.assertIn("- still", summary.stdout)
        self.assertIn("latest frame", summary.stdout)
        details = invoke_automa("hosts", "still", "--help", runtime_root=self.runtime)
        self.assertIn("--name", details.stdout)
        self.assertNotIn("--host-url", details.stdout)
