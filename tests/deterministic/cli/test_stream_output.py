"""Stream policies keep their output contracts while sharing terminal writing."""
from __future__ import annotations

import io
import json
import unittest
from unittest.mock import Mock, patch

from cli.automa_cli.hosts.decision import DecisionSurfaceError
from cli.automa_cli.hosts.decision_steps import _poll
from cli.automa_cli.hosts.streaming import _poll_stream


class FlushedOutput(io.StringIO):
    def __init__(self):
        super().__init__()
        self.frames = []

    def flush(self):
        self.frames.append(self.getvalue())


class StreamOutputTests(unittest.TestCase):
    def test_probe_stream_flushes_each_verdict_and_recovers_before_interrupt(self):
        for json_output, no_clear in ((False, False), (False, True), (True, False)):
            with self.subTest(json=json_output, no_clear=no_clear):
                output = FlushedOutput()
                probes = [{"status": "unavailable", "error": "offline"}, {"status": "live"}]
                probe = Mock(side_effect=[(item, None) for item in probes])
                with patch("cli.automa_cli.hosts.streaming.time.sleep", side_effect=[None, KeyboardInterrupt]) as sleep:
                    result = _poll_stream(
                        step="memory", probe=probe, render=lambda live, _: live["status"],
                        refresh_s=0, once=False, no_clear=no_clear,
                        json_output=json_output, stream=output,
                    )
                prefix = "\033[2J\033[H" if not json_output and not no_clear else ""
                lines = [json.dumps(item, sort_keys=True) if json_output else item["status"] for item in probes]
                first = prefix + lines[0] + "\n"
                self.assertEqual(output.frames, [first, first + prefix + lines[1] + "\n"])
                self.assertEqual((result.exit_code, result.message), (130, ""))
                self.assertEqual([call.args for call in sleep.call_args_list], [(0.1,), (0.1,)])

    def test_probe_once_writes_or_returns_the_same_json_verdict(self):
        for status, code in (("live", 0), ("stale", 2)):
            for connected in (True, False):
                with self.subTest(status=status, connected=connected):
                    output = FlushedOutput() if connected else None
                    live = {"status": status}
                    with patch("cli.automa_cli.hosts.streaming.time.sleep") as sleep:
                        result = _poll_stream(
                            step="perception", probe=lambda: (live, None),
                            render=Mock(side_effect=AssertionError("JSON must not render")),
                            refresh_s=1, once=True, no_clear=False, json_output=True, stream=output,
                        )
                    self.assertEqual(result.exit_code, code)
                    line = json.dumps(live, sort_keys=True)
                    self.assertEqual(result.message, "" if connected else line)
                    if connected:
                        self.assertEqual(output.frames, [line + "\n"])
                    sleep.assert_not_called()

    def test_decision_stream_flushes_errors_and_recovers_with_its_footer(self):
        error = DecisionSurfaceError("unavailable", "offline", host_name="fixture")
        for json_output, no_clear in ((False, False), (False, True), (True, False)):
            with self.subTest(json=json_output, no_clear=no_clear):
                output = FlushedOutput()
                with patch("cli.automa_cli.hosts.decision_steps.time.sleep", side_effect=[None, KeyboardInterrupt]) as sleep:
                    result = _poll(
                        host_name="fixture", accept=Mock(side_effect=[error, {"frame_id": "next"}]),
                        render=lambda frame: frame["frame_id"], footer=lambda: ["view"],
                        refresh_s=0, once=False, no_clear=no_clear,
                        json_output=json_output, output=output,
                    )
                self.assertEqual((result.exit_code, result.message), (130, ""))
                self.assertEqual(len(output.frames), 2)
                if json_output:
                    lines = output.getvalue().splitlines()
                    self.assertEqual(json.loads(lines[0])["message"], "offline")
                    self.assertEqual(lines[1], '{"frame_id": "next"}')
                    self.assertEqual(output.frames[0], lines[0] + "\n")
                else:
                    prefix = "" if no_clear else "\033[2J\033[H"
                    first = prefix + "offline\n\nview\n"
                    self.assertEqual(output.frames, [first, first + prefix + "next\n\nview\n"])
                self.assertEqual([call.args for call in sleep.call_args_list], [(0.05,), (0.05,)])

    def test_decision_interrupt_retains_the_last_error_without_output(self):
        with patch("cli.automa_cli.hosts.decision_steps.time.sleep", side_effect=KeyboardInterrupt):
            result = _poll(
                host_name="fixture",
                accept=Mock(side_effect=DecisionSurfaceError("unavailable", "offline")),
                render=str, refresh_s=0, once=False, no_clear=False, json_output=False, output=None,
            )
        self.assertEqual((result.exit_code, result.message), (130, "offline"))

    def test_decision_once_returns_pretty_json_without_writing_or_sleeping(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                output = FlushedOutput()
                accept = (Mock(side_effect=DecisionSurfaceError("unavailable", "offline"))
                          if fail else Mock(return_value={"frame_id": "one"}))
                with patch("cli.automa_cli.hosts.decision_steps.time.sleep") as sleep:
                    result = _poll(
                        host_name="fixture", accept=accept, render=str,
                        refresh_s=1, once=True, no_clear=False, json_output=True, output=output,
                    )
                self.assertEqual(result.exit_code, 2 if fail else 0)
                payload = json.loads(result.message)
                self.assertEqual(result.message, json.dumps(payload, indent=2, sort_keys=True))
                self.assertEqual(output.frames, [])
                sleep.assert_not_called()
