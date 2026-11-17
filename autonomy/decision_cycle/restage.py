"""A selection change invalidates state from its first changed plugin onward."""

from __future__ import annotations

# pyright: basic

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from itertools import zip_longest
from typing import Generic, TypeVar, TypedDict, cast

from autonomy.decision_cycle.activation import STEPS, StepActivation, executable_document
from autonomy.shared_memory import SharedMemory

Runner = TypeVar("Runner")
Owner = tuple[str, str | None]
_HOST_KEYS = frozenset({
    "action.mode", "action.user_steering", "action.user_throttle", "action.control_applications",
})


def owner_of(key: str, belief_owners: Mapping[str, frozenset[Owner]]) -> frozenset[Owner]:
    """Owners of a key; an empty set denotes an unowned seed value.

    ``<step>.<plugin_id>.<name>`` is that plugin's state. ``<step>.<name>``,
    such as ``memory.evidence``, is a publication of the step, owned by
    ``(step, None)``.
    """
    if key in _HOST_KEYS:
        return frozenset({("host", None)})
    if key.startswith("memory.beliefs."):
        return belief_owners.get(key, frozenset())
    parts = key.split(".", 2)
    if len(parts) in (2, 3) and parts[0] in STEPS and all(parts):
        return frozenset({(parts[0], parts[1] if len(parts) == 3 else None)})
    return frozenset()


class SourceIdentity(TypedDict, total=False):
    host_name: str
    tree_sha256: str
    plugin_revisions: dict[str, str]


class ExecutableDocument(TypedDict):
    plugins: list[str]
    plugin_specs: dict[str, str]
    plugin_configs: dict[str, dict[str, object]]
    source: SourceIdentity


@dataclass(frozen=True)
class ExecutablePlugin:
    owner: Owner
    spec: str | None
    config: Mapping[str, object]
    source: tuple[str | None, str | None, str | None]


Activations = Mapping[str, StepActivation | None]


@dataclass(frozen=True)
class Restaged(Generic[Runner]):
    steps: dict[str, Runner | None]
    activations: Activations
    cleared: frozenset[Owner]
    belief_owners: Mapping[str, frozenset[Owner]] = field(repr=False)

    def keep(self, shared_memory: SharedMemory) -> SharedMemory:
        """Filter the map at adoption, including writes since construction."""
        return {
            key: value for key, value in shared_memory.items()
            if not owner_of(key, self.belief_owners) & self.cleared
        }


def restage(
    applied_steps: Mapping[str, Runner | None], applied: Activations, desired: Activations,
    *, build: Callable[[str, StepActivation], Runner],
) -> Restaged[Runner] | None:
    """Build changed steps without touching applied runners or their map.

    Reuse everything before the first changed executable plugin. Removed owners
    are always invalidated; step publications follow their producing plugins.
    Call ``keep`` at adoption so construction can happen off the frame boundary.
    """
    old_docs = {step: cast(ExecutableDocument | None, executable_document(applied.get(step))) for step in STEPS}
    new_docs = {step: cast(ExecutableDocument | None, executable_document(desired.get(step))) for step in STEPS}
    if old_docs == new_docs:
        return None
    old, new = _execution_order(old_docs), _execution_order(new_docs)
    first = next((i for i, pair in enumerate(zip_longest(old, new)) if pair[0] != pair[1]), len(old))
    cleared = {item.owner for item in old[first:]}
    new_owners = {item.owner for item in new}
    cleared.update(item.owner for item in old if item.owner not in new_owners)
    cleared.update((step, None) for step, _plugin_id in tuple(cleared))
    beliefs: dict[str, frozenset[Owner]] = {}
    for definition, plugin in getattr(applied_steps.get("memory"), "applied", ()):
        for belief_id in plugin.contract.belief_ids:
            key = f"memory.beliefs.{belief_id}"
            beliefs[key] = beliefs.get(key, frozenset()) | {("memory", definition.plugin_id)}
    steps = dict(applied_steps)
    for step in STEPS:
        if old_docs[step] != new_docs[step]:
            activation = desired.get(step)
            try:
                steps[step] = None if activation is None else build(step, activation)
            except Exception as exc:
                raise RuntimeError(f"could not load {step}: {type(exc).__name__}: {exc}") from exc
    return Restaged(steps, dict(desired), frozenset(cleared), beliefs)


def _execution_order(documents: Mapping[str, ExecutableDocument | None]) -> list[ExecutablePlugin]:
    return [
        ExecutablePlugin((step, plugin_id), document["plugin_specs"].get(plugin_id),
                         document["plugin_configs"].get(plugin_id, {}),
                         (document["source"].get("host_name"), document["source"].get("tree_sha256"),
                          document["source"].get("plugin_revisions", {}).get(plugin_id)))
        for step in STEPS if (document := documents[step]) is not None
        for plugin_id in document["plugins"]
    ]
