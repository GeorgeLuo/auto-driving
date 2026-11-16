"""Named policies, explicit executor exits, and the plan that follows them.

A policy definition has ``when``, ``goal``, optional ``how``, and ordered
``executors`` (proposal plugin IDs). Configure a ``PolicyCycle`` on the judge
plugin; call ``current`` before taking an answer scoped by ``policy.tag()``, then ``choose``
and ask under the returned tag. ``current`` consumes listed executors' exits,
leaving the policy undecided until the next choice. Instances hold config only.
The serialized state owns a lifetime token, created only when that state is absent. Restoring a recording seed preserves the token;
a replay without that state starts a new lifetime and ignores earlier controls.

Shared-memory keys and ownership:

* ``proposal.<cycler_id>.policy`` (default ``proposal.policy_cycler.policy``):
  the cycler's state: ``name``, ``epoch``, ``since_ms``, ``reason``, ``policies``,
  last ten ``changes``, and ``held``. Pass ``key`` for a differently named judge.
* ``proposal.policy``: rewritten from that state on every current/choose call.
  Executors and the plan read this publication, never the cycler's private key.
* ``proposal.<executor_id>.exit``: that executor writes once per identity through
  ``write_exit``; it, the cycler and plan read ``policy, why, at_ms, detail``.
* ``memory.control_runs``: the packaged ``memory/control_runs`` plugin's
  applied runs. The cycler measures its acting floor from this publication;
  executors and prompt builders can read it through ``read_control_runs``.
  See ``shared/control_runs.py`` for state, retention and clock rules.

Executors use ``read_policy``, ``assigns``, ``read_exit`` and ``write_exit``;
tag proposals with ``policy.tag()``. After uploading the collection, arm both
``plan/follow_policy`` by preset and ``memory/control_runs`` by name (appending
memory without replacing the collection's plugins). No plan config duplicates
the policy definitions. An exit stops selection that frame, handing control up
to the cycler next frame. The plan only selects; it writes no history and keeps
the candidate's reason, confidence, command and metadata unchanged.
"""

from __future__ import annotations

# pyright: basic

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import NotRequired, TypedDict, cast
from uuid import uuid4

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.plan.values import ActionPlan, PlanContribution
from autonomy.decision_cycle.proposal.values import ActionProposal
from autonomy.shared_memory import SharedMemory
from implementations.decision_cycle.shared.control_runs import ControlRun, read_control_runs

POLICY_KEY = "proposal.policy"
EXIT_REASONS = frozenset({"done", "lost", "blocked", "timeout"})


class PolicyDefinition(TypedDict):
    when: str
    goal: str
    how: NotRequired[str]
    executors: list[str] | tuple[str, ...]


class PolicyTag(TypedDict):
    lifetime: str
    name: str | None
    epoch: int


# Functional syntax keeps the JSON spelling of the reserved word 'from'.
PolicyChange = TypedDict("PolicyChange", {"from": str | None, "to": str | None, "epoch": int,
                                         "at_ms": int, "reason": str})


class PolicyState(PolicyTag):
    since_ms: int
    reason: str
    policies: dict[str, PolicyDefinition]
    changes: list[PolicyChange]
    held: int


class PolicyExit(TypedDict):
    policy: PolicyTag
    why: str
    at_ms: int
    detail: dict[str, object]


@dataclass(frozen=True)
class Policy:
    """A detached current policy; an undecided policy has name None."""

    name: str | None
    epoch: int
    since_ms: int
    reason: str
    definition: PolicyDefinition | dict[str, object]
    lifetime: str

    @property
    def executors(self) -> tuple[str, ...]:
        return tuple(cast(Sequence[str], self.definition.get("executors", ())))

    def assigns(self, plugin_id: str) -> bool:
        return plugin_id in self.executors

    def tag(self) -> PolicyTag:
        return {"lifetime": self.lifetime, "name": self.name, "epoch": self.epoch}


def _policy(record: PolicyState) -> Policy:
    return Policy(record["name"], record["epoch"], record["since_ms"], record["reason"],
                  deepcopy(record["policies"].get(record["name"], {})) if record["name"] is not None else {},
                  record["lifetime"])


def read_policy(shared_memory: SharedMemory) -> Policy | None:
    """Read the published policy, or None before the cycler's first frame."""
    record = cast(PolicyState | None, shared_memory.get(POLICY_KEY))
    return None if record is None else _policy(record)


def read_exit(shared_memory: SharedMemory, plugin_id: str, policy: Policy | None) -> PolicyExit | None:
    """Read only this policy epoch's exit, detached from shared memory."""
    event = cast(PolicyExit | None, shared_memory.get(f"proposal.{plugin_id}.exit"))
    if policy is None or not event or event.get("policy") != policy.tag():
        return None
    return deepcopy(event)


def write_exit(
    shared_memory: SharedMemory, plugin_id: str, policy: Policy, *,
    why: str, now_ms: int, detail: dict[str, object] | None = None,
) -> PolicyExit:
    """Publish a sticky exit: the first exit in an epoch wins."""
    if why not in EXIT_REASONS:
        raise ValueError("exit why must be done, lost, blocked or timeout")
    event: PolicyExit | None = read_exit(shared_memory, plugin_id, policy)
    if event is None:
        event = {"policy": policy.tag(), "why": why, "at_ms": now_ms,
                 "detail": deepcopy({} if detail is None else detail)}
        shared_memory[f"proposal.{plugin_id}.exit"] = deepcopy(event)
    return event


def _definitions(policies: object) -> dict[str, PolicyDefinition]:
    if not isinstance(policies, Mapping) or not policies:
        raise ValueError("policies must be a non-empty mapping")
    result = deepcopy(dict(policies))
    for name, rule in result.items():
        if not isinstance(name, str) or not name or not isinstance(rule, dict):
            raise ValueError("each policy needs a name and definition")
        if any(not isinstance(rule.get(k), str) or not rule[k] for k in ("when", "goal")):
            raise ValueError("each policy needs when and goal")
        executors = rule.get("executors")
        if not isinstance(executors, (list, tuple)) or any(not isinstance(e, str) or not e for e in executors):
            raise ValueError("executors must list proposal plugin IDs")
        if "how" in rule and (not isinstance(rule["how"], str) or not rule["how"]):
            raise ValueError("how must be non-empty when supplied")
    return cast(dict[str, PolicyDefinition], result)


class PolicyCycle:
    """Judge-independent transitions; history belongs to the supplied memory.

    A different choice is counted and discarded until the earliest non-zero
    run of this epoch is min_acting_ms old. This is elapsed frame time from
    that run, including any gaps, not a sum of run durations. Even a zero
    minimum requires an applied run. Undecided policies accept a choice at once.
    Choices require ``memory/control_runs`` to be armed. Frame timestamps and
    application delivery times are host wall-clock milliseconds on current
    hosts; a backwards clock step can delay the floor until the clock catches up.
    """

    def __init__(
        self, policies: Mapping[str, object], *, initial: str, min_acting_ms: int = 1000,
        key: str = "proposal.policy_cycler.policy",
    ) -> None:
        self._policies = _definitions(policies)
        if initial not in self._policies:
            raise ValueError("initial must be a configured policy")
        if type(min_acting_ms) is not int or min_acting_ms < 0:
            raise ValueError("min_acting_ms must be a non-negative int")
        self._initial, self._min_acting_ms, self._key = initial, min_acting_ms, key

    def _state(self, shared_memory: SharedMemory, now_ms: int) -> PolicyState:
        state: PolicyState | None = deepcopy(cast(PolicyState | None, shared_memory.get(self._key)))
        if state is None:
            state = {"lifetime": uuid4().hex, "policies": {}, "name": self._initial, "epoch": 0, "since_ms": now_ms, "reason": "initial",
                     "changes": [], "held": 0}
        state["policies"] = deepcopy(self._policies)
        return state

    def _publish(self, shared_memory: SharedMemory, state: PolicyState) -> Policy:
        shared_memory[self._key] = deepcopy(state)
        shared_memory[POLICY_KEY] = deepcopy(state)
        return _policy(state)

    @staticmethod
    def _change(state: PolicyState, name: str | None, now_ms: int, reason: str) -> None:
        change: PolicyChange = {"from": state["name"], "to": name, "epoch": state["epoch"] + 1,
                  "at_ms": now_ms, "reason": reason}
        state["changes"] = [*state["changes"], change][-10:]
        state.update(name=name, epoch=change["epoch"], since_ms=now_ms, reason=reason)

    def current(self, shared_memory: SharedMemory, *, now_ms: int) -> Policy:
        """Publish the initial/current policy, consuming a current listed exit."""
        state = self._state(shared_memory, now_ms)
        policy = _policy(state)
        for executor in policy.executors:
            event = read_exit(shared_memory, executor, policy)
            if event is not None:
                self._change(state, None, now_ms, f"exit:{executor}:{event['why']}")
                break
        return self._publish(shared_memory, state)

    def choose(
        self, shared_memory: SharedMemory, name: str, *, now_ms: int, reason: str = "cycler",
    ) -> Policy:
        """Apply a judge's choice, or count a different choice held by the floor."""
        if name not in self._policies:
            raise ValueError("choice must be a configured policy")
        runs = read_control_runs(shared_memory)
        if runs is None:
            raise ValueError("PolicyCycle choices require applied runs; arm memory/control_runs")
        state = self._state(shared_memory, now_ms)
        if name != state["name"]:
            if state["name"] is None or self._acted(runs, _policy(state), now_ms):
                self._change(state, name, now_ms, reason)
            else:
                state["held"] += 1
        return self._publish(shared_memory, state)

    def held(self, shared_memory: SharedMemory) -> int:
        state = cast(PolicyState | None, shared_memory.get(self._key))
        return 0 if state is None else state["held"]

    def _acted(self, runs: Sequence[ControlRun], policy: Policy, now_ms: int) -> bool:
        starts = [run["start_ms"] for run in runs
                  if run["plan"].get("policy") == policy.tag()
                  and run["by"] in policy.executors
                  and any(float(v) != 0.0 for v in run["command"])]
        return bool(starts) and now_ms - min(starts) >= self._min_acting_ms


def _select(
    shared_memory: SharedMemory, policy: Policy | None, frame_id: str, candidates: Sequence[ActionProposal],
) -> ActionProposal | None:
    if policy is None or policy.name is None:
        return None
    if any(read_exit(shared_memory, executor, policy) is not None for executor in policy.executors):
        return None
    for executor in policy.executors:
        selected = next((c for c in candidates if c.plugin_id == executor and c.is_active(frame_id)
                         and c.metadata.get("policy") == policy.tag()), None)
        if selected is not None:
            return selected
    return None


def follow_policy_plan(
    context: DecisionFrameContext, candidates: Sequence[ActionProposal], *, selector_id: str = "follow_policy",
) -> ActionPlan:
    """Select the first active assigned candidate without changing it or memory."""
    memory, now = context.shared_memory, int(context.timestamp_ms)
    if memory is None:
        raise ValueError("follow_policy requires shared memory")
    policy = read_policy(memory)
    selected = _select(memory, policy, context.frame_id, candidates)
    if selected is None:
        return ActionPlan(frame_id=context.frame_id, timestamp_ms=now, status="idle",
                          candidates=tuple(candidates), selector_id=selector_id)
    return ActionPlan(
        frame_id=context.frame_id, timestamp_ms=now, status="selected", candidates=tuple(candidates),
        selector_id=selector_id, selected_proposal_id=selected.proposal_id,
        contributions=(PlanContribution(proposal_id=selected.proposal_id, plugin_id=selected.plugin_id),),
        command=selected.command, reason=selected.reason, confidence=selected.confidence,
        metadata={"policy": policy.tag()} if policy is not None else {})
