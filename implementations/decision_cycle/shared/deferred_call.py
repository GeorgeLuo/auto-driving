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

Concurrency: workers compute private outcomes only. The owning plugin's next
ask/take/poll/drop_stale commits them into its current map, inside the runner's
plugin-call boundary. record() is read-only; counters become visible at that
owning call. Retained resources deliver across downstream map replacement.
Removed/replaced history has a different lifetime, so old outcomes cannot revive
or contaminate it. No lock covers run(). At most workers requests are outstanding;
when capacity is occupied, asking waits for a later owning call without queueing
more transport work. shutdown(wait=False) never waits for a remote operation.
"""

from __future__ import annotations

# pyright: basic

import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from time import monotonic
from typing import Generic, Literal, TypeAlias, TypeVar, TypedDict, cast
from uuid import uuid4

from autonomy.shared_memory import SharedMemory, require_plugin_call

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
        return cast(None | bool | int | str, value)
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_data(item) for item in value]
    if isinstance(value, dict):
        if not all(type(key) is str for key in value):
            raise TypeError("deferred call JSON object keys must be strings")
        return {key: _json_data(item) for key, item in value.items()}
    raise TypeError("deferred call scope, about and result must be finite JSON data")


class CallPair(TypedDict):
    scope: JSONValue
    about: JSONValue


class CallRecord(CallPair):
    call_id: str
    asked_ms: int
    status: Literal["pending", "answered", "failed"]
    elapsed_ms: int
    result: JSONValue
    error: str | None


class CallHistory(TypedDict):
    lifetime: str
    current: CallRecord | None
    last_asked: CallPair | None
    counts: dict[str, int]


@dataclass(frozen=True)
class Completion:
    status: Literal["answered", "failed"]
    elapsed_ms: int
    result: JSONValue
    error: str | None


def _empty_record() -> CallHistory:
    return {"lifetime": uuid4().hex, "current": None, "last_asked": None,
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
        self._workers = workers
        self._closed = False
        self._started: dict[str, str] = {}
        self._completed: dict[str, Completion] = {}

    def record(self, shared_memory: SharedMemory) -> CallHistory:
        """Read a detached record; an unused resource reports zero counts."""
        with self._lock:
            return deepcopy(cast(CallHistory, shared_memory.get(self._key) or _empty_record()))

    def _overdue(self, current: CallRecord, now_ms: int | None) -> bool:
        if self._max_age_ms is None or now_ms is None or current["status"] != "pending":
            return False
        return now_ms - current["asked_ms"] >= self._max_age_ms

    def _reconcile(self, shared_memory: SharedMemory, scope: JSONValue, now_ms: int | None = None) -> CallHistory:
        require_plugin_call(shared_memory, self._key)
        record = self.record(shared_memory)
        self._commit_completed(shared_memory, record)
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
            record["counts"]["expired"] += 1
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
                raise DeferredCallFailed(current["error"] or "deferred call failed")
            return CallAnswer(current["call_id"], current["scope"], current["about"], current["asked_ms"],
                              current["elapsed_ms"], current["result"])

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
            pair: CallPair = {"scope": scope, "about": about}
            if record["current"] is not None:
                return
            if about is None:
                return
            if record["last_asked"] == pair:
                return
            if self._closed:
                raise RuntimeError("deferred call resource is shut down")
            if len(self._started) >= self._workers:
                return
            payload = request()
            current: CallRecord = {"call_id": uuid4().hex, **pair, "asked_ms": now_ms, "status": "pending",
                       "elapsed_ms": 0, "result": None, "error": None}
            record["current"], record["last_asked"] = current, deepcopy(pair)
            record["counts"]["asked"] += 1
            self._started[current["call_id"]] = record["lifetime"]
            shared_memory[self._key] = record
            if self._pool is not None:
                self._pool.submit(self._complete, current["call_id"], payload)
                return
        self._complete(current["call_id"], payload)
        with self._lock:
            self._reconcile(shared_memory, scope)

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

    def _complete(self, call_id: str, payload: Request) -> None:
        started = monotonic()
        status: Literal["answered", "failed"]
        try:
            result, error, status = _json_data(self._run(payload)), None, "answered"
        except Exception as exc:
            result, error, status = None, f"{type(exc).__name__}: {exc}", "failed"
        outcome = Completion(status, int((monotonic() - started) * 1000), result, error)
        with self._lock:
            self._completed[call_id] = outcome

    def _commit_completed(self, shared_memory: SharedMemory, record: CallHistory) -> None:
        for call_id, outcome in self._completed.items():
            lifetime = self._started.pop(call_id)
            if self._key not in shared_memory or lifetime != record["lifetime"]:
                continue
            current = record["current"]
            if current is not None and current["call_id"] == call_id:
                record["counts"][outcome.status] += 1
                record["current"] = {**current, "status": outcome.status, "elapsed_ms": outcome.elapsed_ms,
                                     "result": outcome.result, "error": outcome.error}
            else:
                record["counts"]["dropped"] += 1
            shared_memory[self._key] = record
        self._completed.clear()

    def shutdown(self, *, wait: bool = True) -> None:
        """Stop submitting; wait=False retires without waiting for remote calls."""
        with self._lock:
            self._closed = True
        if self._pool is not None:
            self._pool.shutdown(wait=wait)
