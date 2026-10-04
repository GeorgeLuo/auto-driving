"""Packaged plugins are listed by the ID each one declares."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from unittest import mock

from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.plugins import DuplicatePluginIdError
from cli.automa_cli import app
from implementations.decision_cycle import catalog


class DeclaredIdTests(unittest.TestCase):
    def tearDown(self) -> None:
        catalog._declared_entries.cache_clear()

    def _clashing_actions(self) -> contextlib.AbstractContextManager:
        # A second entry whose plugin declares an ID already taken in the step.
        entries = catalog.STEP_PLUGINS["action"]
        catalog._declared_entries.cache_clear()
        return mock.patch.dict(catalog.STEP_PLUGINS, {"action": (*entries, dict(entries[0]))})

    def test_every_step_lists_plugins_by_declared_id(self) -> None:
        for step in catalog.STEPS:
            with self.subTest(step=step):
                plugins = catalog.step_plugins(step)
                self.assertEqual(len(plugins), len(catalog.STEP_PLUGINS[step]))
                self.assertTrue(set(catalog.DEFAULT_STEP_PLUGINS[step]) <= set(plugins))

    def test_two_plugins_declaring_one_id_stop_the_step(self) -> None:
        with self._clashing_actions():
            with self.assertRaisesRegex(DuplicatePluginIdError, "duplicate action plugin id 'hold'"):
                catalog.step_plugins("action")
            self.assertEqual(sorted(catalog.step_plugins("memory")), ["bounded_evidence"])

    def test_cli_reports_the_clash_and_exits_2(self) -> None:
        stderr = io.StringIO()
        with (
            self._clashing_actions(),
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"AUTOMA_RUNTIME_ROOT": tmp}),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(stderr),
        ):
            code = app.main(
                ["vehicles", "update", "action", "--id", "chase-sim-chaser", "--plugin", "hold", "--dry-run"]
            )
        self.assertEqual(code, 2)
        self.assertIn("error: duplicate action plugin id 'hold'", stderr.getvalue())


class PresetActivationTests(unittest.TestCase):
    def test_perception_and_memory_are_the_steps_with_presets(self) -> None:
        self.assertEqual(tuple(catalog.STEP_PRESETS), ("perception", "memory"))
        self.assertEqual(set(catalog.DEFAULT_STEP_PRESETS), set(catalog.STEP_PRESETS))
        for step in catalog.STEPS:
            if step not in catalog.STEP_PRESETS:
                with self.subTest(step=step):
                    with self.assertRaisesRegex(ValueError, f"{step} has no presets"):
                        catalog.preset_activation(step, "anything")

    def test_every_preset_selects_cataloged_plugins_and_records_its_name(self) -> None:
        for step, presets in catalog.STEP_PRESETS.items():
            known = catalog.step_plugins(step)
            for name, entry in presets.items():
                with self.subTest(step=step, preset=name):
                    activation = catalog.preset_activation(step, name)
                    self.assertTrue(entry["description"])
                    self.assertEqual(activation.plugins, tuple(entry["plugins"]))
                    self.assertTrue(set(activation.plugins) <= set(known))
                    self.assertEqual(activation.metadata, {"preset": name})
                    for plugin_id, overrides in entry.get("plugin_configs", {}).items():
                        self.assertEqual(
                            {key: activation.plugin_configs[plugin_id][key] for key in overrides},
                            overrides,
                        )

    def test_every_preset_builds_its_steps_runner(self) -> None:
        runners = {"perception": PerceptionRunner, "memory": MemoryRunner}
        for step, presets in catalog.STEP_PRESETS.items():
            for name in presets:
                with self.subTest(step=step, preset=name):
                    runner = runners[step].from_activation(catalog.preset_activation(step, name))
                    self.assertEqual(
                        tuple(runner.plugin_ids), tuple(presets[name]["plugins"])
                    )

    def test_the_default_selection_is_the_default_preset(self) -> None:
        for step, default in catalog.DEFAULT_STEP_PRESETS.items():
            with self.subTest(step=step):
                self.assertEqual(
                    catalog.DEFAULT_STEP_PLUGINS[step],
                    tuple(catalog.STEP_PRESETS[step][default]["plugins"]),
                )
                self.assertEqual(
                    catalog.selection_activation(step).to_payload(),
                    catalog.preset_activation(step, default).to_payload(),
                )

    def test_a_plugin_list_is_labeled_with_the_preset_it_equals_else_custom(self) -> None:
        def label(step: str, plugins: list[str]) -> str:
            return catalog.selection_activation(step, plugins=plugins).metadata["preset"]

        self.assertEqual(label("memory", ["bounded_evidence"]), "recency_ledger")
        self.assertEqual(label("perception", ["frame", "floor_plane"]), "lightweight_observer")
        # Order matters, an empty list is no preset, and a preset with config
        # overrides is not equalled by its plugin list alone.
        self.assertEqual(label("perception", ["floor_plane", "frame"]), catalog.CUSTOM_PRESET)
        self.assertEqual(label("memory", []), catalog.CUSTOM_PRESET)
        self.assertEqual(
            label("perception", ["frame", "floor_plane", "obstruction_tracks"]), catalog.CUSTOM_PRESET
        )

    def test_selection_errors_name_the_step(self) -> None:
        with self.assertRaisesRegex(ValueError, r"unknown memory preset 'nope'; known: .*recency_ledger"):
            catalog.preset_activation("memory", "nope")
        with self.assertRaisesRegex(ValueError, "choose either a memory preset or plugins, not both"):
            catalog.selection_activation("memory", preset="recency_ledger", plugins=[])
        with self.assertRaisesRegex(ValueError, r"unknown perception preset 'nope'"):
            catalog.selection_activation("perception", preset="nope")


if __name__ == "__main__":
    unittest.main()
