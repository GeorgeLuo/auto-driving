"""Both vehicles load and apply selections through one host.

The fixture names the code source and the transport. Chase passes a car into
``create_host``; the onboard host does not, because that process is the
drivetrain. One substituted ``load_runner`` covers both.
"""

from __future__ import annotations

import inspect
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from autonomy.decision_cycle.activation import (
    step_activation,
    write_step_activation,
)
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.plugin_loader import INSTALLED_PACKAGE, CodeSource, load_runner
from cli.automa_cli.hosts.bundles import sync_controller_bundle
from cli.automa_cli.releases.paths import controller_bundle_paths
from implementations.decision_cycle.catalog import packaged_activation
from implementations.runtime.chase_sim import create_host as create_chase_host
from implementations.runtime.picar import create_host as create_picar_host

LAZY_MODULE = "implementations.decision_cycle.proposal.lazy_only"
VEHICLES = ("chase", "picar")


class _Car:
    """Stand-in for the simulator car. Manual runs do not call it."""


def _frame(host, index: int):
    return host.run(DecisionFrameContext(f"frame-{index}", index, 1_000 + index))


def _open(vehicle: str, activations: dict, *, source, watches=()):
    steps = decision_steps(activations, source=source)
    host = (
        create_chase_host(_Car(), steps=steps)
        if vehicle == "chase"
        else create_picar_host(steps=steps)
    )
    for step, path, loaded in watches:
        host.watch_selection(step, path, loaded)
    return host


def _publish(host, sink: dict) -> dict:
    """Copy the host identity the way each vehicle's frame loop does."""

    applied = host.applied_decision()
    sink["generation_id"] = applied["generation_id"]
    sink["steps"] = applied["steps"]
    return applied


class SharedSelectionTests(unittest.TestCase):
    def test_installed_source_rebuild_uses_its_release_and_no_staging_path(self) -> None:
        activation = replace(packaged_activation("proposal"), metadata={
            "controller_bundle": {"root_dir": "/missing/staging-machine/bundle", "release": {"tree_sha256": "old"}},
        })
        release = {"tree_sha256": "installed", "created_at_ms": 123}
        with patch("autonomy.runtime.recording.INSTALLED_RELEASE", release):
            host = _open("picar", {"proposal": activation}, source=INSTALLED_PACKAGE)
            before = host.step("proposal")
            self.assertEqual(before.activation.metadata["controller_bundle"], {"release": release})
            self.assertTrue(host.disarm({"proposal": list(activation.plugins)})["ok"])
            _frame(host, 0)
            after = host.step("proposal")
            self.assertIsNot(after, before)
            self.assertEqual(after.plugin_ids, ())
            self.assertEqual(after.activation.metadata["controller_bundle"], {"release": release})

    def test_substituted_loader_is_shared(self) -> None:
        seen: list[tuple[str, CodeSource | None]] = []
        real = load_runner

        def substitute(activation, *, source=None):
            seen.append((activation.step, source))
            return real(activation, source=source)

        proposal = packaged_activation("proposal")
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("autonomy.decision_cycle.steps.load_runner", side_effect=substitute),
        ):
            bundle = controller_bundle_paths(Path(tmp) / "vehicle")
            sync_controller_bundle(bundle, emit=None)
            sources = {
                "chase": CodeSource(bundle_root=Path(bundle["root_dir"])),
                "picar": INSTALLED_PACKAGE,
            }
            for vehicle in VEHICLES:
                with self.subTest(vehicle=vehicle):
                    seen.clear()
                    host = _open(vehicle, {"proposal": proposal}, source=sources[vehicle])
                    self.assertEqual(
                        [source for _step, source in seen],
                        [sources[vehicle]] * len(seen),
                    )
                    self.assertIn("proposal", [step for step, _source in seen])
                    self.assertEqual(
                        list(host.step("proposal").plugin_ids),
                        ["avoid_recent_obstruction"],
                    )

    def test_code_source_does_not_leak_into_host_imports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = controller_bundle_paths(Path(tmp) / "vehicle")
            sync_controller_bundle(bundle, emit=None)
            bundle_root = Path(bundle["root_dir"])
            staged = bundle_root / "implementations/decision_cycle/proposal/lazy_only.py"
            staged.write_text(
                "class LazyOnly:\n"
                "    plugin_id = 'lazy_only'\n"
                "    def propose(self, context):\n"
                "        return None\n",
                encoding="utf-8",
            )
            host_catalog = sys.modules["implementations.decision_cycle.catalog"]
            host_activation = sys.modules["autonomy.decision_cycle.activation"]
            lazy = step_activation(
                "proposal",
                ["lazy_only"],
                {"lazy_only": f"{LAZY_MODULE}:LazyOnly"},
            )
            packaged = packaged_activation("proposal")

            chase = _open(
                "chase",
                {"proposal": lazy},
                source=CodeSource(bundle_root=bundle_root),
            )
            loaded = chase.step("proposal")
            self.assertNotIn(LAZY_MODULE, sys.modules)
            self.assertIs(sys.modules["implementations.decision_cycle.catalog"], host_catalog)
            self.assertIs(sys.modules["autonomy.decision_cycle.activation"], host_activation)
            self.assertEqual(loaded.plugins["lazy_only"].__class__.__module__, LAZY_MODULE)
            self.assertEqual(Path(loaded.import_context.bundle_root), bundle_root)
            try:
                _frame(chase, 0)
            except (ImportError, ModuleNotFoundError):
                raise
            except Exception:
                pass
            self.assertNotIn(LAZY_MODULE, sys.modules)
            self.assertIs(sys.modules["autonomy.decision_cycle.activation"], host_activation)

            picar = _open("picar", {"proposal": packaged}, source=INSTALLED_PACKAGE)
            installed = Path(
                inspect.getfile(type(picar.step("proposal").plugins["avoid_recent_obstruction"]))
            )
            self.assertFalse(str(installed.resolve()).startswith(str(bundle_root.resolve())))
            self.assertNotIn(LAZY_MODULE, sys.modules)

    def _watched_proposal(self, vehicle: str):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        path = Path(root.name) / "proposal" / "active.json"
        proposal = packaged_activation("proposal")
        write_step_activation(path, proposal)
        host = _open(
            vehicle,
            {"proposal": proposal},
            source=INSTALLED_PACKAGE,
            watches=(("proposal", path, proposal),),
        )
        return host, path

    def test_live_selection_applies_between_frames(self) -> None:
        for vehicle in VEHICLES:
            with self.subTest(vehicle=vehicle):
                host, path = self._watched_proposal(vehicle)
                startup = host.applied_decision()["generation_id"]
                sink: dict = {}
                runner = host.step("proposal")
                original = runner.__call__
                written = False

                def run_and_restage(context):
                    nonlocal written
                    result = original(context)
                    if not written:
                        write_step_activation(path, packaged_activation("proposal", []))
                        written = True
                    return result

                with patch.object(type(runner), "__call__", lambda self, context: run_and_restage(context)):
                    _frame(host, 0)
                _publish(host, sink)
                self.assertEqual(sink["generation_id"], startup)
                self.assertEqual(list(runner.plugin_ids), ["avoid_recent_obstruction"])

                _frame(host, 1)
                applied = _publish(host, sink)
                self.assertNotEqual(applied["generation_id"], startup)
                self.assertEqual(list(host.step("proposal").plugin_ids), [])
                self.assertIsNot(host.step("proposal"), runner)
                self.assertEqual(sink["generation_id"], applied["generation_id"])
                self.assertEqual(sink["steps"]["proposal"]["plugins"], [])

    def test_config_changes_keep_the_startup_identity(self) -> None:
        for vehicle in VEHICLES:
            with self.subTest(vehicle=vehicle):
                host, path = self._watched_proposal(vehicle)
                startup = host.applied_decision()["generation_id"]
                write_step_activation(
                    path,
                    packaged_activation(
                        "proposal",
                        config_overrides={"avoid_recent_obstruction": {"steer_magnitude": 0.5}},
                    ),
                )
                _frame(host, 0)
                applied = host.applied_decision()
                self.assertEqual(applied["generation_id"], startup)
                self.assertEqual(
                    list(host.step("proposal").plugin_ids),
                    ["avoid_recent_obstruction"],
                )

    def test_status_and_publication_use_the_host(self) -> None:
        for vehicle in VEHICLES:
            with self.subTest(vehicle=vehicle):
                proposal = packaged_activation("proposal")
                memory = packaged_activation("memory")
                host = _open(
                    vehicle,
                    {"proposal": proposal, "memory": memory},
                    source=INSTALLED_PACKAGE,
                )
                _frame(host, 0)
                status = host.status()
                applied = host.applied_decision()
                sink: dict = {}
                _publish(host, sink)
                self.assertEqual(status["applied_decision"], applied)
                self.assertEqual(sink["generation_id"], applied["generation_id"])
                self.assertEqual(
                    list(status["steps"]["proposal"]["plugin_ids"]),
                    list(host.step("proposal").plugin_ids),
                )
                self.assertEqual(
                    list(status["steps"]["memory"]["plugin_ids"]),
                    list(host.step("memory").plugin_ids),
                )


if __name__ == "__main__":
    unittest.main()
