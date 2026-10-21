"""A bounded history of how memory beliefs and plugin state change over a run.

The runtime host's status carries every published belief's ``to_dict()`` and
every memory plugin's record under ``steps.memory``, but only as they are now.
``LearningHistory`` ingests that status as the CLI polls it and keeps, for each
belief and each plugin, a series for every leaf that changes, the checks as
they arrive, and each trial from when it is first seen until it ends. The
runtime view's Learning page reads it through ``payload``.

Times are the host's frame clock, ``last_cycle.context.timestamp_ms``. A status
whose frame time has not moved is skipped, so polling faster than frames adds
nothing. A frame time that goes back, or a rise in the runner's
``reset_count``, is a ``reset`` event. Every row carries ``seq``, which rises
with each row, so a reader asks only for the rows after the last it saw.

Leaves are flattened the same way for every belief and plugin, keyed by JSON
Pointer paths: numbers are line series, booleans are 0 or 1, strings are state
lanes, a list adds only its length as ``path#len``, and None and ``*_at_ms``
leaves are skipped. A point is added only when a leaf changes, and a leaf that
disappears adds None. Each belief or plugin keeps at most ``max_paths`` series
and counts the others in ``dropped_paths``.

Trials are seen only in snapshots, so a trial opened and closed between two
polls is missed; its withdrawal or check still shows in the counts. A trial
that leaves with a new check of its method and prediction is ``resolved`` with
that check's outcome. Any other that leaves while its method's withdrawal
count rises is ``withdrawn`` under the reason that rose, and the rest are
``resolved`` with no known outcome.
"""

from __future__ import annotations

import json
import math
import threading
import uuid
from collections import Counter, OrderedDict, deque
from typing import Any


LEARNING_HISTORY_SCHEMA = "automa_learning_history_v1"
BELIEF_FIELDS = (
    "value", "properties", "confidence", "by_method", "corroborated",
    "withdrawals", "discards", "checks", "trials",
)
PLUGIN_FIELDS = ("state", "failure_count", "last_duration_ms", "last_error")


class LearningHistory:
    """Change-only history of the memory step's beliefs and plugins."""

    def __init__(self, *, max_points: int = 1000, max_paths: int = 512) -> None:
        self.max_points = max_points
        self.max_paths = max_paths
        self.history_id = uuid.uuid4().hex
        self._lock = threading.Lock()
        self._seq = 0
        self._t: int | None = None
        self._reset_count: int | None = None
        self._available = False
        self._beliefs: dict[str, _Track] = {}
        self._plugins: dict[str, _Track] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=max_points)

    def ingest(self, autonomy: object) -> bool:
        """Record what changed in a ``host_status()["autonomy"]`` payload; False when nothing new."""

        steps = autonomy.get("steps") if isinstance(autonomy, dict) else None
        memory = steps.get("memory") if isinstance(steps, dict) else None
        if not isinstance(memory, dict):
            return False
        cycle = autonomy.get("last_cycle")
        context = cycle.get("context") if isinstance(cycle, dict) else None
        t = context.get("timestamp_ms") if isinstance(context, dict) else None
        with self._lock:
            self._available = True
            if not _is_int(t) or t == self._t:
                return False
            reset_count = memory.get("reset_count") if _is_int(memory.get("reset_count")) else None
            if self._t is not None and (t < self._t or (
                    reset_count is not None and self._reset_count is not None and reset_count > self._reset_count)):
                self._event(t, "reset", previous_t=self._t)
            self._t = t
            self._reset_count = reset_count
            beliefs = memory.get("beliefs") if isinstance(memory.get("beliefs"), dict) else {}
            for belief_id, belief in beliefs.items():
                if isinstance(belief, dict):
                    self._ingest_belief(str(belief_id), belief, t)
            for belief_id, track in self._beliefs.items():
                if belief_id not in beliefs and track.current is not None:
                    track.removed(self._next, t)
                    self._event(t, "belief_removed", belief_id=belief_id)
            plugins = memory.get("plugins") if isinstance(memory.get("plugins"), list) else []
            for record in plugins:
                if isinstance(record, dict) and isinstance(record.get("plugin_id"), str):
                    track = self._plugins.setdefault(record["plugin_id"], self._track())
                    track.current = record
                    track.record(_leaves({name: record.get(name) for name in PLUGIN_FIELDS}), self._next, t)
            return True

    def payload(self, since: int = 0) -> dict[str, Any]:
        """Every belief and plugin as it is now, with the rows added after ``since``."""

        with self._lock:
            return {
                "schema": LEARNING_HISTORY_SCHEMA,
                "history_id": self.history_id,
                "seq": self._seq,
                "now_ms": self._t,
                "available": self._available,
                "beliefs": {belief_id: track.payload(since) for belief_id, track in self._beliefs.items()},
                "plugins": {plugin_id: track.payload(since) for plugin_id, track in self._plugins.items()},
                "events": _after(self._events, since),
            }

    def _ingest_belief(self, belief_id: str, belief: dict[str, Any], t: int) -> None:
        track = self._beliefs.get(belief_id)
        if track is None or track.current is None:
            track = track or self._beliefs.setdefault(belief_id, self._track())
            self._event(t, "belief_added", belief_id=belief_id)
        previous = track.current or {}
        track.current = belief
        track.record(_leaves({name: belief.get(name) for name in BELIEF_FIELDS}), self._next, t)
        new_checks = track.checked(_items(belief.get("checks")), self._next, t)
        track.trialled(_items(previous.get("trials")), _items(belief.get("trials")),
                       _rises(previous.get("withdrawals"), belief.get("withdrawals")), new_checks, self._next, t)

    def _track(self) -> _Track:
        return _Track(self.max_points, self.max_paths)

    def _event(self, t: int, kind: str, **fields: Any) -> None:
        self._events.append({"seq": self._next(), "t": t, "kind": kind, **fields})

    def _next(self) -> int:
        self._seq += 1
        return self._seq


class _Track:
    """One belief's or plugin's current record, series, checks and trials."""

    def __init__(self, max_points: int, max_paths: int) -> None:
        self.max_points = max_points
        self.max_paths = max_paths
        self.current: dict[str, Any] | None = None
        self.series: dict[str, deque[list[Any]]] = {}
        self.last: dict[str, Any] = {}
        self.dropped: set[str] = set()
        self.checks: deque[dict[str, Any]] = deque(maxlen=max_points)
        self.trials: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._seen_checks: Counter[str] = Counter()

    def record(self, leaves: dict[str, Any], next_seq, t: int) -> None:
        for path in [path for path in self.last if path not in leaves]:
            del self.last[path]
            self.series[path].append([next_seq(), t, None])
        for path, value in leaves.items():
            if path in self.last and self.last[path] == value and type(self.last[path]) is type(value):
                continue
            if path not in self.series:
                if len(self.series) >= self.max_paths:
                    self.dropped.add(path)
                    continue
                self.series[path] = deque(maxlen=self.max_points)
            self.last[path] = value
            self.series[path].append([next_seq(), t, value])

    def checked(self, checks: list[dict[str, Any]], next_seq, t: int) -> list[dict[str, Any]]:
        """Add the checks not in the last snapshot, counting repeats, and return them."""

        unseen = self._seen_checks.copy()
        found = []
        for check in checks:
            key = _canonical(check)
            if unseen[key] > 0:
                unseen[key] -= 1
                continue
            found.append(check)
            self.checks.append({"seq": next_seq(), "t": t, **check})
        self._seen_checks = Counter(_canonical(check) for check in checks)
        return found

    def trialled(self, before, after, withdrawn: dict[str, Counter[str]], new_checks, next_seq, t: int) -> None:
        """Open the trials first seen now and end the ones that left."""

        current = {trial.get("trial_id"): trial for trial in after}
        unchecked = []
        for trial in before:
            if trial.get("trial_id") in current:
                continue
            check = next((item for item in new_checks if item.get("method") == trial.get("method")
                          and item.get("predicted") == trial.get("predicted")), None)
            if check is None:
                unchecked.append(trial)
                continue
            new_checks = [item for item in new_checks if item is not check]
            self._end(trial.get("trial_id"), next_seq, t, end="resolved", outcome=check.get("outcome"))
        for trial in unchecked:
            reasons = withdrawn.get(trial.get("method"))
            reason = next((name for name, count in (reasons or {}).items() if count > 0), None)
            if reason is None:
                self._end(trial.get("trial_id"), next_seq, t, end="resolved", outcome=None)
                continue
            reasons[reason] -= 1
            self._end(trial.get("trial_id"), next_seq, t, end="withdrawn", reason=reason)
        for trial_id, trial in current.items():
            row = self.trials.get(trial_id)
            if row is None or row["end"] != "open":
                self._row(trial_id, {
                    "seq": next_seq(), "trial_id": trial_id, "method": trial.get("method"),
                    "started_at_ms": trial.get("started_at_ms"), "predicted": trial.get("predicted"),
                    "opened_t": t, "ended_t": None, "end": "open", "reason": None, "outcome": None,
                })

    def removed(self, next_seq, t: int) -> None:
        """End every open trial and break every series when the belief is withdrawn from memory."""

        for trial_id, row in list(self.trials.items()):
            if row["end"] == "open":
                self._end(trial_id, next_seq, t, end="removed")
        self.record({}, next_seq, t)
        self.current = None

    def payload(self, since: int) -> dict[str, Any]:
        series = {}
        for path, points in self.series.items():
            rows = _after(points, since)
            if rows:
                series[path] = rows
        return {
            "current": self.current,
            "series": series,
            "checks": _after(self.checks, since),
            "trials": _after(self.trials.values(), since),
            "dropped_paths": len(self.dropped),
        }

    def _end(self, trial_id, next_seq, t: int, **fields: Any) -> None:
        row = self.trials.get(trial_id)
        if row is not None and row["end"] == "open":
            self._row(trial_id, {**row, "seq": next_seq(), "ended_t": t, **fields})

    def _row(self, trial_id, row: dict[str, Any]) -> None:
        self.trials[trial_id] = row
        self.trials.move_to_end(trial_id)
        while len(self.trials) > self.max_points:
            self.trials.popitem(last=False)


def _leaves(value: Any, path: str = "") -> dict[str, Any]:
    """Each number, boolean, string and list length under ``value``, by JSON Pointer path."""

    leaves: dict[str, Any] = {}
    if isinstance(value, bool):
        leaves[path] = int(value)
    elif isinstance(value, (int, float)):
        if math.isfinite(value):
            leaves[path] = value
    elif isinstance(value, str):
        leaves[path] = value
    elif isinstance(value, (list, tuple)):
        leaves[f"{path}#len"] = len(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            key = str(key)
            if item is None or key == "at_ms" or key.endswith("_at_ms"):
                continue
            leaves.update(_leaves(item, f"{path}/{key.replace('~', '~0').replace('/', '~1')}"))
    return leaves


def _rises(before: object, after: object) -> dict[str, Counter[str]]:
    """How much each ``{method: {reason: count}}`` tally rose."""

    before = before if isinstance(before, dict) else {}
    rises: dict[str, Counter[str]] = {}
    for method, reasons in (after if isinstance(after, dict) else {}).items():
        earlier = before.get(method) if isinstance(before.get(method), dict) else {}
        for reason, count in (reasons if isinstance(reasons, dict) else {}).items():
            rise = count - earlier.get(reason, 0) if _is_int(count) and _is_int(earlier.get(reason, 0)) else 0
            if rise > 0:
                rises.setdefault(method, Counter())[reason] = rise
    return rises


def _after(rows, since: int) -> list[Any]:
    """The rows, in ``seq`` order, whose ``seq`` is after ``since``."""

    found = []
    for row in reversed(rows):
        if (row[0] if isinstance(row, list) else row["seq"]) <= since:
            break
        found.append(row)
    found.reverse()
    return found


def _items(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
