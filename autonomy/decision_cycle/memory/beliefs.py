"""What a memory plugin learns, the predictions it opens, and how they were checked.

A ``Belief`` is a learned ``value``, the ``Trial`` predictions still open, and
the recent ``Check`` outcomes that tested it. The plugin decides what counts as
support. Samples and precision belong in ``value`` or ``properties``;
confidence counts checks, never samples.

``confidence`` is None until the first check, as for any unscored record (see
``autonomy/decision_cycle/confidence.py``). Then it is
``(support + 1) / (checks + 2)`` over the retained checks, so it never reaches
0 or 1. ``to_dict`` adds ``confidence``, ``by_method`` and ``corroborated`` for
viewers; ``from_dict`` derives them again rather than reading them.

Trials closed without a check (``withdrawn``, or displaced past the trial bound)
and checks dropped before they age out (``discarded``) are counted for the
belief's lifetime in ``withdrawals`` and ``discards``, as
``{method: {reason: count}}``, so a viewer can tell why learning stalled or
restarted.

The plugin that declares a belief keeps it at ``publication.belief_key``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal

from autonomy.decision_cycle.memory.evidence import require_identifier, require_non_negative_int
from autonomy.serialization import canonical_json_utf8

Outcome = Literal["support", "contradict"]


@dataclass(frozen=True)
class Check:
    """One test of a belief: what it predicted, what was observed, and whether that held."""

    at_ms: int
    method: str
    outcome: Outcome
    predicted: Any = None
    observed: Any = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "at_ms", require_non_negative_int(self.at_ms, field_name="at_ms"))
        require_identifier(self.method, field_name="method")
        if self.outcome not in ("support", "contradict"):
            raise ValueError("outcome must be support or contradict")

    def to_dict(self) -> dict[str, Any]:
        return {
            "at_ms": self.at_ms, "method": self.method, "outcome": self.outcome,
            "predicted": self.predicted, "observed": self.observed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Check:
        return cls(**data)


@dataclass(frozen=True)
class Trial:
    """A prediction a plugin opened and will resolve into a ``Check``."""

    trial_id: str
    method: str
    started_at_ms: int
    predicted: Any
    properties: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_identifier(self.trial_id, field_name="trial_id")
        require_identifier(self.method, field_name="method")
        object.__setattr__(
            self, "started_at_ms", require_non_negative_int(self.started_at_ms, field_name="started_at_ms"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id, "method": self.method, "started_at_ms": self.started_at_ms,
            "predicted": self.predicted, "properties": self.properties,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Trial:
        return cls(**data)


@dataclass(frozen=True)
class Belief:
    """A learned value of JSON data, its open predictions, and recent checks."""

    belief_id: str
    value: Any
    source_plugin_id: str
    updated_at_ms: int
    checks: tuple[Check, ...] = ()
    trials: tuple[Trial, ...] = ()
    properties: dict[str, Any] = field(default_factory=dict)
    withdrawals: dict[str, dict[str, int]] = field(default_factory=dict)
    discards: dict[str, dict[str, int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        canonical_json_utf8(self.value)
        require_identifier(self.belief_id, field_name="belief_id")
        require_identifier(self.source_plugin_id, field_name="source_plugin_id")
        object.__setattr__(
            self, "updated_at_ms", require_non_negative_int(self.updated_at_ms, field_name="updated_at_ms"),
        )
        object.__setattr__(self, "checks", tuple(self.checks))
        object.__setattr__(self, "trials", tuple(self.trials))
        if any(not isinstance(check, Check) for check in self.checks):
            raise TypeError("checks must contain Check values")
        if any(not isinstance(trial, Trial) for trial in self.trials):
            raise TypeError("trials must contain Trial values")
        if len({trial.trial_id for trial in self.trials}) != len(self.trials):
            raise ValueError("trial ids must be unique within a belief")

    @property
    def confidence(self) -> float | None:
        if not self.checks:
            return None
        return (sum(check.outcome == "support" for check in self.checks) + 1) / (len(self.checks) + 2)

    @property
    def by_method(self) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = {}
        for check in self.checks:
            counts.setdefault(check.method, {"support": 0, "contradict": 0})[check.outcome] += 1
        return counts

    @property
    def corroborated(self) -> bool:
        return sum(counts["support"] > counts["contradict"]
                   for counts in self.by_method.values()) >= 2

    def revised(self, value: Any, at_ms: int, *, properties: dict[str, Any] | None = None) -> Belief:
        """Revise the value and optional properties, keeping checks and open predictions."""
        return replace(self, value=value, updated_at_ms=max(self.updated_at_ms, at_ms),
                       properties=self.properties if properties is None else properties)

    def opened(self, trial: Trial, max_trials: int) -> Belief:
        """Open or replace a prediction, withdrawing the oldest beyond ``max_trials`` as displaced."""
        trials = (*(item for item in self.trials if item.trial_id != trial.trial_id), trial)
        kept = trials[-_bound(max_trials, "max_trials"):]
        return replace(self, trials=kept, updated_at_ms=max(self.updated_at_ms, trial.started_at_ms),
                       withdrawals=_tally(self.withdrawals, trials[:len(trials) - len(kept)], "displaced"))

    def withdrawn(self, trial_ids: tuple[str, ...] | None = None, *, reason: str) -> Belief:
        """Drop selected predictions, or all of them, without scoring, counted under ``reason``."""
        require_identifier(reason, field_name="reason")
        dropped = tuple(trial for trial in self.trials if trial_ids is None or trial.trial_id in trial_ids)
        if not dropped:
            return self
        return replace(self, trials=tuple(trial for trial in self.trials if trial not in dropped),
                       withdrawals=_tally(self.withdrawals, dropped, reason))

    def discarded(self, reason: str) -> Belief:
        """Drop every retained check, counted under ``reason``, when they no longer test the value."""
        require_identifier(reason, field_name="reason")
        if not self.checks:
            return self
        return replace(self, checks=(), discards=_tally(self.discards, self.checks, reason))

    def checked(self, check: Check, max_checks: int) -> Belief:
        """Record a check, dropping the oldest beyond ``max_checks``."""
        return replace(self, checks=(*self.checks, check)[-_bound(max_checks, "max_checks"):],
                       updated_at_ms=max(self.updated_at_ms, check.at_ms))

    def resolved(
        self, trial_id: str, outcome: Outcome, observed: Any, at_ms: int, max_checks: int,
    ) -> Belief:
        """Close an open prediction into a check by its method."""
        trial = next((trial for trial in self.trials if trial.trial_id == trial_id), None)
        if trial is None:
            raise ValueError(f"unknown trial id {trial_id!r}")
        if at_ms < trial.started_at_ms:
            raise ValueError("a trial cannot resolve before it starts")
        checked = self.checked(Check(at_ms, trial.method, outcome, trial.predicted, observed), max_checks)
        return replace(checked, trials=tuple(item for item in self.trials if item.trial_id != trial_id))

    def summary(self) -> dict[str, Any]:
        """The learned value and confidence for a per-frame report."""
        return {
            "value": self.value, "source_plugin_id": self.source_plugin_id,
            "updated_at_ms": self.updated_at_ms, "confidence": self.confidence,
            "by_method": self.by_method, "corroborated": self.corroborated,
            "open_trials": len(self.trials),
            "withdrawals": self.withdrawals, "discards": self.discards,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "belief_id": self.belief_id, "value": self.value,
            "source_plugin_id": self.source_plugin_id, "updated_at_ms": self.updated_at_ms,
            "checks": [check.to_dict() for check in self.checks],
            "trials": [trial.to_dict() for trial in self.trials],
            "properties": self.properties, "confidence": self.confidence,
            "by_method": self.by_method, "corroborated": self.corroborated,
            "withdrawals": self.withdrawals, "discards": self.discards,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Belief:
        return cls(
            belief_id=data["belief_id"], value=data["value"],
            source_plugin_id=data["source_plugin_id"], updated_at_ms=data["updated_at_ms"],
            checks=tuple(Check.from_dict(item) for item in data["checks"]),
            trials=tuple(Trial.from_dict(item) for item in data["trials"]),
            properties=data["properties"],
            withdrawals=data["withdrawals"], discards=data["discards"],
        )


def _tally(
    counts: dict[str, dict[str, int]], items: tuple[Check | Trial, ...], reason: str,
) -> dict[str, dict[str, int]]:
    """Count ``items`` under their method and ``reason``, without changing ``counts``."""
    tallied = {method: dict(reasons) for method, reasons in counts.items()}
    for item in items:
        reasons = tallied.setdefault(item.method, {})
        reasons[reason] = reasons.get(reason, 0) + 1
    return tallied


def _bound(value: Any, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value
