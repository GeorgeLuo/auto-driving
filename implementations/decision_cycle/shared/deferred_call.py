"""One deferred outside call per plugin, with its history in shared memory.

Construct a resource once, using a plugin-owned key, then call it each frame::

    self._call = DeferredCall(run, key="proposal.my_plugin.call", blocking=False)
    answer = self._call.poll(shared_memory, now_ms=now, scope=epoch,
                             about=input_id, request=lambda: build_request())
    if answer is not None:
        use(answer.result)

``run(request)`` returns JSON data; scope and about are JSON too. ``about=None``
means there is nothing to ask. The request factory runs on the frame thread,
only when asking, and its payload is never stored. The last (scope, about) pair
is not asked twice, except after failure or abandonment, which permit retry.
``record(shared_memory)`` returns a detached JSON record for diagnostics.

Background mode uses a pool. Blocking mode runs inline: poll takes the answer
in the frame that asked. If an answer changes its own scope, use take, apply
the answer, then ask with the resulting scope. In blocking mode that pattern
takes the answer on the next frame. A plugin with nothing to do this frame (not
assigned, or exited) calls drop_stale with its scope, so calls from an older
scope are counted when the scope changes rather than at its next poll. Call
shutdown when retiring the resource.

``max_age_ms`` caps how long a call this resource started may stay pending:
an ask at least that long after the pending call was asked (frame time)
expires it, counts ``expired``, permits the same pair again, and asks anew.
The expired call keeps its worker until ``run`` returns; its completion then
counts one drop. Without ``max_age_ms`` a hung call holds the resource until
``run`` itself gives up, such as a transport timeout.

A scope change discards a finished answer immediately. A running call is
detached immediately, allowing another submission; its eventual completion
counts one drop and never becomes current. A pending call this resource did
not start (a loaded map or replaced plugin) is abandoned and counted, allowing
the same pair to be asked again. Abandonment and dropping have separate counts.
Answered/failed counts describe completions committed as current; obsolete
completions only increment dropped.

Concurrency: the worker commits only by replacing shared_memory[key], which
the frame thread created when asking. It replaces current only if call_id
still matches; otherwise it only accounts for the discarded completion. A
removed key is never recreated by a worker. Workers and frame methods,
including poll, use this resource's lock for each read-modify-write, never
around run. Hosts must retire the old resource before concurrently driving a
replacement: separate resources have separate locks. The instance keeps only
configuration, the callable, pool, lock and ids of running calls; all history
needed by subsequent frames lives in shared memory. Calls may outlive a scope;
choose enough workers to allow fresh work while obsolete calls finish.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from time import monotonic
from typing import Generic, TypeAlias, TypeVar
from uuid import uuid4

from autonomy.shared_memory import SharedMemory

JSONValue: TypeAlias = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
Request = TypeVar("Request")


@dataclass(frozen=True)
class CallAnswer:
    """A completed call, taken once; times are frame time plus monotonic duration."""

    call_id: str
    scope: JSONValue
    about: JSONValue
    asked_ms: int
    elapsed_ms: int
    result: JSONValue


class DeferredCallFailed(RuntimeError):
    """The current call failed; its error is the message and the pair may retry."""


def _json_data(value: object) -> JSONValue:
    """Detach JSON values, normalizing tuples so saved pairs compare identically."""
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_data(item) for item in value]
    if isinstance(value, dict):
        if not all(type(key) is str for key in value):
            raise TypeError("deferred call JSON object keys must be strings")
        return {key: _json_data(item) for key, item in value.items()}
    raise TypeError("deferred call scope, about and result must be finite JSON data")


def _empty_record() -> dict:
    return {"current": None, "last_asked": None,
            "counts": {"asked": 0, "answered": 0, "failed": 0, "dropped": 0, "abandoned": 0, "expired": 0}}


class DeferredCall(Generic[Request]):
    """A reusable call resource; construct once and keep its key exclusive to it."""

    def __init__(
        self, run: Callable[[Request], JSONValue], *, key: str,
        blocking: bool = False, workers: int = 4, max_age_ms: int | None = None,
    ) -> None:
        if type(workers) is not int or workers < 1:
            raise ValueError("workers must be a positive int")
        if max_age_ms is not None and (type(max_age_ms) is not int or max_age_ms < 1):
            raise ValueError("max_age_ms must be a positive int or None")
        self._run, self._key, self._max_age_ms = run, key, max_age_ms
        self._pool = None
        if not blocking:
            self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="deferred-call")
        self._lock = RLock()
        self._started: set[str] = set()

    def record(self, shared_memory: SharedMemory) -> dict:
        """Read a detached record; an unused resource reports zero counts."""
        with self._lock:
            return deepcopy(shared_memory.get(self._key) or _empty_record())

    def _overdue(self, current: dict, now_ms: int | None) -> bool:
        if self._max_age_ms is None or now_ms is None or current["status"] != "pending":
            return False
        return now_ms - current["asked_ms"] >= self._max_age_ms

    def _reconcile(self, shared_memory: SharedMemory, scope: JSONValue, now_ms: int | None = None) -> dict:
        record = self.record(shared_memory)
        current = record["current"]
        if current is None:
            return record
        if current["status"] == "pending" and current["call_id"] not in self._started:
            record["counts"]["abandoned"] += 1
            record["last_asked"] = None
        elif current["scope"] != scope:
            if current["status"] != "pending":
                record["counts"]["dropped"] += 1
        elif self._overdue(current, now_ms):
            counts = record["counts"]
            counts["expired"] = counts.get("expired", 0) + 1  # records saved before expiry had no count
            record["last_asked"] = None
        else:
            return record
        record["current"] = None
        shared_memory[self._key] = record
        return record

    def take(self, shared_memory: SharedMemory, *, scope: JSONValue) -> CallAnswer | None:
        """Abandon foreign pending work, discard other scopes, then take once.

        Failures are cleared before DeferredCallFailed is raised, and allow the
        same (scope, about) pair to be asked again by a later call.
        """
        scope = _json_data(scope)
        with self._lock:
            record = self._reconcile(shared_memory, scope)
            current = record["current"]
            if current is None or current["status"] == "pending":
                return None
            record["current"] = None
            if current["status"] == "failed":
                record["last_asked"] = None
            shared_memory[self._key] = record
            if current["status"] == "failed":
                raise DeferredCallFailed(current["error"])
            return CallAnswer(**{name: current[name] for name in CallAnswer.__dataclass_fields__})

    def drop_stale(self, shared_memory: SharedMemory, *, scope: JSONValue) -> None:
        """Abandon foreign pending work and discard other scopes; take and ask nothing."""
        scope = _json_data(scope)
        with self._lock:
            self._reconcile(shared_memory, scope)

    def ask(
        self, shared_memory: SharedMemory, *, now_ms: int, scope: JSONValue,
        about: JSONValue, request: Callable[[], Request],
    ) -> None:
        """Ask only for a new pair with no current call; build the request inline.

        A pending call older than ``max_age_ms`` is expired first, so this asks anew.
        """
        scope, about = _json_data(scope), _json_data(about)
        with self._lock:
            record = self._reconcile(shared_memory, scope, now_ms)
            pair = {"scope": scope, "about": about}
            if record["current"] is not None:
                return
            if about is None:
                return
            if record["last_asked"] == pair:
                return
            payload = request()
            current = {"call_id": uuid4().hex, **pair, "asked_ms": now_ms, "status": "pending",
                       "elapsed_ms": None, "result": None, "error": None}
            record["current"], record["last_asked"] = current, deepcopy(pair)
            record["counts"]["asked"] += 1
            self._started.add(current["call_id"])
            shared_memory[self._key] = record
            if self._pool is not None:
                self._pool.submit(self._complete, shared_memory, current, payload)
                return
        self._complete(shared_memory, current, payload)

    def poll(
        self, shared_memory: SharedMemory, *, now_ms: int, scope: JSONValue,
        about: JSONValue, request: Callable[[], Request],
    ) -> CallAnswer | None:
        """Take, ask, and (only if nothing was taken) take again.

        Blocking calls answer in this poll. A sufficiently fast background
        completion can also be taken by the second take.
        """
        answer = self.take(shared_memory, scope=scope)
        self.ask(shared_memory, now_ms=now_ms, scope=scope, about=about, request=request)
        return answer if answer is not None else self.take(shared_memory, scope=scope)

    def _complete(self, shared_memory: SharedMemory, current: dict, payload: Request) -> None:
        started = monotonic()
        try:
            result, error, status = _json_data(self._run(payload)), None, "answered"
        except Exception as exc:
            result, error, status = None, f"{type(exc).__name__}: {exc}", "failed"
        elapsed_ms = int((monotonic() - started) * 1000)
        with self._lock:
            self._started.discard(current["call_id"])
            if self._key not in shared_memory:
                return
            record = self.record(shared_memory)
            if (record["current"] or {}).get("call_id") == current["call_id"]:
                record["counts"][status] += 1
                record["current"] = {**current, "status": status, "elapsed_ms": elapsed_ms,
                                     "result": result, "error": error}
            else:
                record["counts"]["dropped"] += 1
            shared_memory[self._key] = record

    def shutdown(self, *, wait: bool = True) -> None:
        """Retire the pool, optionally waiting for running calls to finish."""
        if self._pool is not None:
            self._pool.shutdown(wait=wait)
