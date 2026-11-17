"""What a memory plugin learns, the predictions it opens, and how they were checked.

A ``Belief`` is a learned ``value``, the ``Trial`` predictions still open, and
the recent ``Check`` outcomes that tested it. The plugin decides what counts as
support. Samples and precision belong in ``value`` or ``properties``;
confidence counts checks, never samples.

``confidence`` is None until the first check, as for any unscored record (see
``autonomy/decision_cycle/confidence.py``). Then it is
``(support + 1) / (checks + 2)`` over the retained checks, so it never reaches
0 or 1. ``to_dict`` adds ``confidence``, ``by_method`` and ``corroborated`` for
viewers; ``from_dict`` checks them against the retained checks.

The plugin that declares a belief keeps it at ``publication.belief_key``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal

from autonomy.decision_cycle.memory.evidence import require_identifier, require_non_negative_int
from autonomy.serialization import canonical_json_utf8
from autonomy.fields import FieldError, require_choice, require_keys, require_nested, require_number

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
        require_choice(self.outcome, ("support", "contradict"), field_name="outcome")
        for name in ("predicted", "observed"):
            try:
                canonical_json_utf8(getattr(self, name))
            except ValueError as exc:
                raise FieldError(name, str(exc)) from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "at_ms": self.at_ms, "method": self.method, "outcome": self.outcome,
            "predicted": self.predicted, "observed": self.observed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Check:
        return cls(**require_keys(data, ("at_ms", "method", "outcome", "predicted", "observed")))


@dataclass(frozen=True)
class Trial:
    """A prediction a plugin opened and will resolve into a ``Check``."""

    trial_id: str
    method: str
    started_at_ms: int
    predicted: Any
    properties: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            canonical_json_utf8(self.predicted)
        except ValueError as exc:
            raise FieldError("predicted", str(exc)) from exc
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
        document = require_keys(data, ("trial_id", "method", "started_at_ms", "predicted", "properties"))
        if not isinstance(document["properties"], dict):
            raise FieldError("properties", "must be an object")
        return cls(**document)


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

    def __post_init__(self) -> None:
        try:
            canonical_json_utf8(self.value)
        except ValueError as exc:
            raise FieldError("value", str(exc)) from exc
        require_identifier(self.belief_id, field_name="belief_id")
        require_identifier(self.source_plugin_id, field_name="source_plugin_id")
        object.__setattr__(
            self, "updated_at_ms", require_non_negative_int(self.updated_at_ms, field_name="updated_at_ms"),
        )
        for name in ("checks", "trials"):
            if not isinstance(getattr(self, name), (list, tuple)):
                raise FieldError(name, "must be an array")
        object.__setattr__(self, "checks", tuple(self.checks))
        object.__setattr__(self, "trials", tuple(self.trials))
        if any(not isinstance(check, Check) for check in self.checks):
            raise FieldError("checks", "must contain Check values")
        if any(not isinstance(trial, Trial) for trial in self.trials):
            raise FieldError("trials", "must contain Trial values")
        if len({trial.trial_id for trial in self.trials}) != len(self.trials):
            raise FieldError("trials", "trial ids must be unique within a belief")

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

    def revised(
        self, value: Any, at_ms: int, *, properties: dict[str, Any] | None = None,
        keep_checks: bool = True,
    ) -> Belief:
        """Revise the value and optional properties, keeping open predictions."""
        return replace(self, value=value, updated_at_ms=max(self.updated_at_ms, at_ms),
                       properties=self.properties if properties is None else properties,
                       checks=self.checks if keep_checks else ())

    def opened(self, trial: Trial, max_trials: int) -> Belief:
        """Open or replace a prediction, dropping the oldest beyond ``max_trials``."""
        trials = tuple(item for item in self.trials if item.trial_id != trial.trial_id)
        return replace(self, trials=(*trials, trial)[-_bound(max_trials, "max_trials"):],
                       updated_at_ms=max(self.updated_at_ms, trial.started_at_ms))

    def withdrawn(self, trial_ids: tuple[str, ...] | None = None) -> Belief:
        """Drop selected predictions, or all of them, without scoring checks."""
        return replace(self, trials=tuple(
            trial for trial in self.trials if trial_ids is not None and trial.trial_id not in trial_ids
        ))

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
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "belief_id": self.belief_id, "value": self.value,
            "source_plugin_id": self.source_plugin_id, "updated_at_ms": self.updated_at_ms,
            "checks": [check.to_dict() for check in self.checks],
            "trials": [trial.to_dict() for trial in self.trials],
            "properties": self.properties, "confidence": self.confidence,
            "by_method": self.by_method, "corroborated": self.corroborated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Belief:
        document = require_keys(data, (
            "belief_id", "value", "source_plugin_id", "updated_at_ms", "checks", "trials",
            "properties", "confidence", "by_method", "corroborated",
        ))
        parsed = {}
        for name, parse in (("checks", Check.from_dict), ("trials", Trial.from_dict)):
            values = document[name]
            if not isinstance(values, (list, tuple)):
                raise FieldError(name, "must be an array")
            parsed[name] = tuple(require_nested(parse, value, field_name=f"{name}.{index}")
                                 for index, value in enumerate(values))
        if not isinstance(document["properties"], dict):
            raise FieldError("properties", "must be an object")
        belief = cls(
            belief_id=document["belief_id"], value=document["value"],
            source_plugin_id=document["source_plugin_id"], updated_at_ms=document["updated_at_ms"],
            properties=document["properties"], **parsed,
        )
        confidence = document["confidence"]
        if confidence is not None:
            confidence = require_number(confidence, field_name="confidence", bounds=(0, 1))
        if confidence != belief.confidence:
            raise FieldError("confidence", "must agree with checks")
        by_method = require_keys(document["by_method"], belief.by_method, field_name="by_method")
        for method, expected in belief.by_method.items():
            counts = require_keys(by_method[method], ("support", "contradict"), field_name=f"by_method.{method}")
            for outcome, count in expected.items():
                actual = require_non_negative_int(counts[outcome], field_name=f"by_method.{method}.{outcome}")
                if actual != count:
                    raise FieldError(f"by_method.{method}.{outcome}", "must agree with checks")
        if type(document["corroborated"]) is not bool or document["corroborated"] != belief.corroborated:
            raise FieldError("corroborated", "must agree with checks")
        return belief


def _bound(value: Any, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value
