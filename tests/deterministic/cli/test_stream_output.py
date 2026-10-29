"""Every step stream polls on one loop: each refresh is a probe with a status,
and ``--once`` writes one frame without a view and exits 0 only when live."""
from __future__ import annotations

import io
import json
import unittest
from unittest.mock import Mock, patch

from cli.automa_cli.hosts import decision_steps
from cli.automa_cli.hosts.streaming import poll_stream


# A fresh cycle whose plan record is empty.
NORMALIZED = {
    "result_age_ms": 0, "max_age_ms": 1000,
    "decision": {"host_name": "fixture", "frame_id": "next",
                 "values": {"activation": {"steps": {"plan": {"plugins": []}}}}, "cycle": {"plan": {}}},
}
OFFLINE = "Could not read the decision publication at http://host.invalid: offline"


def _stream_plan(fetch, *, feed=None, **kwargs):
    """Stream plan from a fixture host whose decision reads are ``fetch``; also return the view factory."""

    with (
        patch.object(decision_steps, "require_valid_activations"),
        patch.object(decision_steps, "runtime_base_url", return_value="http://host.invalid"),
        patch.object(decision_steps, "ViewFeed", return_value=feed) as view,
        patch.object(decision_steps, "fetch_decision_publication", side_effect=fetch),
        patch.object(decision_steps, "accept_decision_publication", return_value=NORMALIZED),
    ):
        return decision_steps.stream_host_step("plan", host_name="fixture", **kwargs), view


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
                    result = poll_stream(
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
                        result = poll_stream(
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

    def test_decision_stream_flushes_errors_and_recovers_with_its_view(self):
        for json_output in (False, True):
            with self.subTest(json=json_output):
                output = FlushedOutput()
                feed = Mock(url="http://view/", error=None)
                feed.read_observation.return_value = None, None
                feed.decision_line.return_value = "action view: http://view/decision"
                with patch("cli.automa_cli.hosts.streaming.time.sleep", side_effect=[None, KeyboardInterrupt]):
                    result, view = _stream_plan([ConnectionError("offline"), {}], feed=feed,
                                                json_output=json_output, output=output)
                self.assertEqual((result.exit_code, result.message), (130, ""))
                self.assertEqual(len(output.frames), 2)
                if json_output:
                    error, live = map(json.loads, output.getvalue().splitlines())
                    self.assertEqual((error["status"], error["error"], error["message"]),
                                     ("error", "runtime_decision_unavailable", OFFLINE))
                    self.assertEqual((live["schema"], live["status"], live["frame_id"]), ("host_plan_stream", "live", "next"))
                    view.assert_not_called()
                else:
                    footer = "\n\nperception view: http://view/perception\naction view: http://view/decision\n"
                    first = "\033[2J\033[H" + OFFLINE + footer
                    self.assertEqual(output.frames[0], first)
                    recovered = output.frames[1].removeprefix(first)
                    self.assertTrue(recovered.startswith("\033[2J\033[HPlan stream: fixture frame=next "), recovered)
                    self.assertTrue(recovered.endswith(footer), recovered)
                    feed.stop.assert_called_once_with()

    def test_decision_once_writes_one_frame_without_a_view(self):
        for json_output in (False, True):
            for fetch, code in ((ConnectionError("offline"), 2), ({}, 0)):
                with self.subTest(json=json_output, live=not code):
                    output = FlushedOutput()
                    with patch("cli.automa_cli.hosts.streaming.time.sleep") as sleep:
                        result, view = _stream_plan([fetch], once=True, json_output=json_output, output=output)
                    self.assertEqual((result.exit_code, result.message), (code, ""))
                    line = output.getvalue()
                    self.assertEqual(output.frames, [line])
                    if json_output:
                        payload = json.loads(line)
                        self.assertEqual(line, json.dumps(payload, sort_keys=True) + "\n")
                        self.assertEqual(payload["status"], "error" if code else "live")
                    else:
                        self.assertTrue(line.startswith(OFFLINE if code else "Plan stream: fixture"), line)
                    view.assert_not_called()
                    sleep.assert_not_called()
