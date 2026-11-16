"""Policy handoffs, minimum acting time and ordered executor runs."""

import unittest

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.action.publication import CONTROL_APPLICATIONS_KEY
from autonomy.decision_cycle.action.selected import SelectedAction
from autonomy.runtime.execution import CONTROL_APPLICATION_LIMIT, ControlApplication
from autonomy.decision_cycle.proposal.values import ActionProposal, ProposedVehicleCommand, SourceRef
from autonomy.shared_memory import shared_memory_document, shared_memory_from_document
from implementations.decision_cycle.shared.control_runs import read_control_runs, record_control_runs
from implementations.decision_cycle.shared.policy import (
    PolicyCycle, follow_policy_plan, read_exit, read_policy, write_exit,
)


def definitions():
    return {
        "search": {"when": "target absent", "goal": "find target", "how": "look around",
                   "executors": ["sweep", "query"]},
        "approach": {"when": "target visible", "goal": "reach target", "executors": ["center"]},
    }


def candidate(memory, plugin_id, at, *, name="search", epoch=0, command=(1, -1), active=True):
    return ActionProposal(
        plugin_id=plugin_id, frame_id=f"f{at}", lifecycle="fresh" if active else "inactive",
        freshness="fresh" if active else "none", available=active, reason="moving", confidence=.7,
        command=ProposedVehicleCommand(throttle=command[0], steering=command[1]) if active else None,
        source_refs=(SourceRef(kind="memory_record", id="proposal.policy", frame_id=f"f{at}"),) if active else (),
        metadata={"policy": {**read_policy(memory).tag(), "name": name, "epoch": epoch}})


def plan(memory, at, *candidates):
    context = DecisionFrameContext(frame_id=f"f{at}", frame_index=at, timestamp_ms=at, shared_memory=memory)
    selected = follow_policy_plan(context, candidates)
    control = SelectedAction().decide(context, selected).control
    application = ControlApplication(context.frame_id, "autonomy", True, control.reason, control, at)
    memory[CONTROL_APPLICATIONS_KEY] = (*memory.get(CONTROL_APPLICATIONS_KEY, ()), application)[-CONTROL_APPLICATION_LIMIT:]
    record_control_runs(memory)
    return selected


class PolicyCycleTests(unittest.TestCase):
    def setUp(self):
        self.memory = {CONTROL_APPLICATIONS_KEY: ()}
        record_control_runs(self.memory)
        self.cycle = PolicyCycle(definitions(), initial="search", min_acting_ms=100)

    def test_initial_is_published_and_definitions_are_detached(self):
        self.assertIsNone(read_policy(self.memory))
        current = self.cycle.current(self.memory, now_ms=20)
        self.assertEqual((current.name, current.epoch, current.since_ms, current.reason), ("search", 0, 20, "initial"))
        self.assertEqual(current.executors, ("sweep", "query"))
        self.assertTrue(current.assigns("sweep"))
        self.assertFalse(current.assigns("center"))
        self.assertEqual(current.definition["how"], "look around")
        current.definition["executors"].clear()
        self.assertEqual(read_policy(self.memory).executors, ("sweep", "query"))

    def test_publication_is_rebuilt_from_owned_state_after_restore(self):
        cycle = PolicyCycle(definitions(), initial="search", key="proposal.judge.policy")
        cycle.current(self.memory, now_ms=0)
        self.memory["proposal.policy"]["name"] = "approach"
        memory = shared_memory_from_document(shared_memory_document(self.memory))
        self.assertEqual(cycle.current(memory, now_ms=1).name, "search")
        self.assertEqual(memory["proposal.policy"], memory["proposal.judge.policy"])

    def test_change_then_equal_choice_preserves_entry_time_and_reason(self):
        self.cycle.current(self.memory, now_ms=0)
        plan(self.memory, 10, candidate(self.memory, "sweep", 10))
        changed = self.cycle.choose(self.memory, "approach", now_ms=110, reason="judge")
        self.assertEqual((changed.name, changed.epoch, changed.since_ms, changed.reason), ("approach", 1, 110, "judge"))
        self.assertEqual(self.cycle.choose(self.memory, "approach", now_ms=200), changed)
        self.assertEqual(self.memory["proposal.policy"]["changes"], [
            {"from": "search", "to": "approach", "epoch": 1, "at_ms": 110, "reason": "judge"}])

    def test_changes_keep_only_last_ten(self):
        for at in range(14):
            current = self.cycle.current(self.memory, now_ms=at * 200)
            executor = current.executors[0]
            plan(self.memory, at * 200, candidate(self.memory, executor, at * 200, name=current.name, epoch=current.epoch))
            self.cycle.choose(self.memory, "approach" if at % 2 == 0 else "search", now_ms=at * 200 + 100)
        changes = self.memory["proposal.policy"]["changes"]
        self.assertEqual(len(changes), 10)
        self.assertEqual([row["epoch"] for row in changes], list(range(5, 15)))

    def test_only_current_listed_exit_leaves_policy_undecided(self):
        current = self.cycle.current(self.memory, now_ms=0)
        write_exit(self.memory, "center", current, why="blocked", now_ms=1)
        self.memory["proposal.query.exit"] = {"epoch": -1, "why": "timeout", "at_ms": 0, "detail": {}}
        self.assertEqual(self.cycle.current(self.memory, now_ms=2), current)
        write_exit(self.memory, "sweep", current, why="lost", now_ms=3)
        undecided = self.cycle.current(self.memory, now_ms=4)
        self.assertEqual((undecided.name, undecided.epoch, undecided.reason), (None, 1, "exit:sweep:lost"))
        self.assertEqual(undecided.executors, ())
        self.assertEqual(self.cycle.current(self.memory, now_ms=5), undecided)
        self.assertIsNone(read_exit(self.memory, "sweep", undecided))

    def test_any_choice_decides_after_exit_including_the_same_name(self):
        for name in ("search", "approach"):
            with self.subTest(name=name):
                memory = {CONTROL_APPLICATIONS_KEY: ()}
                record_control_runs(memory)
                current = self.cycle.current(memory, now_ms=0)
                write_exit(memory, "sweep", current, why="done", now_ms=1)
                self.cycle.current(memory, now_ms=2)
                decided = self.cycle.choose(memory, name, now_ms=2)
                self.assertEqual((decided.name, decided.epoch, decided.reason), (name, 2, "cycler"))

    def test_placeholder_stop_does_not_start_floor_and_earliest_real_run_does(self):
        self.cycle.current(self.memory, now_ms=0)
        plan(self.memory, 0, candidate(self.memory, "query", 0, command=(0, 0)))
        self.assertEqual(self.cycle.choose(self.memory, "approach", now_ms=200).epoch, 0)
        plan(self.memory, 210, candidate(self.memory, "query", 210))
        plan(self.memory, 250, candidate(self.memory, "sweep", 250))
        self.assertEqual(self.cycle.choose(self.memory, "approach", now_ms=309).epoch, 0)
        self.assertEqual(self.cycle.held(self.memory), 2)
        self.assertEqual(self.cycle.choose(self.memory, "approach", now_ms=310).epoch, 1)


    def test_backwards_clock_still_measures_elapsed_time_from_earliest_current_run(self):
        self.cycle.current(self.memory, now_ms=0)
        plan(self.memory, 200, candidate(self.memory, "sweep", 200))
        self.assertEqual(self.cycle.choose(self.memory, "approach", now_ms=100).epoch, 0)
        self.assertEqual(self.cycle.choose(self.memory, "approach", now_ms=299).epoch, 0)
        self.assertEqual(self.cycle.choose(self.memory, "approach", now_ms=300).epoch, 1)

    def test_prior_epoch_run_cannot_satisfy_new_epoch_floor(self):
        self.cycle.current(self.memory, now_ms=0)
        plan(self.memory, 0, candidate(self.memory, "sweep", 0))
        self.cycle.choose(self.memory, "approach", now_ms=100)
        self.assertEqual(self.cycle.choose(self.memory, "search", now_ms=1000).epoch, 1)
        self.assertEqual(self.cycle.held(self.memory), 1)

    def test_exit_is_sticky_and_detail_is_detached(self):
        current = self.cycle.current(self.memory, now_ms=0)
        detail = {"since_seen_ms": 300}
        first = write_exit(self.memory, "sweep", current, why="lost", now_ms=1, detail=detail)
        detail["since_seen_ms"] = 900
        self.assertEqual(write_exit(self.memory, "sweep", current, why="done", now_ms=2), first)
        first["detail"]["since_seen_ms"] = 1000
        self.assertEqual(read_exit(self.memory, "sweep", current)["detail"]["since_seen_ms"], 300)
        with self.assertRaises(ValueError):
            write_exit(self.memory, "sweep", current, why="other", now_ms=3)

    def test_choices_fail_when_control_runs_memory_is_not_armed(self):
        memory = {}
        self.cycle.current(memory, now_ms=0)
        for name in ("search", "approach"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "arm memory/control_runs"):
                self.cycle.choose(memory, name, now_ms=1000)

    def test_selection_alone_and_unapplied_controls_cannot_satisfy_floor(self):
        self.cycle.current(self.memory, now_ms=0)
        context = DecisionFrameContext("f0", 0, 0, shared_memory=self.memory)
        selected = follow_policy_plan(context, (candidate(self.memory, "sweep", 0),))
        control = SelectedAction().decide(context, selected).control
        self.memory[CONTROL_APPLICATIONS_KEY] = (ControlApplication("f0", "autonomy", False, "stale", control),)
        record_control_runs(self.memory)
        self.assertEqual(self.cycle.choose(self.memory, "approach", now_ms=1000).epoch, 0)

    def test_bad_config_and_unknown_choice_are_rejected(self):
        for kwargs in ({"initial": "missing"}, {"initial": "search", "min_acting_ms": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                PolicyCycle(definitions(), **kwargs)
        with self.assertRaises(ValueError):
            PolicyCycle({}, initial="search")
        self.cycle.current(self.memory, now_ms=0)
        with self.assertRaises(ValueError):
            self.cycle.choose(self.memory, "missing", now_ms=1)
        self.assertEqual(read_policy(self.memory).epoch, 0)


class PolicyPlanTests(unittest.TestCase):
    def setUp(self):
        self.memory = {CONTROL_APPLICATIONS_KEY: ()}
        record_control_runs(self.memory)
        self.cycle = PolicyCycle(definitions(), initial="search", min_acting_ms=0)
        self.current = self.cycle.current(self.memory, now_ms=0)

    def test_order_wins_over_candidate_order_and_confidence_is_preserved(self):
        original = candidate(self.memory, "sweep", 0)
        selected = plan(self.memory, 0, candidate(self.memory, "query", 0), original)
        self.assertEqual(selected.selected_candidate().plugin_id, "sweep")
        self.assertEqual(selected.reason, "moving")
        self.assertIs(selected.selected_candidate(), original)
        self.assertEqual(selected.confidence, original.confidence)
        self.assertEqual(original.reason, "moving")
        self.assertEqual(selected.selected_candidate().reason, selected.reason)
        self.assertEqual(dict(selected.metadata["policy"]), self.current.tag())
        self.assertEqual(plan(self.memory, 10, candidate(self.memory, "sweep", 10, active=False), candidate(self.memory, "query", 10))
                         .selected_candidate().plugin_id, "query")

    def test_unlisted_wrong_name_and_wrong_epoch_cannot_drive(self):
        idle = plan(self.memory, 0, candidate(self.memory, "center", 0), candidate(self.memory, "sweep", 0, epoch=1),
                    candidate(self.memory, "query", 0, name="approach"))
        self.assertEqual(idle.status, "idle")
        self.assertIsNone(idle.command)

    def test_no_publication_and_undecided_are_idle(self):
        self.assertEqual(plan({}, 0, candidate(self.memory, "sweep", 0)).status, "idle")
        write_exit(self.memory, "sweep", self.current, why="lost", now_ms=1)
        self.cycle.current(self.memory, now_ms=2)
        self.assertEqual(plan(self.memory, 2, candidate(self.memory, "query", 2, name=None, epoch=1)).status, "idle")

    def test_any_current_listed_exit_stops_sideways_handoff_and_closes_run(self):
        plan(self.memory, 0, candidate(self.memory, "sweep", 0))
        write_exit(self.memory, "query", self.current, why="timeout", now_ms=10)
        idle = plan(self.memory, 10, candidate(self.memory, "sweep", 10), candidate(self.memory, "query", 10))
        self.assertEqual(idle.status, "idle")
        self.assertEqual(read_control_runs(self.memory)[-1]["end_ms"], 10)
        self.assertEqual(read_policy(self.memory).epoch, 0)  # cycler has not consumed the exit yet

    def test_stale_or_unlisted_exits_do_not_stop_selection(self):
        write_exit(self.memory, "center", self.current, why="blocked", now_ms=0)
        self.memory["proposal.sweep.exit"] = {"epoch": -1, "why": "lost"}
        self.assertEqual(plan(self.memory, 0, candidate(self.memory, "sweep", 0)).status, "selected")

    def test_selection_has_no_shared_memory_writes_and_keeps_candidate_metadata(self):
        original = candidate(self.memory, "sweep", 0)
        before = shared_memory_document(self.memory)
        selected = follow_policy_plan(DecisionFrameContext("f0", 0, 0, shared_memory=self.memory), (original,))
        self.assertIs(selected.selected_candidate(), original)
        self.assertEqual(selected.selected_candidate().to_dict(), original.to_dict())
        self.assertEqual(shared_memory_document(self.memory), before)
