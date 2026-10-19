"""Named selection cannot fall back to another policy's proposal."""

from dataclasses import replace
import unittest

from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.proposal.inputs import build_decision_data_source
from implementations.decision_cycle.plan.plugins.select_proposal.plugin import SelectProposal
from implementations.decision_cycle.proposal.plugins.drive_backwards.plugin import DriveBackwards
from implementations.decision_cycle.proposal.plugins.drive_forward.plugin import DriveForward


def _candidate(frame_id="frame_1"):
    return DriveForward().propose(build_decision_data_source(
        frame_id=frame_id, frame_index=0, timestamp_ms=10,
    ), None)


def _source(view_static=None, *, signal=True):
    signals = ({"signal_id": "view_static", "value": view_static,
                "confidence": None, "properties": {}},) if signal else ()
    return build_decision_data_source(
        frame_id="frame_1", frame_index=0, timestamp_ms=10, observation_configured=True,
        observation=Observation(
            observation_id="observation_1", created_at_ms=10, sensor_frame={}, signals=signals,
        ),
    )


def _switching():
    return SelectProposal(view_static_proposal_plugin_id="drive_backwards")


def _both():
    source = _source()
    return DriveForward().propose(source, None), DriveBackwards().propose(source, None)


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

    def test_static_view_switches_to_the_configured_alternate(self):
        forward, backwards = _both()
        result = _switching().plan(
            (forward, backwards), _source(True), None, frame_id="frame_1", timestamp_ms=10,
        )
        self.assertEqual(result.selected_proposal_id, backwards.proposal_id)
        self.assertEqual(result.command.throttle, -0.60)
        self.assertEqual(result.contributions[0].plugin_id, "drive_backwards")

    def test_moving_unknown_or_missing_view_keeps_the_primary_target(self):
        forward, backwards = _both()
        unavailable = build_decision_data_source(
            frame_id="frame_1", frame_index=0, timestamp_ms=10, observation_configured=True,
        )
        for source in (_source(False), _source(None), _source(signal=False), unavailable, None):
            with self.subTest(source=source):
                result = _switching().plan(
                    (forward, backwards), source, None, frame_id="frame_1", timestamp_ms=10,
                )
                self.assertEqual(result.selected_proposal_id, forward.proposal_id)

    def test_static_view_without_the_alternate_stays_idle(self):
        forward, _ = _both()
        result = _switching().plan(
            (forward,), _source(True), None, frame_id="frame_1", timestamp_ms=10,
        )
        self.assertEqual(result.status, "idle")
        self.assertIsNone(result.command)

    def test_static_view_is_ignored_without_an_alternate(self):
        forward, backwards = _both()
        result = SelectProposal().plan(
            (forward, backwards), _source(True), None, frame_id="frame_1", timestamp_ms=10,
        )
        self.assertEqual(result.selected_proposal_id, forward.proposal_id)

    def test_active_override_wins_over_both_targets(self):
        forward, backwards = _both()
        override = _replace(forward, plugin_id="avoid_obstacle", proposal_id="")
        plan = SelectProposal(
            view_static_proposal_plugin_id="drive_backwards", override_proposal_plugin_id="avoid_obstacle",
        )
        for source in (_source(False), _source(True)):
            with self.subTest(source=source):
                result = plan.plan(
                    (forward, backwards, override), source, None, frame_id="frame_1", timestamp_ms=10,
                )
                self.assertEqual(result.selected_proposal_id, override.proposal_id)
                self.assertEqual(result.contributions[0].plugin_id, "avoid_obstacle")

    def test_inactive_or_missing_override_falls_through(self):
        forward, backwards = _both()
        inactive = _replace(forward, plugin_id="avoid_obstacle", proposal_id="", lifecycle="inactive",
                            freshness="none", available=False, command=None, source_refs=(), confidence=0.0)
        plan = SelectProposal(
            view_static_proposal_plugin_id="drive_backwards", override_proposal_plugin_id="avoid_obstacle",
        )
        for candidates in ((forward, backwards, inactive), (forward, backwards)):
            with self.subTest(candidates=len(candidates)):
                moving = plan.plan(candidates, _source(False), None, frame_id="frame_1", timestamp_ms=10)
                static = plan.plan(candidates, _source(True), None, frame_id="frame_1", timestamp_ms=10)
                self.assertEqual(moving.selected_proposal_id, forward.proposal_id)
                self.assertEqual(static.selected_proposal_id, backwards.proposal_id)

    def test_invalid_override_id_is_rejected(self):
        with self.assertRaises(ValueError):
            SelectProposal(override_proposal_plugin_id="not ascii é")
