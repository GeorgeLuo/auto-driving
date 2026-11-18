"""Event-ordered plugin resources for lifecycle interaction regressions."""

import threading

from autonomy.decision_cycle.proposal.values import ActionProposal, ProposedVehicleCommand, SourceRef
from implementations.decision_cycle.shared.deferred_call import DeferredCall
from implementations.decision_cycle.shared.policy import PolicyCycle, read_policy
from tests.support.action_fixtures import InactiveProposal

GATES = {}
JUDGE_SPEC = f"{__name__}:DeferredJudge"
EXECUTOR_SPEC = f"{__name__}:CompletingExecutor"
POLICY_SPEC = f"{__name__}:PolicyJudge"
ACTOR_SPEC = f"{__name__}:PolicyActor"
CALL_KEY = "proposal.judge.call"
ANSWERS_KEY = "proposal.judge.answers"
POLICIES = {name: {"when": "always", "goal": name, "executors": ["actor"]} for name in ("search", "approach")}


class Gate:
    def __init__(self):
        self.release = threading.Event()
        self.entered = threading.Condition()
        self.started = 0
        self.calls = []
        self.complete_during_executor = False
        self.foreign_write = False
        self.foreign_take = False

    def run(self, request):
        with self.entered:
            self.started += 1
            self.entered.notify_all()
        if not self.release.wait(5):
            raise AssertionError("test did not release deferred worker")
        return f"answer:{request}"

    def wait_started(self, count=1):
        with self.entered:
            if not self.entered.wait_for(lambda: self.started >= count, timeout=3):
                raise AssertionError("worker did not start")

    def finish(self):
        self.release.set()
        for call in self.calls:
            call.shutdown()


class DeferredJudge:
    plugin_id = "judge"

    def __init__(self, *, gate="lifecycle"):
        resource = GATES[gate]
        self.label = len(resource.calls)
        self._call = DeferredCall(resource.run, key=CALL_KEY)
        resource.calls.append(self._call)

    def propose(self, context):
        memory = context.shared_memory
        answer = self._call.poll(memory, now_ms=int(context.timestamp_ms), scope=0,
                                 about="one", request=lambda: self.label)
        if answer is not None:
            memory[ANSWERS_KEY] = [*memory.get(ANSWERS_KEY, []), answer.result]
        return InactiveProposal.propose(self, context)


class CompletingExecutor:
    plugin_id = "executor"

    def __init__(self, *, gate="lifecycle"):
        self.gate = GATES[gate]

    def propose(self, context):
        if self.gate.complete_during_executor:
            self.gate.finish()
        if self.gate.foreign_write:
            context.shared_memory[CALL_KEY] = {}
        if self.gate.foreign_take:
            self.gate.calls[0].take(context.shared_memory, scope=0)
        return InactiveProposal.propose(self, context)


class PolicyJudge:
    plugin_id = "judge"

    def __init__(self, *, revision=0):
        self.revision = revision
        self.cycle = PolicyCycle(POLICIES, initial="search", key="proposal.judge.policy", min_acting_ms=100)

    def propose(self, context):
        self.cycle.current(context.shared_memory, now_ms=int(context.timestamp_ms))
        choice = context.metadata.get("choose")
        if choice:
            self.cycle.choose(context.shared_memory, choice, now_ms=int(context.timestamp_ms))
        return InactiveProposal.propose(self, context)


class PolicyActor:
    plugin_id = "actor"

    def propose(self, context):
        policy = read_policy(context.shared_memory)
        return ActionProposal(
            plugin_id=self.plugin_id, frame_id=context.frame_id, lifecycle="fresh", freshness="fresh",
            available=True, reason="moving", confidence=1,
            command=ProposedVehicleCommand(throttle=1, steering=0),
            source_refs=(SourceRef(kind="memory_record", id="proposal.policy", frame_id=context.frame_id),),
            metadata={"policy": policy.tag()},
        )
