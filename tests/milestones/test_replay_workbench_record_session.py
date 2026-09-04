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
    plugins: list[str] | None = None,
    run_plugins: list[str] | None = None,
    frame_id: str = "frame-1",
    position: int = 1,
) -> dict:
    active = ["classical_regions"] if plugins is None else plugins
    running = active if run_plugins is None else run_plugins
    payload = {
        "server_identity": server,
        "run_id": run_id,
        "phase": phase,
        "source_identity": "capture:run",
        "active_plugin_ids": active,
        "run_active_plugin_ids": running,
        "progress": {"completed": completed, "total": total},
        "current_frame": {"frame_id": frame_id, "position": position},
        "perception": {
            "status": "ok",
            "plugin_runs": [{"plugin_id": item} for item in running],
        },
        "memory": {"records": [{"id": "r1"}]},
        "controls": {"pace": "realtime", "loop": False},
        "failure": failure,
        "failure_boundary": None if failure is None else failure.get("boundary"),
        "recovery_action": "start",
        "cleanup": {"worker_started": False},
    }
    return payload


def _toggle_states(run_id: str) -> list[dict]:
    held = "held-frame"
    return [
        _state(
            run_id=run_id,
            phase="paused",
            plugins=["floor_continuity"],
            run_plugins=["floor_continuity"],
            frame_id=held,
            position=3,
            completed=3,
        ),
        _state(
            run_id=run_id,
            phase="paused",
            plugins=[],
            run_plugins=[],
            frame_id=held,
            position=3,
            completed=3,
        ),
        _state(
            run_id=run_id,
            phase="paused",
            plugins=["classical_regions"],
            run_plugins=["classical_regions"],
            frame_id=held,
            position=3,
            completed=3,
        ),
        _state(
            run_id=run_id,
            plugins=[],
            run_plugins=["classical_regions"],
            frame_id="run-frame-a",
            position=4,
            completed=4,
        ),
        _state(
            run_id=run_id,
            plugins=[],
            run_plugins=[],
            frame_id="run-frame-b",
            position=5,
            completed=5,
        ),
    ]


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
        "Press Enter after pausing and selecting another ready plugin set. ": "",
        "Press Enter after selecting empty raw-capture. ": "",
        "Press Enter after restoring a non-empty ready set. ": "",
        "Press Enter immediately after the running toggle": "",
        "Press Enter after the next processed frame shows the new set. ": "",
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

    def _run(
        self,
        states: list[dict],
        answers: dict[str, str | list[str]],
        git_identity: dict[str, str] | None = None,
    ):
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
            git_identity=git_identity
            or {
                "commit": "abc",
                "branch": "m008/replay-workbench-acceptance",
                "worktree_state": "clean",
            },
        )
        return payload, reader.seen, output.getvalue(), queue

    def test_prompts_ask_run_ids_and_inspect_screenshot_before_verdict(self) -> None:
        states = [
            _state(run_id="run-1"),
            _state(run_id="run-1", completed=3),
            *_toggle_states("run-1"),
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
        paused = next(item for item in payload["steps"] if item["id"] == "paused_toggle")
        running = next(item for item in payload["steps"] if item["id"] == "running_toggle")
        self.assertEqual(len(paused["transitions"]), 3)
        self.assertEqual(len(running["transitions"]), 2)
        self.assertEqual(
            paused["transitions"][1]["machine"]["active_plugin_ids"],
            [],
        )

    def test_same_second_run_id_cannot_accept(self) -> None:
        states = [
            _state(run_id="run-1"),
            _state(run_id="run-1"),
            *_toggle_states("run-1"),
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
            *_toggle_states("run-1"),
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
        self.assertIn("paused_toggle missing per-selection snapshots", gaps)

    def test_single_paused_snapshot_cannot_accept(self) -> None:
        states = [
            _state(run_id="run-1"),
            _state(run_id="run-1"),
            _state(run_id="run-1", phase="paused"),
            _state(run_id="run-1", phase="paused"),
            _state(run_id="run-1", phase="paused"),
            _state(run_id="run-1"),
            _state(run_id="run-1"),
            _state(run_id="run-2"),
            _state(
                run_id="run-3",
                phase="failed",
                failure={"boundary": "source", "message": "empty directory"},
            ),
            _state(run_id="run-4"),
            _state(run_id="run-4", phase="completed"),
        ]
        payload, seen, _out, _remaining = self._run(
            states,
            _answers(**{"Path to cropped browser-view.png": str(self.screenshot)}),
        )
        self.assertEqual(payload["status"], "incomplete")
        self.assertIn("paused_toggle snapshots have no empty raw-capture selection", payload["incomplete_reason"])
        self.assertTrue(
            any("selecting another ready plugin set" in prompt for prompt in seen)
        )

    def test_dirty_worktree_cannot_accept(self) -> None:
        states = [
            _state(run_id="run-1"),
            _state(run_id="run-1"),
            *_toggle_states("run-1"),
            _state(run_id="run-2"),
            _state(
                run_id="run-3",
                phase="failed",
                failure={"boundary": "source", "message": "empty directory"},
            ),
            _state(run_id="run-4"),
            _state(run_id="run-4", phase="completed"),
        ]
        payload, _seen, _out, _remaining = self._run(
            states,
            _answers(**{"Path to cropped browser-view.png": str(self.screenshot)}),
            git_identity={
                "commit": "abc",
                "branch": "m008/replay-workbench-acceptance",
                "worktree_state": "dirty",
            },
        )
        self.assertEqual(payload["status"], "incomplete")
        self.assertIn("session did not start from a clean checkout", payload["incomplete_reason"])

    def test_readme_worktree_note_matches_receipt(self) -> None:
        readme = self.artifact_dir / "README.md"
        original_readme = self.mod.README
        self.mod.README = readme
        payload = {
            "status": "accepted",
            "verdict": "accepted",
            "operator": "tester",
            "repository": {"worktree_state": "clean"},
            "steps": [],
            "findings": [],
        }
        try:
            self.mod._write_readme(payload)
            self.assertNotIn("Worktree `dirty` at record time", readme.read_text())

            payload["repository"]["worktree_state"] = "dirty"
            self.mod._write_readme(payload)
            self.assertIn("Worktree `dirty` at record time", readme.read_text())
        finally:
            self.mod.README = original_readme


if __name__ == "__main__":
    unittest.main()
