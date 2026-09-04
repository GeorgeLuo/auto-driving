from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RECORDER_PATH = (
    ROOT
    / "docs/milestones/008-cli-decision-workbench/evidence"
    / "replay-workbench-acceptance/record_session.py"
)


def _load_recorder():
    name = "replay_workbench_record_session"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, RECORDER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _state(
    *,
    run_id: str,
    phase: str = "running",
    failure: dict | None = None,
    server: str = "workbench-aaa",
    completed: int = 1,
    total: int = 10,
) -> dict:
    payload = {
        "server_identity": server,
        "run_id": run_id,
        "phase": phase,
        "source_identity": "capture:run",
        "active_plugin_ids": ["classical_regions"],
        "run_active_plugin_ids": ["classical_regions"],
        "progress": {"completed": completed, "total": total},
        "perception": {"status": "ok", "plugin_runs": [{"plugin_id": "classical_regions"}]},
        "memory": {"records": [{"id": "r1"}]},
        "controls": {"pace": "realtime", "loop": False},
        "failure": failure,
        "failure_boundary": None if failure is None else failure.get("boundary"),
        "recovery_action": "start",
        "cleanup": {"worker_started": False},
    }
    return payload


class PromptScript:
    def __init__(self, answers: dict[str, str | list[str]]) -> None:
        self.seen: list[str] = []
        self.answers = answers

    def __call__(self, prompt: str) -> str:
        self.seen.append(prompt)
        for key, value in self.answers.items():
            if key not in prompt:
                continue
            if isinstance(value, list):
                if not value:
                    continue
                return value.pop(0)
            return value
        raise AssertionError(f"unexpected prompt: {prompt!r}\nseen={self.seen}")


class FakeWorkbench:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    def start(self) -> None:
        return None

    def wait_until_ready(self, **kwargs):
        return "http://127.0.0.1:9/", {
            "available": True,
            "url": "http://127.0.0.1:9/",
            "server_identity": "workbench-aaa",
            "phase": "running",
            "run_id": "run-1",
            "observation_only": True,
            "persistent_across_terminal_state": True,
        }

    def transcript(self) -> str:
        return "workbench: http://127.0.0.1:9/\n"

    def stop(self) -> None:
        return None


def _answers(**overrides: str | list[str]) -> dict[str, str | list[str]]:
    mapping: dict[str, str | list[str]] = {
        "Press Enter when you have done that on the page. ": "",
        "Press Enter when the failure is visible. ": "",
        "Press Enter when the recovered run has started. ": "",
        "first run id": "run-1",
        "second run id": "run-2",
        "failed run id": "run-3",
        "recovered run id": "run-4",
        "failure boundary shown": "source",
        "failure message shown": "empty directory",
        "next action shown": "start",
        "Path to cropped browser-view.png": "",
        "crop exclude local filesystem paths": "y",
        "[y/n/u]": "y",
        "Notes (optional): ": "",
        "Operator verdict": "accepted",
    }
    mapping.update(overrides)
    return mapping


class RecordSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load_recorder()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.artifact_dir = Path(self.tmpdir.name)
        self.source_dir = self.artifact_dir / "source"
        self.source_dir.mkdir()
        self.screenshot = self.artifact_dir / "crop.png"
        self.screenshot.write_bytes(b"png")

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _run(self, states: list[dict], answers: dict[str, str | list[str]]):
        queue = list(states)

        def fetch_json(url: str, timeout: float = 2.0) -> dict:
            self.assertIn("api/state", url)
            if not queue:
                raise AssertionError("no remaining state snapshots")
            return queue.pop(0)

        reader = PromptScript(answers)
        output = io.StringIO()
        payload = self.mod.run_session(
            source_dir=self.source_dir,
            plugin_dir=None,
            plugin=None,
            operator="tester",
            browser_name="Chrome",
            browser_version="1",
            screenshot=self.screenshot,
            pace="realtime",
            max_frames=1024,
            reader=reader,
            output=output,
            fetch_json=fetch_json,
            launcher=FakeWorkbench,
            artifact_dir=self.artifact_dir,
        )
        return payload, reader.seen, output.getvalue(), queue

    def test_prompts_ask_run_ids_and_inspect_screenshot_before_verdict(self) -> None:
        states = [
            _state(run_id="run-1"),
            _state(run_id="run-1", completed=3),
            _state(run_id="run-1", phase="paused"),
            _state(run_id="run-1"),
            _state(run_id="run-2"),
            _state(
                run_id="run-3",
                phase="failed",
                failure={"boundary": "source", "message": "empty directory"},
            ),
            _state(run_id="run-4"),
            _state(run_id="run-4", phase="completed", completed=10),
        ]
        payload, seen, _out, remaining = self._run(
            states, _answers(**{"Path to cropped browser-view.png": str(self.screenshot)})
        )
        self.assertEqual(remaining, [])
        joined = "\n".join(seen)
        inspect_shot = next(
            i for i, prompt in enumerate(seen) if "Path to cropped browser-view.png" in prompt
        )
        first_id = next(i for i, prompt in enumerate(seen) if "first run id" in prompt)
        second_id = next(i for i, prompt in enumerate(seen) if "second run id" in prompt)
        failed_id = next(i for i, prompt in enumerate(seen) if "failed run id" in prompt)
        recovered_id = next(
            i for i, prompt in enumerate(seen) if "recovered run id" in prompt
        )
        verdict = next(i for i, prompt in enumerate(seen) if "Operator verdict" in prompt)
        self.assertIn("ask is now, not at the end", joined)
        self.assertLess(first_id, inspect_shot)
        self.assertLess(inspect_shot, second_id)
        self.assertLess(second_id, failed_id)
        self.assertLess(failed_id, recovered_id)
        self.assertLess(recovered_id, verdict)
        self.assertTrue(payload["screenshot"]["captured"])
        self.assertEqual(payload["screenshot"]["asked_during"], "inspect_replay")
        self.assertEqual(payload["identities"]["second_run_id"], "run-2")
        self.assertEqual(payload["status"], "accepted")

    def test_same_second_run_id_cannot_accept(self) -> None:
        states = [
            _state(run_id="run-1"),
            _state(run_id="run-1"),
            _state(run_id="run-1"),
            _state(run_id="run-1"),
            _state(run_id="run-1"),
            _state(
                run_id="run-3",
                failure={"boundary": "source", "message": "empty directory"},
            ),
            _state(run_id="run-4"),
            _state(run_id="run-4", phase="completed"),
        ]
        payload, seen, output, _remaining = self._run(
            states,
            _answers(
                **{
                    "second run id": "run-1",
                    "Path to cropped browser-view.png": str(self.screenshot),
                }
            ),
        )
        self.assertNotEqual(payload["status"], "accepted")
        self.assertEqual(payload["status"], "incomplete")
        self.assertIn("second run id is not distinct from first", payload["incomplete_reason"])
        self.assertIn("second_run snapshot still shows the first run id", payload["incomplete_reason"])
        self.assertTrue(any("second run id" in prompt for prompt in seen))
        self.assertIn("WARNING: second run id matches the first run id.", output)

    def test_source_failure_without_failure_payload_cannot_accept(self) -> None:
        states = [
            _state(run_id="run-1"),
            _state(run_id="run-1"),
            _state(run_id="run-1"),
            _state(run_id="run-1"),
            _state(run_id="run-2"),
            _state(run_id="run-1", phase="completed", completed=10),
            _state(run_id="run-4"),
            _state(run_id="run-4", phase="completed"),
        ]
        payload, _seen, output, _remaining = self._run(
            states,
            _answers(**{"Path to cropped browser-view.png": str(self.screenshot)}),
        )
        self.assertEqual(payload["status"], "incomplete")
        self.assertIn("source_failure snapshot has no failure payload", payload["incomplete_reason"])
        self.assertIn("WARNING: snapshot has no failure payload", output)

    def test_identity_gaps_for_prior_packet_shape(self) -> None:
        same = {
            "server_identity": "workbench-aaa",
            "run_id": "run-same",
            "phase": "completed",
            "failure": None,
            "progress": {"completed": 845, "total": 845},
        }
        steps = [
            {"id": "page_open", "status": "observed_pass", "machine": same},
            {"id": "second_run", "status": "observed_pass", "machine": same},
            {"id": "source_failure", "status": "observed_pass", "machine": same},
        ]
        gaps = self.mod.identity_gaps(
            {
                "server_identity": "workbench-aaa",
                "first_run_id": None,
                "second_run_id": None,
                "failed_run_id": None,
                "recovered_run_id": None,
            },
            steps,
            {"captured": False, "path_redaction": None, "asked_during": "inspect_replay"},
        )
        status, reason = self.mod.finalize_status(
            verdict="accepted",
            steps=steps,
            observation_only={
                "vehicle": {"occurred": False},
                "worker": {"occurred": False},
            },
            gaps=gaps,
        )
        self.assertEqual(status, "incomplete")
        self.assertIn("missing second run id", reason)
        self.assertIn("source_failure snapshot has no failure payload", reason)
        self.assertIn("inspect screenshot was not captured", reason)


if __name__ == "__main__":
    unittest.main()
