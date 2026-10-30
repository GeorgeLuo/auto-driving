from __future__ import annotations

import unittest
from pathlib import Path

from autonomy.decision_cycle.activation import (
    read_step_activation,
    step_activation,
    write_step_activation,
)
from autonomy.decision_cycle.steps import load_decision_steps
from cli.automa_cli.hosts.step_activations import (
    bundle_activation_path,
    host_bundle,
)
from tests.shared.cli.decision.decision_surfaces_fixtures import (
    DecisionSurfaceFixture,
    packaged_decision_steps,
)


class ProposalStagingTests(DecisionSurfaceFixture, unittest.TestCase):
    def test_staged_steps_load_their_plugins_from_the_staged_bundle(self) -> None:
        self._stage()
        bundle = host_bundle("chase-sim-chaser", self.runtime_root)
        activation_path = bundle_activation_path(bundle, "proposal")
        original = read_step_activation(activation_path, "proposal")
        plugin_path = Path(bundle["root_dir"]) / (
            "implementations/decision_cycle/proposal/plugins/bundle_only.py"
        )
        plugin_path.write_text(
            "from implementations.decision_cycle.proposal.plugins."
            "avoid_recent_obstruction.plugin import AvoidRecentObstruction\n"
            "class BundleOnlyProposal(AvoidRecentObstruction):\n"
            "    plugin_id = 'bundle_only'\n"
            "    def propose(self, context):\n"
            "        from implementations.decision_cycle.proposal.plugins."
            "bundle_only_helper import MARKER\n"
            "        context.shared_memory['bundle_marker'] = MARKER\n"
            "        return super().propose(context)\n",
            encoding="utf-8",
        )
        plugin_path.with_name("bundle_only_helper.py").write_text(
            "MARKER = 'from the staged bundle'\n", encoding="utf-8",
        )
        write_step_activation(activation_path, step_activation(
            "proposal", ["bundle_only"],
            {"bundle_only": "implementations.decision_cycle.proposal.plugins.bundle_only:BundleOnlyProposal"},
            metadata=original.metadata,
        ))

        steps = load_decision_steps(Path(bundle["root_dir"]) / "runtime")
        self.assertEqual(steps.proposal.status()["plugin_ids"], ["bundle_only"])

    def test_stage_writes_the_packaged_proposal_with_the_hold_action(self) -> None:
        identity = self._stage()
        staged = identity["steps"]["proposal"]
        expected = packaged_decision_steps()["proposal"]
        for key in ("plugins", "plugin_specs", "plugin_configs"):
            self.assertEqual(staged[key], expected[key])
        self.assertEqual(staged["metadata"]["host_name"], "chase-sim-chaser")
        self.assertEqual(identity["steps"]["action"]["plugins"], ["hold"])

    def test_staged_steps_load_from_the_runtime_directory(self) -> None:
        self._stage()
        steps = load_decision_steps(
            self.runtime_root / "chase-sim-chaser" / "bundle" / "runtime"
        )
        self.assertEqual(steps.proposal.status()["plugin_ids"], ["avoid_recent_obstruction"])
        self.assertEqual(steps.action.status()["plugin_ids"], ["hold"])
