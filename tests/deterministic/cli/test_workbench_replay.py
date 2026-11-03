from __future__ import annotations

import json
import unittest
from unittest import mock
from urllib.request import urlopen

from cli.automa_cli.hosts.views.runtime_view import runtime_view_server
from cli.automa_cli.workbench.models import WorkbenchEvent
from cli.automa_cli.shared.text import format_browser_result
from cli.automa_cli.workbench.render import render_workbench_result
from cli.automa_cli.workbench.render import format_workbench_status
from cli.automa_cli.workbench.replay import run_workbench_replay
from tests.shared.cli.invoke import invoke_automa
from tests.shared.cli.workbench_fixtures import image_source


class WorkbenchTests(unittest.TestCase):
    def test_status_renderer_formats_a_supplied_replay(self) -> None:
        text = format_workbench_status(
            {
                "phase": "completed",
                "sequence_id": "workbench.image_replay.v1",
                "run_id": None,
                "source": {"source_path": "/tmp/frames"},
                "machine_detail": {"pipeline": {"perception_preset": "lightweight_observer"}},
                "active_perception_plugin_ids": ["frame"],
                "progress": {"completed": 1, "total": 1},
                "failure": {"message": "stopped"},
                "recovery_action": "retry",
                "cleanup": {
                    "perception": "reset",
                    "memory": "reset",
                    "proposal": "reset",
                    "plan": "reset",
                    "source_read_only": True,
                    "movement_control": False,
                },
            },
            server_url="http://127.0.0.1:9/workbench/",
            serving=True,
        )

        self.assertEqual(text, "\n".join([
            "automa decision playback workbench",
            "phase: completed",
            "sequence: workbench.image_replay.v1",
            "run_id: (none)",
            "source: /tmp/frames",
            "perception: lightweight_observer (frame)",
            "memory: none",
            "proposal: none",
            "plan: none",
            "progress: 1/1",
            "workbench: http://127.0.0.1:9/workbench/",
            "server: persistent loopback mode; press Ctrl-C to stop",
            "failure: stopped",
            "recovery: retry",
            "cleanup: perception=reset; memory=reset; proposal=reset; plan=reset; source_read_only=True; movement_control=False",
        ]))

    def test_open_replay_opens_the_viewers_workbench_with_its_source_loaded(self) -> None:
        opened = []
        shown: list[str] = []

        def emit(event: WorkbenchEvent) -> None:
            if event.state is not None:
                shown.append(format_workbench_status(
                    event.state, server_url=event.server_url, serving=event.serving,
                ))
            if event.browser:
                shown.append(format_browser_result(event.browser))

        with image_source(1) as root:
            def open_view(url: str) -> str:
                opened.append(url)
                self.assertTrue(url.endswith("/workbench/"))
                with urlopen(url, timeout=2) as response:
                    self.assertIn('<a class="automa-home" href="/"', response.read().decode())
                with urlopen(url + "api/state", timeout=2) as response:
                    state = json.load(response)
                self.assertEqual(state["source"]["source_path"], str(root.resolve()))
                with urlopen(url.removesuffix("workbench/"), timeout=2) as response:
                    self.assertIn('<a class="nav" href="/workbench/">Workbench</a>', response.read().decode())
                raise KeyboardInterrupt

            with mock.patch("cli.automa_cli.workbench.replay.launch_browser", side_effect=open_view) as launch:
                result = run_workbench_replay(
                    root, cadence_ms=0, open_browser=True, emit=emit, view_server=runtime_view_server,
                )

        launch.assert_called_once_with(opened[0])
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(render_workbench_result(result), "viewer stopped")
        self.assertIn(f"workbench: {opened[0]}", "\n".join(shown))

    def test_cli_replay_human_output_names_recovery_and_cleanup(self) -> None:
        with image_source(1) as root:
            result = invoke_automa(
                "workbench",
                str(root),
                "--cadence-ms",
                "0",
            )

        self.assertIn("phase: completed", result.stdout)
        self.assertIn("recovery:", result.stdout)
        self.assertIn("cleanup: perception=reset; memory=reset;", result.stdout)
        self.assertIn("source_read_only=True", result.stdout)
        # Each step's selection, as the inspect commands print it.
        self.assertIn("perception: lightweight_observer (frame, floor_plane)", result.stdout)
        self.assertIn("memory: recency_ledger (bounded_evidence)", result.stdout)

    def test_cli_replay_preset_and_plugin_flags_select_equivalent_recipes_for_each_step(self) -> None:
        cases = (
            ("perception", "lightweight_observer", ("frame", "floor_plane")),
            ("memory", "recency_ledger", ("bounded_evidence",)),
            ("proposal", "obstruction_avoidance", ("avoid_recent_obstruction",)),
        )

        def selections(output):
            return {step: next(line for line in output.splitlines() if line.startswith(f"{step}: "))
                    for step, _, _ in cases}

        with image_source(1) as root:
            command = ("workbench", str(root), "--cadence-ms", "0")
            implicit = selections(invoke_automa(*command).stdout)
            for step, preset, plugins in cases:
                with self.subTest(step=step):
                    named = invoke_automa(*command, f"--{step}-preset", preset)
                    flags = tuple(arg for plugin in plugins for arg in (f"--{step}-plugin", plugin))
                    selected = invoke_automa(*command, *flags)
                    # Both explicit inputs match the default, including the
                    # other steps that this request did not select.
                    self.assertEqual(selections(named.stdout), implicit)
                    self.assertEqual(selections(selected.stdout), implicit)
            # A non-default preset reaches its step alone; perception is the
            # step with more than one preset.
            chosen = selections(invoke_automa(*command, "--perception-preset", "sim_debug").stdout)
            self.assertEqual(chosen, {**implicit, "perception": "perception: sim_debug (frame, sim_color_targets)"})

    def test_cli_replay_rejects_mixed_removed_or_unknown_selections(self) -> None:
        with image_source(1) as root:
            for args, message in (
                (("--perception-preset", "sim_debug", "--perception-plugin", "frame"),
                 "not allowed with argument --perception-preset"),
                (("--memory-preset", "recency_ledger", "--memory-plugin", "bounded_evidence"),
                 "not allowed with argument --memory-preset"),
                (("--proposal-preset", "obstruction_avoidance", "--proposal-plugin", "avoid_recent_obstruction"),
                 "not allowed with argument --proposal-preset"),
                (("--proposal-preset", "missing"), "invalid choice: 'missing'"),
                (("--plugin", "frame"), "unrecognized arguments: --plugin"),
                (("--memory-plugin", "missing"), "unknown memory plugin(s) missing"),
            ):
                result = invoke_automa(
                    "workbench", str(root), *args, check=False
                )
                self.assertEqual(result.returncode, 2, args)
                self.assertIn(message, result.stdout + result.stderr, args)


if __name__ == "__main__":
    unittest.main()
