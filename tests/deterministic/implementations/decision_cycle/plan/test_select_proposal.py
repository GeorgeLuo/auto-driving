"""Named selection cannot fall back to another policy's proposal."""

from dataclasses import replace
import unittest

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.publication import write_observation
from autonomy.decision_cycle.observation.values import Observation
from implementations.decision_cycle.plan.plugins.select_proposal.plugin import SelectProposal
from implementations.decision_cycle.proposal.plugins.drive_backwards.plugin import DriveBackwards
from implementations.decision_cycle.proposal.plugins.drive_forward.plugin import DriveForward


def _context(frame_id="frame_1", timestamp_ms=10, *, shared_memory=None, observation=None):
    memory = shared_memory
    if observation is not None:
        memory = {} if memory is None else memory
        write_observation(memory, observation)
    return DecisionFrameContext(
        frame_id=frame_id, frame_index=0, timestamp_ms=timestamp_ms, shared_memory=memory,
    )


def _candidate(frame_id="frame_1"):
    return DriveForward().propose(_context(frame_id, observation=Observation("o", 10, {})))


def _view(view_static=None, *, signal=True):
    signals = ({"signal_id": "view_static", "value": view_static,
                "confidence": None, "properties": {}},) if signal else ()
    return _context(observation=Observation(
        observation_id="observation_1", created_at_ms=10, sensor_frame={}, signals=signals,
    ))


def _switching():
    return SelectProposal(view_static_proposal_plugin_id="drive_backwards")


def _both():
    context = _view()
    return DriveForward().propose(context), DriveBackwards().propose(context)


def _replace(candidate, **updates):
    return replace(candidate, metadata=dict(candidate.metadata), **updates)


class SelectProposalTests(unittest.TestCase):
    def test_named_candidate_wins_regardless_of_other_candidate_confidence(self):
        forward = _replace(_candidate(), confidence=0.1)
        other = _replace(forward, plugin_id="other", proposal_id="", confidence=1.0)
        result = SelectProposal().plan(_context(), (other, forward))
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
                result = SelectProposal().plan(_context(), candidates)
                self.assertEqual(result.status, "idle")
                self.assertIsNone(result.command)
                self.assertIsNone(result.selected_proposal_id)

    def test_another_frame_candidate_is_rejected_by_plan_contract(self):
        with self.assertRaisesRegex(ValueError, "candidate frame_id"):
            SelectProposal().plan(_context(), (_candidate("old_frame"),))

    def test_target_is_selected_from_configuration(self):
        other = _replace(_candidate(), plugin_id="other", proposal_id="")
        result = SelectProposal(proposal_plugin_id="other").plan(_context(), (other,))
        self.assertEqual(result.selected_proposal_id, other.proposal_id)

    def test_static_view_switches_to_the_configured_alternate(self):
        forward, backwards = _both()
        result = _switching().plan(_view(True), (forward, backwards))
        self.assertEqual(result.selected_proposal_id, backwards.proposal_id)
        self.assertEqual(result.command.throttle, -0.60)
        self.assertEqual(result.contributions[0].plugin_id, "drive_backwards")

    def test_moving_unknown_or_missing_view_keeps_the_primary_target(self):
        forward, backwards = _both()
        for context in (_view(False), _view(None), _view(signal=False), _context(shared_memory={}), _context()):
            with self.subTest(context=context):
                result = _switching().plan(context, (forward, backwards))
                self.assertEqual(result.selected_proposal_id, forward.proposal_id)

    def test_static_view_without_the_alternate_stays_idle(self):
        forward, _ = _both()
        result = _switching().plan(_view(True), (forward,))
        self.assertEqual(result.status, "idle")
        self.assertIsNone(result.command)

    def test_static_view_is_ignored_without_an_alternate(self):
        forward, backwards = _both()
        result = SelectProposal().plan(_view(True), (forward, backwards))
        self.assertEqual(result.selected_proposal_id, forward.proposal_id)

    def test_active_override_wins_over_both_targets(self):
        forward, backwards = _both()
        override = _replace(forward, plugin_id="avoid_obstacle", proposal_id="")
        plan = SelectProposal(
            view_static_proposal_plugin_id="drive_backwards", override_proposal_plugin_id="avoid_obstacle",
        )
        for context in (_view(False), _view(True)):
            with self.subTest(context=context):
                result = plan.plan(context, (forward, backwards, override))
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
                moving = plan.plan(_view(False), candidates)
                static = plan.plan(_view(True), candidates)
                self.assertEqual(moving.selected_proposal_id, forward.proposal_id)
                self.assertEqual(static.selected_proposal_id, backwards.proposal_id)

    def test_invalid_override_id_is_rejected(self):
        with self.assertRaises(ValueError):
            SelectProposal(override_proposal_plugin_id="not ascii é")

    def test_chase_without_observation_plans_idle(self):
        from implementations.decision_cycle.proposal.plugins.avoid_obstacle.plugin import AvoidObstacle
        context = _context(shared_memory={})
        candidates = (DriveForward().propose(context), DriveBackwards().propose(context),
                      AvoidObstacle().propose(context))
        plan = SelectProposal(override_proposal_plugin_id="avoid_obstacle",
                              view_static_proposal_plugin_id="drive_backwards").plan(context, candidates)
        self.assertEqual(plan.status, "idle")
        self.assertIsNone(plan.command)
