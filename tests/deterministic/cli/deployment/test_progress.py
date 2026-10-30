"""Deployment progress belongs to the command's chosen output channel."""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from cli.automa_cli.app import main
from cli.automa_cli.hosts.deployment.models import DeploymentResult
from cli.automa_cli.hosts.deployment.picar_deploy import run_step
from cli.automa_cli.hosts.deployment.render import render_progress
from cli.automa_cli.shared.progress import ProgressEvent


class DeploymentProgressTests(unittest.TestCase):
    def test_json_receipt_and_progress_use_separate_channels(self):
        def update(*, emit, **options):
            emit(ProgressEvent("step", {"label": "Sync controller"}))
            emit(ProgressEvent("output", {"line": "transfer complete"}))
            return DeploymentResult(0, receipt={"host_name": "car", "complete": True})

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("cli.automa_cli.hosts.deployment.commands.update_host", side_effect=update), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["hosts", "update", "--name", "car", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"host_name": "car", "complete": True})
        self.assertEqual(stderr.getvalue(), "==> Sync controller\ntransfer complete\n")

    def test_process_output_is_reported_without_printing(self):
        events = []
        child = SimpleNamespace(stdout=iter(["first line\n", "\n", "last line\n"]), wait=lambda: 7)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("cli.automa_cli.hosts.deployment.picar_deploy.subprocess.Popen", return_value=child), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            code = run_step("Copy source", ["rsync", "source with spaces", "target"],
                            env=None, verbose=True, emit=events.append)
        self.assertEqual(code, 7)
        self.assertEqual((stdout.getvalue(), stderr.getvalue()), ("", ""))
        self.assertEqual(events[1].data["command"], ["rsync", "source with spaces", "target"])
        self.assertEqual([render_progress(event) for event in events],
                         ["==> Copy source", "$ rsync 'source with spaces' target", "first line", "", "last line"])

    def test_progress_observation_is_optional(self):
        child = SimpleNamespace(stdout=iter(["output\n"]), wait=lambda: 0)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("cli.automa_cli.hosts.deployment.picar_deploy.subprocess.Popen", return_value=child), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            code = run_step("Copy", ["rsync"], env=None, verbose=True, emit=None)
        self.assertEqual((code, stdout.getvalue(), stderr.getvalue()), (0, "", ""))
