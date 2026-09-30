from __future__ import annotations

import unittest

from autonomy.decision_cycle.action import ActionComposition
from autonomy.decision_cycle.proposal.selection import (
    PROPOSAL_STEP,
    load_proposal_plugins,
    proposal_manager_from_config,
    proposal_plugin_manager,
)
from autonomy.decision_cycle.proposal.values import ActionProposal
from autonomy.plugins import PluginManagementError, PluginManager


class _ConfiguredProposal:
    """Proposes idle under its configured ID and records its construction config."""

    def __init__(self, *, plugin_id: str, reason: str = "idle") -> None:
        self.plugin_id = plugin_id
        self.reason = reason

    def __call__(self, source, shared_memory) -> ActionProposal:
        return ActionProposal(
            plugin_id=self.plugin_id,
            frame_id=source.frame_id,
            lifecycle="inactive",
            freshness="none",
            confidence=0.0,
            reason=self.reason,
            command=None,
            available=False,
        )


class _Mislabeled(_ConfiguredProposal):
    def __init__(self) -> None:
        super().__init__(plugin_id="someone_else")


def _uncallable(**config):
    return object()


def _spec(name: str) -> str:
    return f"{__name__}:{name}"


def _document(plugins, count: int = 6) -> dict:
    ids = [f"p{index}" for index in range(count)]
    return {
        "plugins": plugins,
        "plugin_specs": {plugin_id: _spec("_ConfiguredProposal") for plugin_id in ids},
        "plugin_configs": {
            plugin_id: {"plugin_id": plugin_id, "reason": f"from_{plugin_id}"} for plugin_id in ids
        },
    }


class ProposalSelectionTests(unittest.TestCase):
    def test_manager_is_scoped_to_the_proposal_step(self) -> None:
        manager = proposal_plugin_manager({"p0": _spec("_ConfiguredProposal")})
        self.assertEqual(manager.step, PROPOSAL_STEP)
        self.assertEqual(manager.available_ids, ("p0",))
        self.assertEqual(manager.selected_ids, ())

    def test_selection_has_no_count_limit_and_keeps_order(self) -> None:
        order = ["p5", "p0", "p3", "p1", "p4", "p2"]
        manager = proposal_manager_from_config(_document(order))
        self.assertEqual(manager.selected_ids, tuple(order))
        plugins = load_proposal_plugins(manager)
        self.assertEqual(list(plugins), order)
        self.assertEqual(plugins["p3"].reason, "from_p3")

        result = ActionComposition(plugins=plugins).run(
            frame_id="frame_001", frame_index=0, timestamp_ms=1
        )
        assert result.plan is not None
        self.assertEqual(
            [candidate.reason for candidate in result.plan.candidates],
            [f"from_p{index}" for index in range(6)],
        )

    def test_empty_selection_loads_nothing(self) -> None:
        manager = proposal_manager_from_config(_document([]))
        self.assertEqual(load_proposal_plugins(manager), {})

    def test_invalid_documents_are_rejected_before_loading(self) -> None:
        cases = {
            "unknown key": {**_document(["p0"]), "enabled_plugins": ["p0"]},
            "plugins not a list": _document("p0"),
            "duplicate ids": _document(["p0", "p0"]),
            "specs missing": {"plugins": ["p0"]},
        }
        for name, document in cases.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    proposal_manager_from_config(document)
        with self.assertRaises(PluginManagementError):
            proposal_manager_from_config(_document(["ghost"]))

    def test_loaded_plugin_must_be_callable_under_its_selected_id(self) -> None:
        for spec, message in (
            (_spec("_Mislabeled"), "declares plugin_id"),
            (_spec("_uncallable"), "not callable"),
        ):
            with self.subTest(spec):
                manager = proposal_manager_from_config(
                    {"plugins": ["p0"], "plugin_specs": {"p0": spec}}
                )
                with self.assertRaisesRegex(TypeError, message):
                    load_proposal_plugins(manager)

    def test_bad_plugin_config_fails_at_load(self) -> None:
        manager = proposal_manager_from_config(
            {
                "plugins": ["p0"],
                "plugin_specs": {"p0": _spec("_ConfiguredProposal")},
                "plugin_configs": {"p0": {"plugin_id": "p0", "unknown": 1}},
            }
        )
        with self.assertRaises(TypeError):
            load_proposal_plugins(manager)

    def test_other_step_managers_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            load_proposal_plugins(PluginManager.from_specs("memory", {}))


if __name__ == "__main__":
    unittest.main()
