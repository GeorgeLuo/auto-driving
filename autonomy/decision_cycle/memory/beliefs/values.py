"""Learned values, open predictions, and bounded checks of their truth.

Plugins decide what supports a hypothesis. Samples and precision belong in
``value`` or ``properties``; confidence counts checks, never samples.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Literal


Outcome = Literal["support", "contradict"]


def _identifier(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _timestamp(value: Any, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


def _json_value(value: Any) -> Any:
    """Detach and normalize JSON data; reject objects and nonfinite numbers."""
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("belief data must be JSON serializable and finite") from exc


def _properties(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("properties must be an object with string keys")
    return _json_value(value)


@dataclass(frozen=True)
class Check:
    at_ms: int
    method: str
    outcome: Outcome
    predicted: Any = None
    observed: Any = None

    def __post_init__(self) -> None:
        _timestamp(self.at_ms, "at_ms")
        _identifier(self.method, "method")
        if self.outcome not in ("support", "contradict"):
            raise ValueError("outcome must be support or contradict")
        object.__setattr__(self, "predicted", _json_value(self.predicted))
        object.__setattr__(self, "observed", _json_value(self.observed))

    def to_dict(self) -> dict[str, Any]:
        return _json_value({
            "at_ms": self.at_ms, "method": self.method, "outcome": self.outcome,
            "predicted": self.predicted, "observed": self.observed,
        })

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Check:
        return cls(**data)


@dataclass(frozen=True)
class Trial:
    trial_id: str
    method: str
    started_at_ms: int
    predicted: Any
    properties: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _identifier(self.trial_id, "trial_id")
        _identifier(self.method, "method")
        _timestamp(self.started_at_ms, "started_at_ms")
        object.__setattr__(self, "predicted", _json_value(self.predicted))
        object.__setattr__(self, "properties", _properties(self.properties))

    def to_dict(self) -> dict[str, Any]:
        return _json_value({
            "trial_id": self.trial_id, "method": self.method,
            "started_at_ms": self.started_at_ms, "predicted": self.predicted,
            "properties": self.properties,
        })

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Trial:
        return cls(**data)


@dataclass(frozen=True)
class Belief:
    belief_id: str
    value: Any
    source_plugin_id: str
    updated_at_ms: int
    checks: tuple[Check, ...] = ()
    trials: tuple[Trial, ...] = ()
    properties: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _identifier(self.belief_id, "belief_id")
        _identifier(self.source_plugin_id, "source_plugin_id")
        _timestamp(self.updated_at_ms, "updated_at_ms")
        if any(not isinstance(check, Check) for check in self.checks):
            raise TypeError("checks must contain Check values")
        if any(not isinstance(trial, Trial) for trial in self.trials):
            raise TypeError("trials must contain Trial values")
        if len({trial.trial_id for trial in self.trials}) != len(self.trials):
            raise ValueError("trial ids must be unique within a belief")
        object.__setattr__(self, "value", _json_value(self.value))
        object.__setattr__(self, "checks", tuple(self.checks))
        object.__setattr__(self, "trials", tuple(self.trials))
        object.__setattr__(self, "properties", _properties(self.properties))

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

    def revised(self, value: Any, at_ms: int, keep_checks: bool) -> Belief:
        """Revise the hypothesis, retiring open predictions of its old value."""
        return replace(self, value=value, updated_at_ms=at_ms,
                       checks=self.checks if keep_checks else (), trials=())

    def opened(self, trial: Trial) -> Belief:
        return replace(self, trials=(*self.trials, trial), updated_at_ms=trial.started_at_ms)

    def checked(self, check: Check, max_checks: int) -> Belief:
        if type(max_checks) is not int or max_checks < 1:
            raise ValueError("max_checks must be a positive integer")
        return replace(self, checks=(*self.checks, check)[-max_checks:], updated_at_ms=check.at_ms)

    def resolved(
        self, trial_id: str, outcome: Outcome, observed: Any, at_ms: int, max_checks: int,
    ) -> Belief:
        trial = next((trial for trial in self.trials if trial.trial_id == trial_id), None)
        if trial is None:
            raise ValueError(f"unknown trial id {trial_id!r}")
        if at_ms < trial.started_at_ms:
            raise ValueError("a trial cannot resolve before it starts")
        checked = self.checked(Check(at_ms, trial.method, outcome, trial.predicted, observed), max_checks)
        return replace(checked, trials=tuple(item for item in self.trials if item.trial_id != trial_id))

    def to_dict(self) -> dict[str, Any]:
        return {
            "belief_id": self.belief_id, "value": _json_value(self.value),
            "source_plugin_id": self.source_plugin_id, "updated_at_ms": self.updated_at_ms,
            "checks": [check.to_dict() for check in self.checks],
            "trials": [trial.to_dict() for trial in self.trials],
            "properties": _json_value(self.properties), "confidence": self.confidence,
            "by_method": self.by_method, "corroborated": self.corroborated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Belief:
        return cls(
            belief_id=data["belief_id"], value=data["value"],
            source_plugin_id=data["source_plugin_id"], updated_at_ms=data["updated_at_ms"],
            checks=tuple(Check.from_dict(item) for item in data.get("checks", ())),
            trials=tuple(Trial.from_dict(item) for item in data.get("trials", ())),
            properties=data.get("properties", {}),
        )
