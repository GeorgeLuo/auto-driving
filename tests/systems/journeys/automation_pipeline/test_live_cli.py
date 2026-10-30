"""Step streams started through ``cli/automa`` against a running runtime host.

A stream runs until it is stopped, so these tests start the executable. The
other live commands run in this process in
``deterministic/cli/automation_pipeline/test_live_cli.py``.
"""
from __future__ import annotations

import hashlib
import json
import threading
import unittest
import uuid
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from cli.automa_cli.hosts.publications.host_publications import fetch_decision_publication
from cli.automa_cli.shared.paths import ROOT
from tests.shared.cli.automation_pipeline.pipeline_fixtures import HOST_NAME
from tests.shared.cli.invoke import invoke_automa
from tests.shared.integration.live_host import LiveHostTestCase, wait_until as _wait
from tests.support.cli_runner import run_automa, start_automa


class LiveCommandsTests(LiveHostTestCase):
    def test_stream_plan_checks_an_uploaded_plan_the_executable_never_loaded(self) -> None:
        # The stream accepts the plan an uploaded plugin chose without loading
        # that plugin itself.
        source = (ROOT / "implementations" / "decision_cycle" / "plan" / "plugins"
                  / "highest_confidence" / "plugin.py").read_bytes() + f"# {uuid.uuid4().hex}\n".encode()
        name = f"{hashlib.sha256(source).hexdigest()}.py"
        file = self.runtime_root.parent / "highest_confidence.py"
        file.write_bytes(source)
        invoke_automa(
            "plugins", "upload", "--host-name", HOST_NAME, "--file", str(file), "--name", "plan/highest_confidence",
            "--entrypoint", "highest_confidence:HighestConfidencePlan",
            "--arm", runtime_root=self.runtime_root,
        )
        self.observe()

        def published() -> bool:
            try:
                return name in json.dumps(fetch_decision_publication(self.host.base_url))
            except (OSError, ValueError):
                return False

        _wait(published, message="the host never published a cycle from the uploaded plan")
        result = run_automa("plugins", "stream", "--step", "plan", "--host-name", HOST_NAME, "--once", "--json",
                            runtime_root=self.runtime_root, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        frame = json.loads(result.stdout)
        self.assertEqual(frame["plugins"], ["highest_confidence"])
        self.assertEqual(frame["record"]["selector_id"], "highest_confidence")

    def test_a_running_step_stream_hosts_the_decision_view(self) -> None:
        # How each proposal arose is on the decision page: the host's latest
        # decision beside its frame and the evidence its proposals read.
        self.observe()
        stream = start_automa(
            "plugins", "stream", "--step", "proposal", "--host-name", HOST_NAME, "--no-clear",
            runtime_root=self.runtime_root,
        )
        self.addCleanup(stream.stdout.close)
        self.addCleanup(stream.wait, 10)
        self.addCleanup(stream.terminate)
        lines: list[str] = []

        def collect() -> None:
            for line in stream.stdout:
                lines.append(line.rstrip("\n"))

        threading.Thread(target=collect, daemon=True).start()
        page = _wait(
            lambda: next((line.split()[2] for line in lines if line.startswith("action view: http")), None),
            timeout_s=20, message="the proposal stream printed no action view",
        )
        with urlopen(page, timeout=5) as response:
            self.assertEqual(response.headers.get_content_type(), "text/html")
        view_url, _, generation = page.partition("/decision?")

        def latest():
            try:
                with urlopen(f"{view_url}/api/decision/latest?{generation}", timeout=5) as response:
                    return json.load(response)
            except HTTPError:  # warming, or the newest cycle expired between refreshes
                return None

        decision = _wait(latest, message="the decision view never served a current decision")
        self.assertEqual(decision["decision"]["frame_id"], decision["current_image"]["frame_id"])
        self.assertIsNotNone(decision["decision"]["cycle"]["proposal"])
        self.assertIn("evidence", decision)

    def test_a_stream_poll_during_cycle_publication_receives_a_coherent_decision(self) -> None:
        self.observe()
        loop = self.host.loop
        publishing = threading.Event()
        release = threading.Event()
        reading = threading.Event()
        returned = threading.Event()
        capture = loop._capture_decision_publication
        publish = loop.publish_decision_latest

        def pause_publication(**kwargs):
            if not publishing.is_set():
                publishing.set()
                if not release.wait(10):
                    raise TimeoutError("the test never released the completed cycle")
            return capture(**kwargs)

        def read_publication(**kwargs):
            reading.set()
            result = publish(**kwargs)
            returned.set()
            return result

        # This check deliberately holds a completed cycle while a fresh CLI
        # interpreter starts. Keep that artificial pause inside its display
        # window; expiry is covered separately by the publication tests.
        with patch("autonomy.runtime.frame_loop.stale_after_ms", return_value=10_000), \
                patch.object(loop, "_capture_decision_publication", side_effect=pause_publication), \
                patch.object(loop, "publish_decision_latest", side_effect=read_publication):
            try:
                self.assertTrue(publishing.wait(10), "the host never completed another cycle")
                stream = start_automa(
                    "plugins", "stream", "--step", "proposal", "--host-name", HOST_NAME,
                    "--once", "--json", runtime_root=self.runtime_root,
                )
                self.addCleanup(stream.stdout.close)
                self.addCleanup(stream.wait, 10)
                self.addCleanup(stream.terminate)
                self.assertTrue(reading.wait(10), "the CLI never polled the decision route")
                self.assertFalse(returned.wait(0.1), "the decision route exposed a partly published cycle")
            finally:
                release.set()
            output, _ = stream.communicate(timeout=10)
        self.assertEqual(stream.returncode, 0, output)
        decision = json.loads(output)
        self.assertEqual(decision["frame_id"], decision["record"]["frame_id"])
        self.assertEqual(decision["plugins"], ["avoid_recent_obstruction"])


if __name__ == "__main__":
    unittest.main()
