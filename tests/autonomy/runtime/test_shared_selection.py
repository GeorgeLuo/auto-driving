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
from pathlib import Path
from unittest.mock import patch

from autonomy.decision_cycle.activation import (
    DECISION_STEPS,
    activation_generation_id,
    step_activation,
    write_step_activation,
)
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.steps import builtin_activation, decision_steps
from autonomy.runtime.plugin_loader import INSTALLED_PACKAGE, CodeSource, load_runner
from cli.automa_cli.bundles import controller_bundle_paths, sync_controller_bundle
from cli.automa_cli.decision_view import DecisionView
from implementations.decision_cycle.catalog import packaged_activation
from implementations.runtime.chase_sim import create_host as create_chase_host
from implementations.runtime.picar import create_host as create_picar_host

LAZY_MODULE = "implementations.decision_cycle.proposal.lazy_only"
VEHICLES = ("chase", "picar")


class _Car:
    """Stand-in for the simulator car. Manual runs do not call it."""


def _frame(host, index: int):
    return host.run(DecisionFrameContext(f"frame-{index}", index, 1_000 + index))


def _identity(proposal) -> dict:
    steps = {
        step: (
            proposal.to_payload()
            if step == "proposal"
            else builtin_activation(step).to_payload()
        )
        for step in DECISION_STEPS
    }
    return {
        "generation_id": activation_generation_id(steps, prefix="decision"),
        "steps": steps,
    }


def _open(vehicle: str, activations: dict, *, source, watches=()):
    steps = decision_steps(activations, source=source)
    host = (
        create_chase_host(_Car(), steps=steps)
        if vehicle == "chase"
        else create_picar_host(steps=steps)
    )
    for step, path, loaded in watches:
        host.watch_selection(step, path, loaded)
    if "proposal" in activations:
        host.use_applied_decision(_identity(activations["proposal"]))
    return host


def _publish(host, sink: dict, view: DecisionView | None = None) -> dict:
    """Copy the host identity the way each vehicle's frame loop does."""

    applied = host.applied_decision()
    sink["generation_id"] = applied["generation_id"]
    sink["steps"] = applied["steps"]
    if view is not None:
        current = view.health_payload()["identity"]["activation_generation_id"]
        if applied["generation_id"] != current:
            view.adopt(applied)
    return applied


class SharedSelectionTests(unittest.TestCase):
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
            sync_controller_bundle(bundle, output=None)
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
            sync_controller_bundle(bundle, output=None)
            bundle_root = Path(bundle["root_dir"])
            staged = bundle_root / "implementations/decision_cycle/proposal/lazy_only.py"
            staged.write_text(
                "class LazyOnly:\n"
                "    plugin_id = 'lazy_only'\n"
                "    def propose(self, context, observation):\n"
                "        return None\n",
                encoding="utf-8",
            )
            host_catalog = sys.modules["implementations.decision_cycle.proposal.catalog"]
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
            self.assertIs(sys.modules["implementations.decision_cycle.proposal.catalog"], host_catalog)
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
                view = (
                    DecisionView(
                        vehicle_id="chase-sim-chaser",
                        run_id="run-1",
                        worker_pid=1,
                        activation=host.applied_decision(),
                        activation_path=path,
                    )
                    if vehicle == "chase"
                    else None
                )
                runner = host.step("proposal")
                original = runner.run
                written = False

                def run_and_restage(**kwargs):
                    nonlocal written
                    result = original(**kwargs)
                    if not written:
                        write_step_activation(path, packaged_activation("proposal", []))
                        written = True
                    return result

                runner.run = run_and_restage
                _frame(host, 0)
                _publish(host, sink, view)
                self.assertEqual(sink["generation_id"], startup)
                self.assertEqual(list(runner.plugin_ids), ["avoid_recent_obstruction"])
                if view is not None:
                    self.assertEqual(
                        view.health_payload()["identity"]["activation_generation_id"],
                        startup,
                    )

                _frame(host, 1)
                applied = _publish(host, sink, view)
                self.assertNotEqual(applied["generation_id"], startup)
                self.assertEqual(list(runner.plugin_ids), [])
                self.assertEqual(sink["generation_id"], applied["generation_id"])
                self.assertEqual(sink["steps"]["proposal"]["plugins"], [])
                if view is not None:
                    self.assertEqual(
                        view.health_payload()["identity"]["activation_generation_id"],
                        applied["generation_id"],
                    )

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

    def test_failed_selection_keeps_the_applied_identity(self) -> None:
        for vehicle in VEHICLES:
            with self.subTest(vehicle=vehicle):
                host, path = self._watched_proposal(vehicle)
                startup = host.applied_decision()["generation_id"]
                plugin = host.step("proposal").plugins["avoid_recent_obstruction"]

                def failing_reset(shared_memory=None):
                    del shared_memory
                    raise RuntimeError("proposal reset unavailable")

                plugin.reset = failing_reset
                write_step_activation(path, packaged_activation("proposal", []))
                _frame(host, 0)
                sink: dict = {}
                applied = _publish(host, sink)
                # The requested selection is recorded on the manager, and the
                # failed reset leaves the plugins that were already applied.
                self.assertEqual(list(host.step("proposal").plugin_manager.selected_ids), [])
                self.assertEqual(list(host.step("proposal").plugin_ids), ["avoid_recent_obstruction"])
                self.assertEqual(applied["generation_id"], startup)
                self.assertEqual(sink["generation_id"], startup)

    def test_recovered_selection_adopts_its_pending_identity(self) -> None:
        for vehicle in VEHICLES:
            for failure in ("load", "reset"):
                with self.subTest(vehicle=vehicle, failure=failure):
                    host, path = self._watched_proposal(vehicle)
                    runner = host.step("proposal")
                    if failure == "load":
                        write_step_activation(path, packaged_activation("proposal", []))
                        _frame(host, 0)
                        replacement = packaged_activation("proposal")
                        owner, method = runner, "load_plugin"
                    else:
                        replacement = packaged_activation("proposal", [])
                        owner = runner.plugins["avoid_recent_obstruction"]
                        method = "reset"
                    original = getattr(owner, method, lambda *args, **kwargs: None)
                    attempts = 0

                    def fail_once(*args, **kwargs):
                        nonlocal attempts
                        attempts += 1
                        if attempts == 1:
                            raise RuntimeError(f"transient proposal {failure} failure")
                        return original(*args, **kwargs)

                    previous = host.applied_decision()
                    write_step_activation(path, replacement)
                    # An explicit sync must retain the request for run(), too.
                    host.sync_selection()
                    with patch.object(owner, method, side_effect=fail_once, create=True):
                        _frame(host, 1)
                        self.assertEqual(host.applied_decision(), previous)
                        self.assertEqual(runner.plugin_manager.selected_ids, replacement.plugins)
                        self.assertNotEqual(runner.plugin_ids, replacement.plugins)
                        _frame(host, 2)
                    self.assertEqual(attempts, 2)
                    self.assertEqual(runner.plugin_ids, replacement.plugins)
                    self.assertEqual(host.applied_decision(), _identity(replacement))
                    self.assertEqual(host.status()["applied_decision"], _identity(replacement))

    def test_status_reset_and_publication_use_the_host(self) -> None:
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
                report = host.reset_memory()
                self.assertIsInstance(report, dict)
                self.assertEqual(
                    list(host.status()["steps"]["memory"]["plugin_ids"]),
                    list(host.step("memory").plugin_ids),
                )


if __name__ == "__main__":
    unittest.main()
