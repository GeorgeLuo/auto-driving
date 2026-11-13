"""Every step stream polls on one loop: each refresh is a probe with a status,
and ``--once`` writes one frame without a view and exits 0 only when live.

Presentation goes through ``stream_host_step``. The poll loop itself carries
no terminal flags and no view.
"""
from __future__ import annotations

import ast
import io
import json
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from autonomy.decision_cycle.cycle import CYCLE_RECORD_KEYS
from autonomy.runtime.frame_loop import FRAME_PUBLICATION_SCHEMA
from autonomy.runtime.report import HostReport, ReportApplication
from autonomy.decision_cycle.perception.interface import PerceptionText
from cli.automa_cli.plugins.stream import decision, memory, perception, session
from cli.automa_cli.plugins.stream.commands import stream_host_step
from cli.automa_cli.plugins.stream.models import PerceptionPublication, StreamEvent, ViewState
from cli.automa_cli.plugins.stream.polling import poll_events
from cli.automa_cli.plugins.stream.render import render_stream
from cli.automa_cli.hosts.errors import RuntimeHostError


# A fresh cycle whose plan record is empty.
NORMALIZED = {
    "result_age_ms": 0, "max_age_ms": 1000,
    "report": HostReport(
        host_name="fixture", run_id="run", generation_id="generation",
        frame_id="next", frame_index=0, timestamp_ms=1, published_at_ms=1,
        values={"activation": {"steps": {"plan": {"plugins": []}}}},
        cycle={
            "perception": None, "observation": None, "memory": None,
            "proposal": {}, "plan": {}, "action": {},
        },
        application=ReportApplication(False, "observe_only", "observe-only", 0, 0),
    ),
}
# What the host serves for NORMALIZED; acceptance itself is patched.
PUBLICATION = {"health": "healthy", "report": NORMALIZED["report"].to_dict()}
OFFLINE = "Could not read the decision publication at http://host.invalid: offline"
ROOT = Path(__file__).resolve().parents[3]
STREAM = ROOT / "cli" / "automa_cli" / "plugins" / "stream"
OPERATIONS = ("service.py", "polling.py", "perception.py", "memory.py", "decision.py", "session.py", "models.py")


def _stream_plan(fetch, *, feed=None, **kwargs):
    """Stream plan from a fixture host whose decision reads are ``fetch``; also return the view factory."""

    with (
        patch.object(decision, "require_valid_activations"),
        patch.object(decision, "runtime_base_url", return_value="http://host.invalid"),
        patch.object(session, "ViewFeed", return_value=feed) as view,
        patch.object(decision, "fetch_frame_publication", side_effect=fetch),
        patch.object(decision, "accept_decision_publication", return_value=NORMALIZED),
    ):
        return stream_host_step("plan", host_name="fixture", **kwargs), view


def _referenced_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                found.add(node.module)
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def _frame_publication(*, health="healthy", status="ok", error=None, **cycle):
    """A host's frame publication that the stream readers accept for host ``fixture``."""

    report = HostReport(
        host_name="fixture", run_id="run", generation_id="generation",
        frame_id="next", frame_index=0, timestamp_ms=1, published_at_ms=1,
        values={"source_id": "fixture:local", "stale_after_ms": 1000,
                "activation": {"steps": {}}},
        cycle={**{step: None for step in CYCLE_RECORD_KEYS}, **cycle},
        application=ReportApplication(False, "observe_only", "observe-only", 0, 0),
        status=status, error=error,
    )
    return {"schema": FRAME_PUBLICATION_SCHEMA, "health": health, "result_age_ms": 1500,
            "stale_after_ms": 1000, "report": report.to_dict()}


class FlushedOutput(io.StringIO):
    def __init__(self):
        super().__init__()
        self.frames = []

    def flush(self):
        self.frames.append(self.getvalue())


class StreamOutputTests(unittest.TestCase):
    def test_polling_reports_the_probe_without_presentation(self):
        events = []
        with patch("cli.automa_cli.plugins.stream.polling.time.sleep") as sleep:
            live = poll_events(
                step="memory", probe=lambda: ({"status": "live", "error": "ignored"}, {"report": 1}),
                refresh_s=1, once=True, emit=events.append,
            )
            stale = poll_events(
                step="memory", probe=lambda: ({"status": "stale", "error": "late"}, None),
                refresh_s=0.5, once=True, emit=events.append,
            )
        self.assertEqual(live.exit_code, 0)
        self.assertEqual(stale.exit_code, 2)
        self.assertEqual(stale.message, "late")
        self.assertEqual(events[0].source, {"report": 1})
        self.assertIsNone(events[0].view)
        self.assertFalse(hasattr(events[0], "json_output"))
        self.assertFalse(hasattr(events[0], "clear"))
        sleep.assert_not_called()

        with patch("cli.automa_cli.plugins.stream.polling.time.sleep", side_effect=KeyboardInterrupt) as sleep:
            interrupted = poll_events(
                step="memory", probe=lambda: ({"status": "live"}, None),
                refresh_s=0, once=False, emit=events.append,
            )
        self.assertEqual((interrupted.exit_code, interrupted.message), (130, ""))
        self.assertEqual([call.args for call in sleep.call_args_list], [(0.1,)])

    def test_probe_stream_flushes_each_verdict_and_recovers_before_interrupt(self):
        for json_output, no_clear in ((False, False), (False, True), (True, False)):
            with self.subTest(json=json_output, no_clear=no_clear):
                output = FlushedOutput()
                probes = [{"status": "error", "error": "offline"}, {"status": "error", "error": "still offline"}]
                pending = iter((item, None) for item in probes)
                seen = {}

                def fake_host(step, *, emit, refresh_s, once, serve_view, include_publication, **kwargs):
                    seen["serve_view"] = serve_view
                    seen["include_publication"] = include_publication
                    return poll_events(
                        step=step, probe=lambda: next(pending), refresh_s=refresh_s, once=once, emit=emit,
                    )

                with (
                    patch("cli.automa_cli.plugins.stream.commands.stream_host", side_effect=fake_host),
                    patch("cli.automa_cli.plugins.stream.polling.time.sleep", side_effect=[None, KeyboardInterrupt]) as sleep,
                ):
                    result = stream_host_step(
                        "plan", host_name="fixture", refresh_s=0, once=False, no_clear=no_clear,
                        json_output=json_output, output=output,
                    )
                prefix = "\033[2J\033[H" if not json_output and not no_clear else ""
                lines = [json.dumps(item, sort_keys=True) if json_output else item["error"] for item in probes]
                first = prefix + lines[0] + "\n"
                self.assertEqual(output.frames, [first, first + prefix + lines[1] + "\n"])
                self.assertEqual((result.exit_code, result.message), (130, ""))
                self.assertEqual([call.args for call in sleep.call_args_list], [(0.1,), (0.1,)])
                self.assertEqual(seen["serve_view"], not json_output)
                self.assertEqual(seen["include_publication"], not json_output)

    def test_probe_once_writes_or_returns_the_same_json_verdict(self):
        for status, code in (("live", 0), ("stale", 2)):
            for connected in (True, False):
                with self.subTest(status=status, connected=connected):
                    output = FlushedOutput() if connected else None
                    live = {"status": status, "error": "" if status == "live" else "late"}

                    def fake_host(step, *, emit, refresh_s, once, **kwargs):
                        return poll_events(
                            step=step, probe=lambda: (live, None), refresh_s=refresh_s, once=once, emit=emit,
                        )

                    with (
                        patch("cli.automa_cli.plugins.stream.commands.stream_host", side_effect=fake_host),
                        patch("cli.automa_cli.plugins.stream.polling.time.sleep") as sleep,
                    ):
                        result = stream_host_step(
                            "plan", host_name="fixture", refresh_s=1, once=True, no_clear=False,
                            json_output=True, output=output,
                        )
                    self.assertEqual(result.exit_code, code)
                    line = json.dumps(live, sort_keys=True)
                    self.assertEqual(result.message, "" if connected else line)
                    if connected:
                        self.assertEqual(output.frames, [line + "\n"])
                        self.assertNotIn(line, result.message)
                    sleep.assert_not_called()

    def test_decision_stream_flushes_errors_and_recovers_with_its_view(self):
        for json_output in (False, True):
            with self.subTest(json=json_output):
                output = FlushedOutput()
                feed = Mock(url="http://view/", error=None)
                feed.read_observation.return_value = None, None
                feed.state.return_value = ("http://view/", None, "/decision", None)
                with patch("cli.automa_cli.plugins.stream.polling.time.sleep", side_effect=[None, KeyboardInterrupt]):
                    result, view = _stream_plan([ConnectionError("offline"), PUBLICATION], feed=feed,
                                                json_output=json_output, output=output)
                self.assertEqual((result.exit_code, result.message), (130, ""))
                self.assertEqual(len(output.frames), 2)
                if json_output:
                    error, live = map(json.loads, output.getvalue().splitlines())
                    self.assertEqual((error["status"], error["error_code"], error["error"]),
                                     ("error", "runtime_decision_unavailable", OFFLINE))
                    self.assertNotIn("message", error)
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

    def test_decision_once_reports_a_stale_or_raised_frame_and_keeps_its_records(self):
        stale = {**PUBLICATION, "health": "stale", "result_age_ms": 1500, "stale_after_ms": 1000}
        raised = {**PUBLICATION, "report": {
            **PUBLICATION["report"], "status": "error", "error": "RuntimeError: boom",
            "cycle": {**PUBLICATION["report"]["cycle"], "plan": None, "action": None},
        }}
        for publication, status, reason in (
            (stale, "stale", "The latest frame is 1500 ms old, past its 1000 ms ceiling."),
            (raised, "error", "The cycle for frame next raised before plan recorded: RuntimeError: boom"),
        ):
            with self.subTest(status=status):
                output = FlushedOutput()
                result, _view = _stream_plan([publication], once=True, json_output=False, output=output)
                self.assertEqual(result.exit_code, 2)
                screen = output.getvalue()
                self.assertTrue(screen.startswith(reason + "\nPlan stream: fixture frame=next "), screen)
                output = FlushedOutput()
                _stream_plan([publication], once=True, json_output=True, output=output)
                live = json.loads(output.getvalue())
                self.assertEqual((live["status"], live["error"], live["frame_id"]), (status, reason, "next"))

    def test_memory_names_a_raised_cycle_instead_of_a_missing_step(self):
        probe = memory._probe_memory(
            host_name="fixture", base_url="http://host.invalid", fetch_error=None,
            publication=_frame_publication(status="error", error="RuntimeError: boom"),
        )
        self.assertEqual((probe["status"], probe["error"]), (
            "error", "The cycle for frame next raised before memory recorded: RuntimeError: boom",
        ))
        absent = memory._probe_memory(
            host_name="fixture", base_url="http://host.invalid", fetch_error=None,
            publication=_frame_publication(),
        )
        self.assertEqual(absent["status"], "absent")

    def test_perception_reports_a_stale_frame_with_its_fields(self):
        probe = perception._probe_perception(
            host_name="fixture", base_url="http://host.invalid", fetch_error=None,
            publication=_frame_publication(health="stale", perception={"signals": [], "things": []}),
        )
        self.assertEqual((probe["status"], probe["frame_id"], probe["signal_count"]), ("stale", "next", 0))
        self.assertEqual(probe["error"], "The latest frame is 1500 ms old, past its 1000 ms ceiling.")

    def test_a_plugin_error_stays_inside_its_detail_field(self):
        text = PerceptionText.from_dict({"plugin_runs": [
            {"plugin_id": "lens", "status": "error", "error": "bad frame\nretry later"},
        ]}).text
        self.assertIn("plugin_status id=lens status=error detail=bad_frame_retry_later", text.splitlines())

    def test_decision_once_writes_one_frame_without_a_view(self):
        for json_output in (False, True):
            for fetch, code in ((ConnectionError("offline"), 2), (PUBLICATION, 0)):
                with self.subTest(json=json_output, live=not code):
                    output = FlushedOutput()
                    with patch("cli.automa_cli.plugins.stream.polling.time.sleep") as sleep:
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

    def test_preflight_prints_one_diagnostic(self):
        with patch.object(perception, "runtime_base_url", side_effect=RuntimeHostError("missing host")):
            text = stream_host_step("perception", host_name="missing", once=True, output=io.StringIO())
            self.assertEqual((text.exit_code, text.message), (2, "missing host"))
            self.assertEqual(text.message.count("missing host"), 1)

            output = FlushedOutput()
            rendered = stream_host_step(
                "perception", host_name="missing", once=True, json_output=True, output=output,
            )
        self.assertEqual((rendered.exit_code, rendered.message), (2, ""))
        self.assertEqual(output.getvalue().count("\n"), 1)
        probe = json.loads(output.getvalue())
        self.assertEqual((probe["status"], probe["error"], probe["schema"]),
                         ("unavailable", "missing host", "host_perception_live"))

    def test_json_and_one_shot_do_not_open_a_view(self):
        for step in ("perception", "memory", "plan"):
            for once, json_output in ((False, True), (True, False), (True, True)):
                with self.subTest(step=step, once=once, json=json_output):
                    with (
                        _offline(step),
                        patch.object(session, "ViewFeed") as view,
                        patch("cli.automa_cli.plugins.stream.polling.time.sleep", side_effect=KeyboardInterrupt),
                    ):
                        stream_host_step(
                            step, host_name="fixture", once=once, json_output=json_output, output=io.StringIO(),
                        )
                    view.assert_not_called()

    def test_memory_text_and_json_read_the_same_completed_report(self):
        report = NORMALIZED["report"].to_dict()
        report["cycle"]["memory"] = {"schema": "memory_report", "plugins": [], "evidence_publisher": None}
        report["values"].update(source_id="fixture:local", stale_after_ms=1000)
        publication = {"schema": "host_frame_publication", "health": "healthy", "report": report}
        with (
            patch.object(memory, "runtime_base_url", return_value="http://host.invalid"),
            patch.object(memory, "read_publication", return_value=(publication, None)) as read,
        ):
            text = stream_host_step("memory", host_name="fixture", once=True, output=io.StringIO())
            self.assertEqual(text.exit_code, 0)
            read.assert_called_once()
            output = io.StringIO()
            rendered = stream_host_step("memory", host_name="fixture", once=True, json_output=True, output=output)
        self.assertEqual(rendered.exit_code, 0)
        self.assertEqual(read.call_count, 2)
        self.assertEqual(json.loads(output.getvalue())["record"], report["cycle"]["memory"])

    def test_perception_json_still_reads_the_publication(self):
        with (
            patch.object(perception, "runtime_base_url", return_value="http://host.invalid"),
            patch.object(perception, "read_publication", return_value=({"health": "absent"}, None)) as read,
            patch.object(session, "ViewFeed") as view,
        ):
            result = stream_host_step(
                "perception", host_name="fixture", once=True, json_output=True, output=io.StringIO(),
            )
        self.assertEqual(result.exit_code, 2)
        read.assert_called_once()
        view.assert_not_called()

    def test_view_closes_on_completion_interruption_and_failure(self):
        for outcome in ("complete", "interrupt", "fail"):
            with self.subTest(outcome=outcome):
                feed = Mock()
                feed.state.return_value = ("http://view/", None, None, None)
                feed.read_observation.return_value = ({"health": "absent"}, None)

                def emit(event):
                    if outcome == "interrupt":
                        raise KeyboardInterrupt
                    if outcome == "fail":
                        raise RuntimeError("boom")

                with (
                    patch.object(perception, "runtime_base_url", return_value="http://host.invalid"),
                    patch.object(session, "ViewFeed", return_value=feed),
                ):
                    if outcome == "fail":
                        with self.assertRaises(RuntimeError):
                            perception.stream_perception(
                                host_name="fixture", refresh_s=0.5, once=True, timeout_s=1,
                                serve_view=True, emit=emit,
                            )
                    else:
                        result = perception.stream_perception(
                            host_name="fixture", refresh_s=0.5, once=outcome == "complete", timeout_s=1,
                            serve_view=True, emit=emit,
                        )
                        self.assertEqual(result.exit_code, 2 if outcome == "complete" else 130)
                feed.stop.assert_called_once_with()

    def test_render_uses_supplied_view_addresses(self):
        event = StreamEvent(
            step="plan",
            host_name="fixture",
            live={"status": "error", "error": "offline"},
            view=ViewState(url="http://view/", error=None, decision_page="/decision", decision_error="late"),
        )
        text = render_stream(event, json_output=False)
        self.assertIn("offline", text)
        self.assertIn("perception view: http://view/perception", text)
        self.assertIn("action view: http://view/decision  (latest unavailable: late)", text)
        self.assertEqual(render_stream(event, json_output=True), json.dumps(event.live, sort_keys=True))
        self.assertEqual(render_stream(replace_view(event), json_output=False), "offline")

    def test_perception_screen_formats_the_summary_the_stream_read(self):
        evidence = {
            "schema": "perception_text", "plugin_id": "lightweight_observer",
            "signals": [{"signal_id": "clear", "value": True}, {"signal_id": "distance", "value": 2}],
            "things": [{"thing_id": "ahead", "kind": "obstacle", "label": "ahead"}],
        }
        summary = perception.perception_publication({
            "health": "healthy", "result_age_ms": 40,
            "report": {
                "frame_id": "f-12", "timestamp_ms": 1000,
                "cycle": {"perception": evidence},
                "application": {"mode": "observe_only", "steering": 0.1, "throttle": 0.2, "reason": "hold"},
                "values": {"timing": {"duration_ms": 33}, "activation": {
                    "steps": {"perception": {"metadata": {"preset": "lightweight_observer"}}},
                }},
            },
        })
        self.assertEqual((summary.frame_id, summary.signal_count, summary.thing_count, summary.text),
                         ("f-12", 2, 1, PerceptionText.from_dict(evidence).text))
        self.assertEqual(perception.perception_publication(None), PerceptionPublication())
        text = render_stream(StreamEvent("perception", {"status": "live"}, source=summary), json_output=False)
        self.assertIn("presets: perception=lightweight_observer  mode: observe_only", text)
        self.assertIn("control: steering=0.1  throttle=0.2  reason=hold", text)
        self.assertIn("cadence: cycle_ms=33  age_ms=40", text)
        self.assertIn("latest: frame=f-12  captured_at_ms=1000  signals=2  things=1", text)
        unread = render_stream(StreamEvent("perception", {"status": "error"}), json_output=False)
        self.assertIn("latest: frame=none  captured_at_ms=unknown  signals=unknown  things=unknown", unread)
        self.assertIn("publication: unavailable", unread)
        self.assertTrue(unread.endswith("(no latest perception yet)"))

    def test_stream_operations_do_not_import_presentation(self):
        forbidden = {"commands", "render"}
        violations = [
            f"{path.name} imports {name}"
            for path in (STREAM / name for name in OPERATIONS)
            for name in _referenced_modules(path)
            if name == "commands" or name == "render" or name.endswith(".commands") or name.endswith(".render")
            or name in forbidden
        ]
        self.assertEqual(violations, [])
        renderer = _referenced_modules(STREAM / "render.py")
        leaked = sorted(
            name for name in renderer
            if name in {"service", "session", "perception", "memory", "decision", "polling", "commands", "view_feed"}
            or name.endswith(".view_feed") or name.endswith(".session") or name.endswith(".service")
        )
        self.assertEqual(leaked, [])


def replace_view(event: StreamEvent) -> StreamEvent:
    return StreamEvent(step=event.step, live=event.live, source=event.source, host_name=event.host_name)


@contextmanager
def _offline(step: str):
    if step == "perception":
        with (
            patch.object(perception, "runtime_base_url", return_value="http://host.invalid"),
            patch.object(perception, "read_publication", return_value=({"health": "absent"}, None)),
        ):
            yield
        return
    if step == "memory":
        with (
            patch.object(memory, "runtime_base_url", return_value="http://host.invalid"),
            patch.object(memory, "read_publication", return_value=({"memory": {}}, None)),
        ):
            yield
        return
    with (
        patch.object(decision, "require_valid_activations"),
        patch.object(decision, "runtime_base_url", return_value="http://host.invalid"),
        patch.object(decision, "fetch_frame_publication", return_value={}),
        patch.object(decision, "accept_decision_publication", return_value=NORMALIZED),
    ):
        yield
