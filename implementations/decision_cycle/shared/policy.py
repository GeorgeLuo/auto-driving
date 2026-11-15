"""Named policies, explicit executor exits, and the plan that follows them.

A policy definition has ``when``, ``goal``, optional ``how``, and ordered
``executors`` (proposal plugin IDs). Configure a ``PolicyCycle`` on the judge
plugin; call ``current`` before taking an epoch-scoped answer, then ``choose``
and ask under the returned epoch. ``current`` consumes listed executors' exits,
leaving the policy undecided until the next choice. Instances hold config only.

Shared-memory keys and ownership:

* ``proposal.<cycler_id>.policy`` (default ``proposal.policy_cycler.policy``):
  the cycler's state: ``name``, ``epoch``, ``since_ms``, ``reason``, ``policies``,
  last ten ``changes``, and ``held``. Pass ``key`` for a differently named judge.
* ``proposal.policy``: rewritten from that state on every current/choose call.
  Executors and the plan read this publication, never the cycler's private key.
* ``proposal.<executor_id>.exit``: that executor writes once per epoch through
  ``write_exit``; it, the cycler and plan read ``epoch, why, at_ms, detail``.
* ``plan.<selector_id>.state``: the plan's ``runs`` (last forty), rewritten to
  ``plan.action_log`` each frame. The cycler, executors and prompt builders read
  the published log. Seed/reset the producer's state, not the publication.

Executors use ``read_policy``, ``assigns``, ``read_exit`` and ``write_exit``;
tag proposals with ``policy.tag()``. The host arms ``plan/follow_policy`` after
uploading the collection. No plan config duplicates the policy definitions.
An exit stops selection that frame, handing control up to the cycler next frame.
The log describes commands selected at the plan interface, without a separate
actuator acknowledgement. Open runs have ``end_ms=None``; idle closes them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.plan.values import ActionPlan, PlanContribution
from autonomy.decision_cycle.proposal.values import ActionProposal
from autonomy.shared_memory import SharedMemory

POLICY_KEY = "proposal.policy"
ACTION_LOG_KEY = "plan.action_log"
EXIT_REASONS = frozenset({"done", "lost", "blocked", "timeout"})


@dataclass(frozen=True)
class Policy:
    """A detached current policy; an undecided policy has name None."""

    name: str | None
    epoch: int
    since_ms: int
    reason: str
    definition: dict

    @property
    def executors(self) -> tuple[str, ...]:
        return tuple(self.definition.get("executors", ()))

    def assigns(self, plugin_id: str) -> bool:
        return plugin_id in self.executors

    def tag(self) -> dict:
        return {"name": self.name, "epoch": self.epoch}


def _policy(record: dict) -> Policy:
    return Policy(record["name"], record["epoch"], record["since_ms"], record["reason"],
                  deepcopy(record["policies"].get(record["name"], {})))


def read_policy(shared_memory: SharedMemory) -> Policy | None:
    """Read the published policy, or None before the cycler's first frame."""
    record = shared_memory.get(POLICY_KEY)
    return None if record is None else _policy(record)


def read_exit(shared_memory: SharedMemory, plugin_id: str, policy: Policy | None) -> dict | None:
    """Read only this policy epoch's exit, detached from shared memory."""
    event = shared_memory.get(f"proposal.{plugin_id}.exit")
    if policy is None or not event or event["epoch"] != policy.epoch:
        return None
    return deepcopy(event)


def write_exit(
    shared_memory: SharedMemory, plugin_id: str, policy: Policy, *,
    why: str, now_ms: int, detail: dict | None = None,
) -> dict:
    """Publish a sticky exit: the first exit in an epoch wins."""
    if why not in EXIT_REASONS:
        raise ValueError("exit why must be done, lost, blocked or timeout")
    event = read_exit(shared_memory, plugin_id, policy)
    if event is None:
        event = {"epoch": policy.epoch, "why": why, "at_ms": now_ms,
                 "detail": deepcopy({} if detail is None else detail)}
        shared_memory[f"proposal.{plugin_id}.exit"] = deepcopy(event)
    return event


def _definitions(policies: object) -> dict:
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
    return result


class PolicyCycle:
    """Judge-independent transitions; history belongs to the supplied memory.

    A different choice is counted and discarded until the earliest non-zero
    run of this epoch is min_acting_ms old. This is elapsed frame time from
    that run, including any gaps, not a sum of run durations. Even a zero
    minimum requires a real run. Undecided policies accept a choice at once.
    """

    def __init__(
        self, policies: Mapping[str, dict], *, initial: str, min_acting_ms: int = 1000,
        key: str = "proposal.policy_cycler.policy",
    ) -> None:
        self._policies = _definitions(policies)
        if initial not in self._policies:
            raise ValueError("initial must be a configured policy")
        if type(min_acting_ms) is not int or min_acting_ms < 0:
            raise ValueError("min_acting_ms must be a non-negative int")
        self._initial, self._min_acting_ms, self._key = initial, min_acting_ms, key

    def _state(self, shared_memory: SharedMemory, now_ms: int) -> dict:
        state = deepcopy(shared_memory.get(self._key))
        if state is None:
            state = {"name": self._initial, "epoch": 0, "since_ms": now_ms, "reason": "initial",
                     "changes": [], "held": 0}
        state["policies"] = deepcopy(self._policies)
        return state

    def _publish(self, shared_memory: SharedMemory, state: dict) -> Policy:
        shared_memory[self._key] = deepcopy(state)
        shared_memory[POLICY_KEY] = deepcopy(state)
        return _policy(state)

    @staticmethod
    def _change(state: dict, name: str | None, now_ms: int, reason: str) -> None:
        change = {"from": state["name"], "to": name, "epoch": state["epoch"] + 1,
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
        state = self._state(shared_memory, now_ms)
        if name != state["name"]:
            if state["name"] is None or self._acted(shared_memory, state["epoch"], now_ms):
                self._change(state, name, now_ms, reason)
            else:
                state["held"] += 1
        return self._publish(shared_memory, state)

    def held(self, shared_memory: SharedMemory) -> int:
        return int((shared_memory.get(self._key) or {}).get("held", 0))

    def _acted(self, shared_memory: SharedMemory, epoch: int, now_ms: int) -> bool:
        starts = [run["start_ms"] for run in shared_memory.get(ACTION_LOG_KEY) or []
                  if run.get("epoch") == epoch and any(float(v) != 0.0 for v in run.get("command") or ())]
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
                         and dict(c.metadata.get("policy") or {}) == policy.tag()), None)
        if selected is not None:
            return selected
    return None


def _record_run(
    shared_memory: SharedMemory, selector_id: str, policy: Policy | None,
    selected: ActionProposal | None, now_ms: int,
) -> None:
    state_key = f"plan.{selector_id}.state"
    runs = deepcopy((shared_memory.get(state_key) or {}).get("runs", []))
    entry = None
    if selected is not None and selected.command is not None and policy is not None:
        entry = {"by": selected.plugin_id, "policy": policy.name, "epoch": policy.epoch,
                 "command": [selected.command.throttle, selected.command.steering],
                 "start_ms": now_ms, "end_ms": None}
    previous = runs[-1] if runs else None
    unchanged = entry is not None and previous is not None and previous["end_ms"] is None and all(
        previous[k] == entry[k] for k in ("by", "policy", "epoch", "command"))
    if not unchanged:
        if previous is not None and previous["end_ms"] is None:
            previous["end_ms"] = now_ms
        if entry is not None:
            runs.append(entry)
    shared_memory[state_key] = {"runs": deepcopy(runs[-40:])}
    shared_memory[ACTION_LOG_KEY] = runs[-40:]


def follow_policy_plan(
    context: DecisionFrameContext, candidates: Sequence[ActionProposal], *, selector_id: str = "follow_policy",
) -> ActionPlan:
    """Select the first active assigned candidate, close/split/log its run."""
    memory, now = context.shared_memory, int(context.timestamp_ms)
    if memory is None:
        raise ValueError("follow_policy requires shared memory")
    policy = read_policy(memory)
    selected = _select(memory, policy, context.frame_id, candidates)
    _record_run(memory, selector_id, policy, selected, now)
    if selected is None:
        return ActionPlan(frame_id=context.frame_id, timestamp_ms=now, status="idle",
                          candidates=tuple(candidates), selector_id=selector_id)
    # Selected plans must repeat the candidate's reason and confidence. Name the
    # executor on a detached candidate copy until the host carries its identity.
    selected = replace(selected, reason=f"{selected.plugin_id}:{selected.reason}",
                       metadata=selected.to_dict()["metadata"])
    annotated = tuple(selected if c.plugin_id == selected.plugin_id else c for c in candidates)
    return ActionPlan(
        frame_id=context.frame_id, timestamp_ms=now, status="selected", candidates=annotated,
        selector_id=selector_id, selected_proposal_id=selected.proposal_id,
        contributions=(PlanContribution(proposal_id=selected.proposal_id, plugin_id=selected.plugin_id),),
        command=selected.command, reason=selected.reason, confidence=selected.confidence,
        metadata={"policy": policy.tag()} if policy is not None else {})
