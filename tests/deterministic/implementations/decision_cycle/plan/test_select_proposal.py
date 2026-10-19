"""Named selection cannot fall back to another policy's proposal."""

from dataclasses import replace
import unittest

from autonomy.decision_cycle.proposal.inputs import build_decision_data_source
from implementations.decision_cycle.plan.plugins.select_proposal.plugin import SelectProposal
from implementations.decision_cycle.proposal.plugins.drive_forward.plugin import DriveForward


def _candidate(frame_id="frame_1"):
    return DriveForward().propose(build_decision_data_source(
        frame_id=frame_id, frame_index=0, timestamp_ms=10,
    ), None)


def _replace(candidate, **updates):
    return replace(candidate, metadata=dict(candidate.metadata), **updates)


class SelectProposalTests(unittest.TestCase):
    def test_named_candidate_wins_regardless_of_other_candidate_confidence(self):
        forward = _replace(_candidate(), confidence=0.1)
        other = _replace(forward, plugin_id="other", proposal_id="", confidence=1.0)
        result = SelectProposal().plan(
            (other, forward), None, None, frame_id="frame_1", timestamp_ms=10,
        )
        self.assertEqual(result.status, "selected")
        self.assertEqual(result.selected_proposal_id, forward.proposal_id)
        self.assertEqual(result.command, forward.command)
        self.assertEqual(result.confidence, 0.1)
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(result.contributions[0].plugin_id, "drive_forward")

    def test_missing_inactive_or_stale_target_stays_idle_without_fallback(self):
        forward = _candidate()
        other = _replace(forward, plugin_id="other", proposal_id="")
        inactive = _replace(forward, lifecycle="inactive", freshness="none",
                           available=False, command=None)
        stale = _replace(forward, lifecycle="stale", freshness="stale",
                         available=False, command=None)
        for target in (None, inactive, stale):
            with self.subTest(target=target):
                candidates = (other,) if target is None else (other, target)
                result = SelectProposal().plan(
                    candidates, None, None, frame_id="frame_1", timestamp_ms=10,
                )
                self.assertEqual(result.status, "idle")
                self.assertIsNone(result.command)
                self.assertIsNone(result.selected_proposal_id)

    def test_another_frame_candidate_is_rejected_by_plan_contract(self):
        with self.assertRaisesRegex(ValueError, "candidate frame_id"):
            SelectProposal().plan(
                (_candidate("old_frame"),), None, None, frame_id="frame_1", timestamp_ms=10,
            )

    def test_target_is_selected_from_configuration(self):
        other = _replace(_candidate(), plugin_id="other", proposal_id="")
        result = SelectProposal(proposal_plugin_id="other").plan(
            (other,), None, None, frame_id="frame_1", timestamp_ms=10,
        )
        self.assertEqual(result.selected_proposal_id, other.proposal_id)
