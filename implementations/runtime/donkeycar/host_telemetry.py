"""Read-only host-boundary telemetry for the Donkey runtime.

The producer observes the values that the host has already selected at the
post-``DriveMode``/pre-drivetrain boundary.  It never participates in command
selection, performs network I/O, or reports actuator feedback.  Publications
are detached in-memory snapshots so route readers cannot mutate the state that
the next reader or the next host tick observes.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from copy import deepcopy
from typing import Any, Callable, Mapping, cast


HOST_TELEMETRY_SCHEMA = "automa_host_boundary_telemetry_v0"
HOST_TELEMETRY_LATEST_PATH = "/autonomy/telemetry/latest"
HOST_TELEMETRY_RECORDS_PATH = "/autonomy/telemetry/records"
HOST_TELEMETRY_LIMITS = {
    "max_source_age_ms": 1500,
    "max_publication_age_ms": 1500,
    "max_gap_ms": 1000,
    "future_skew_tolerance_ms": 250,
}
HOST_TELEMETRY_MODES = frozenset({"user", "local_angle", "local"})
HOST_TELEMETRY_BOUNDARY = "post_drive_mode_pre_drivetrain"
HOST_TELEMETRY_REASONS = (
    "publisher_missing",
    "warming",
    "producer_stopped",
    "schema_invalid",
    "field_invalid",
    "identity_mismatch",
    "future_dated",
    "source_stale",
    "publication_stale",
    "sequence_regressed",
    "sequence_duplicate",
    "sequence_gap",
    "coverage_gap",
    "observer_error",
    "method_not_allowed",
    "query_invalid",
    "redirect_rejected",
)


def epoch_ms() -> int:
    """Return the Donkey host's wall-clock Unix epoch in milliseconds."""

    return int(time.time() * 1000)


def _is_epoch_ms(value: Any) -> bool:
    return type(value) is int and value >= 0


def _is_finite_unit(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and -1.0 <= float(value) <= 1.0
    )


def _require_identity(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _pair(value: Mapping[str, Any], *, field: str) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    if set(value) != {"steering", "throttle"}:
        raise ValueError(f"{field} must contain only steering and throttle")
    steering = value.get("steering")
    throttle = value.get("throttle")
    if not _is_finite_unit(steering) or not _is_finite_unit(throttle):
        raise ValueError(f"{field} must contain finite normalized steering/throttle")
    steering_value = cast(int | float, steering)
    throttle_value = cast(int | float, throttle)
    return {"steering": float(steering_value), "throttle": float(throttle_value)}


def _source_frame(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("source_frame must be an object")
    if set(value) != {
        "frame_id",
        "frame_index",
        "captured_at_ms",
        "completed_at_ms",
    }:
        raise ValueError("source_frame has an invalid shape")
    frame_id = value.get("frame_id")
    if not isinstance(frame_id, str) or not frame_id:
        raise ValueError("source_frame.frame_id must be a non-empty string")
    frame_index = value.get("frame_index")
    if not _is_epoch_ms(frame_index):
        raise ValueError("source_frame.frame_index must be a non-negative integer")
    captured = value.get("captured_at_ms")
    completed = value.get("completed_at_ms")
    if not _is_epoch_ms(captured) or not _is_epoch_ms(completed):
        raise ValueError("source_frame timestamps must be epoch milliseconds")
    captured_value = cast(int, captured)
    completed_value = cast(int, completed)
    if completed_value < captured_value:
        raise ValueError("source_frame.completed_at_ms precedes captured_at_ms")
    return {
        "frame_id": frame_id,
        "frame_index": frame_index,
        "captured_at_ms": captured_value,
        "completed_at_ms": completed_value,
    }


def identity_tuple(record: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return the complete join identity without filling missing members."""

    activation = record.get("activation")
    source_frame = record.get("source_frame")
    if not isinstance(activation, Mapping) or not isinstance(source_frame, Mapping):
        raise ValueError("record identity containers are missing")
    return (
        record.get("vehicle_id"),
        record.get("source_id"),
        record.get("run_id"),
        record.get("generation_id"),
        activation.get("engine_id"),
        activation.get("activated_at_ms"),
        activation.get("generation_id"),
        source_frame.get("frame_id"),
        source_frame.get("frame_index"),
        source_frame.get("captured_at_ms"),
        source_frame.get("completed_at_ms"),
    )


def _query_int(value: Any, *, minimum: int) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer query value")
    if type(value) is int:
        result = value
    elif isinstance(value, str) and value.isascii() and value.isdigit():
        result = int(value)
    else:
        raise ValueError("query value must be a decimal integer")
    if result < minimum:
        raise ValueError("query value is below its minimum")
    return result


def parse_records_query(query: Mapping[str, Any]) -> tuple[int, int]:
    """Validate the exact bounded-history query shape."""

    if not isinstance(query, Mapping):
        raise ValueError("query must be an object")
    if set(query) != {"after_sequence", "limit"}:
        raise ValueError("only after_sequence and limit are accepted")
    after_sequence = _query_int(query["after_sequence"], minimum=0)
    limit = _query_int(query["limit"], minimum=1)
    if limit > 128:
        raise ValueError("limit must be at most 128")
    return after_sequence, limit


class HostTelemetryStore:
    """Thread-safe latest/history store for complete host-boundary ticks.

    ``observe`` is intentionally synchronous and memory-only.  The default
    ring retains 256 records, and every read returns a deep copy while holding
    the same lock used by publication, so a reader sees one complete snapshot
    rather than a mixture of two host ticks.
    """

    def __init__(
        self,
        *,
        vehicle_id: str,
        source_id: str,
        run_id: str,
        generation_id: str,
        activation_engine_id: str,
        activation_activated_at_ms: int,
        activation_generation_id: str | None = None,
        activation_engine_config: Mapping[str, Any] | None = None,
        clock: Callable[[], int] | None = None,
        max_records: int = 256,
        limits: Mapping[str, int] | None = None,
    ) -> None:
        if type(max_records) is not int or max_records < 256:
            raise ValueError("max_records must retain at least 256 records")
        for field, value in (
            ("vehicle_id", vehicle_id),
            ("source_id", source_id),
            ("run_id", run_id),
            ("generation_id", generation_id),
            ("activation_engine_id", activation_engine_id),
        ):
            _require_identity(value, field=field)
        if not _is_epoch_ms(activation_activated_at_ms):
            raise ValueError("activation_activated_at_ms must be a non-negative integer")
        if activation_generation_id is not None:
            _require_identity(activation_generation_id, field="activation_generation_id")
            if activation_generation_id != generation_id:
                raise ValueError("activation_generation_id must equal generation_id")
        if limits is not None and dict(limits) != HOST_TELEMETRY_LIMITS:
            raise ValueError("telemetry limits are fixed for v0")
        if clock is not None and not callable(clock):
            raise ValueError("clock must be callable")

        self.vehicle_id = vehicle_id
        self.source_id = source_id
        self.run_id = run_id
        self.generation_id = generation_id
        self.activation_engine_id = activation_engine_id
        self.activation_activated_at_ms = activation_activated_at_ms
        # Retained only for a bounded status/debugger owner; it is not copied
        # into the telemetry wire record because it is outside the v0 contract.
        self.activation_engine_config = deepcopy(dict(activation_engine_config or {}))
        self.limits = dict(HOST_TELEMETRY_LIMITS)
        self._clock = clock or epoch_ms
        self._records: deque[dict[str, Any]] = deque(maxlen=max_records)
        self._lock = threading.RLock()
        self._stopped = False
        self._last_error_reason: str | None = None
        self._last_error_status: str | None = None
        self._last_sequence = 0
        self._last_clock_ms: int | None = None

    @property
    def identity(self) -> tuple[Any, ...]:
        return (
            self.vehicle_id,
            self.source_id,
            self.run_id,
            self.generation_id,
            self.activation_engine_id,
            self.activation_activated_at_ms,
            self.generation_id,
        )

    def stop(self) -> None:
        """Make this producer terminal; subsequent latest reads are stopped."""

        with self._lock:
            self._stopped = True
            self._last_error_reason = None
            self._last_error_status = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            if self._stopped:
                return {
                    "schema": HOST_TELEMETRY_SCHEMA,
                    "status": "stopped",
                    "reason": "producer_stopped",
                    "record_count": len(self._records),
                    "latest_sequence": self._last_sequence or None,
                    "stopped": True,
                }
            try:
                read_at_ms = self._clock_now_locked()
            except (TypeError, ValueError, OverflowError):
                return {
                    "schema": HOST_TELEMETRY_SCHEMA,
                    "status": "error",
                    "reason": "field_invalid",
                    "record_count": len(self._records),
                    "latest_sequence": self._last_sequence or None,
                    "stopped": self._stopped,
                }
            except Exception:
                return {
                    "schema": HOST_TELEMETRY_SCHEMA,
                    "status": "error",
                    "reason": "observer_error",
                    "record_count": len(self._records),
                    "latest_sequence": self._last_sequence or None,
                    "stopped": self._stopped,
                }
            if (
                self._last_clock_ms is not None
                and read_at_ms < self._last_clock_ms
            ):
                status, reason = "error", "sequence_regressed"
            elif self._last_error_reason is not None:
                status, reason = (
                    self._last_error_status or "error",
                    self._last_error_reason,
                )
            elif not self._records:
                status, reason = "warming", "warming"
            else:
                envelope = self._envelope_for_record_locked(
                    self._records[-1], read_at_ms
                )
                status, reason = envelope["status"], envelope["reason"]
            return {
                "schema": HOST_TELEMETRY_SCHEMA,
                "status": status,
                "reason": reason,
                "record_count": len(self._records),
                "latest_sequence": self._last_sequence or None,
                "stopped": self._stopped,
            }

    def observe(
        self,
        *,
        mode: str,
        user_input: Mapping[str, Any],
        pilot_output: Mapping[str, Any],
        host_selected_output: Mapping[str, Any],
        source_frame: Mapping[str, Any],
        observed_at_ms: int | None = None,
        published_at_ms: int | None = None,
        sequence: int | None = None,
    ) -> dict[str, Any]:
        """Record one post-``DriveMode`` observation or fail closed.

        ``sequence`` is optional for the normal path, where the store assigns
        the next host tick.  Tests and adapters that reserve host ticks may
        provide it to preserve the specified skipped-tick arithmetic.
        """

        with self._lock:
            if self._stopped:
                return self._error_envelope_locked("stopped", "producer_stopped")

            try:
                if mode not in HOST_TELEMETRY_MODES:
                    raise ValueError("mode_invalid")
                user = _pair(user_input, field="user_input")
                pilot = _pair(pilot_output, field="pilot_output")
                selected = _pair(host_selected_output, field="host_selected_output")
                frame = _source_frame(source_frame)
                observed = (
                    self._clock_now_locked()
                    if observed_at_ms is None
                    else observed_at_ms
                )
                if not _is_epoch_ms(observed):
                    raise ValueError("observed_at_ms_invalid")
                published = published_at_ms
                if published is not None and not _is_epoch_ms(published):
                    raise ValueError("published_at_ms_invalid")
                now_ms = self._clock_now_locked()
                if (
                    self._last_clock_ms is not None
                    and now_ms < self._last_clock_ms
                ):
                    return self._observation_error_locked(
                        "error", "sequence_regressed"
                    )
                if frame["completed_at_ms"] > (
                    now_ms + self.limits["future_skew_tolerance_ms"]
                ):
                    return self._observation_error_locked("error", "future_dated")
                if self.activation_activated_at_ms > (
                    now_ms + self.limits["future_skew_tolerance_ms"]
                ):
                    return self._observation_error_locked("error", "future_dated")
                if observed < frame["completed_at_ms"]:
                    raise ValueError("source_time_future")
                if published is not None and published < observed:
                    raise ValueError("publication_precedes_observation")
                if observed > now_ms + self.limits["future_skew_tolerance_ms"]:
                    return self._observation_error_locked("error", "future_dated")
                if (
                    published is not None
                    and published > now_ms + self.limits["future_skew_tolerance_ms"]
                ):
                    return self._observation_error_locked("error", "future_dated")
            except (TypeError, ValueError, OverflowError):
                return self._observation_error_locked("error", "field_invalid")
            except Exception:
                return self._observation_error_locked("error", "observer_error")

            previous = self._records[-1] if self._records else None
            next_sequence = self._last_sequence + 1
            if sequence is not None:
                if type(sequence) is not int or sequence < 1:
                    return self._observation_error_locked("error", "field_invalid")
                next_sequence = sequence
            if previous is None:
                if next_sequence != 1:
                    return self._observation_error_locked("error", "sequence_gap")
                gap_ms = None
                skipped = None
            else:
                if next_sequence < self._last_sequence:
                    return self._observation_error_locked(
                        "error", "sequence_regressed"
                    )
                if next_sequence == self._last_sequence:
                    return self._observation_error_locked(
                        "error", "sequence_duplicate"
                    )
                previous_observed = previous["host_tick"]["observed_at_ms"]
                previous_published = previous["host_tick"]["published_at_ms"]
                if observed < previous_observed or (
                    published is not None and published < previous_published
                ):
                    return self._observation_error_locked(
                        "error", "sequence_regressed"
                    )
                gap_ms = observed - previous_observed
                skipped = next_sequence - previous["host_tick"]["sequence"] - 1

                previous_frame = previous["source_frame"]
                if next_sequence > self._last_sequence + 1 and skipped < 1:
                    return self._observation_error_locked("error", "sequence_gap")
                if frame["frame_index"] < previous_frame["frame_index"]:
                    return self._observation_error_locked(
                        "error", "sequence_regressed"
                    )
                if frame["frame_index"] == previous_frame["frame_index"]:
                    if frame != previous_frame:
                        return self._observation_error_locked(
                            "error", "identity_mismatch"
                        )
                elif frame["frame_id"] == previous_frame["frame_id"]:
                    return self._observation_error_locked(
                        "error", "identity_mismatch"
                    )

            source_age_ms = observed - frame["completed_at_ms"]
            coverage_reason = None
            if previous is not None and skipped:
                coverage_reason = "sequence_gap"
            if (
                previous is not None
                and gap_ms is not None
                and gap_ms > self.limits["max_gap_ms"]
                and coverage_reason is None
            ):
                coverage_reason = "coverage_gap"

            status = "healthy"
            reason = ""
            if source_age_ms > self.limits["max_source_age_ms"]:
                status = "stale"
                reason = "source_stale"
            elif coverage_reason is not None:
                status = "unavailable"
                reason = coverage_reason

            record: dict[str, Any] = {
                "schema": HOST_TELEMETRY_SCHEMA,
                "status": status,
                "reason": reason,
                "vehicle_id": self.vehicle_id,
                "source_id": self.source_id,
                "run_id": self.run_id,
                "generation_id": self.generation_id,
                "activation": {
                    "engine_id": self.activation_engine_id,
                    "activated_at_ms": self.activation_activated_at_ms,
                    "generation_id": self.generation_id,
                },
                "source_frame": frame,
                "host_tick": {
                    "sequence": next_sequence,
                    "observed_at_ms": observed,
                    "published_at_ms": published,
                    "source_age_ms": source_age_ms,
                    "gap_since_previous_ms": gap_ms,
                    "skipped_since_previous": skipped,
                },
                "mode": mode,
                "user_input": user,
                "pilot_output": pilot,
                "host_selected_output": selected,
                "application": {
                    "boundary": HOST_TELEMETRY_BOUNDARY,
                    "actuator_feedback": "unavailable",
                },
                "limits": deepcopy(self.limits),
            }
            if coverage_reason is not None:
                record["coverage_reason"] = coverage_reason

            # A missing publication timestamp is filled after the provisional
            # append while this lock is still held.  Readers cannot enter
            # until the record and all state updates are complete.  Preserve a
            # possible evicted item if the post-append clock check fails.
            evicted_record = (
                self._records[0]
                if self._records.maxlen is not None
                and len(self._records) == self._records.maxlen
                else None
            )
            self._records.append(record)
            if published is None:
                try:
                    published = self._clock_now_locked()
                except (TypeError, ValueError, OverflowError):
                    self._records.pop()
                    if evicted_record is not None:
                        self._records.appendleft(evicted_record)
                    return self._observation_error_locked("error", "field_invalid")
                except Exception:
                    self._records.pop()
                    if evicted_record is not None:
                        self._records.appendleft(evicted_record)
                    return self._observation_error_locked("error", "observer_error")
                if published < now_ms:
                    self._records.pop()
                    if evicted_record is not None:
                        self._records.appendleft(evicted_record)
                    return self._observation_error_locked(
                        "error", "sequence_regressed"
                    )
                if published < observed:
                    self._records.pop()
                    if evicted_record is not None:
                        self._records.appendleft(evicted_record)
                    return self._observation_error_locked("error", "field_invalid")
                if previous is not None and published < previous["host_tick"]["published_at_ms"]:
                    self._records.pop()
                    if evicted_record is not None:
                        self._records.appendleft(evicted_record)
                    return self._observation_error_locked(
                        "error", "sequence_regressed"
                    )
                record["host_tick"]["published_at_ms"] = published
            self._last_sequence = next_sequence
            # An explicitly supplied publication time is test/adapter data;
            # only the store clock advances the monotonic clock watermark.
            self._last_clock_ms = (
                published if published_at_ms is None else now_ms
            )
            self._last_error_reason = None
            self._last_error_status = None
            return self._envelope_for_record_locked(
                record,
                published if published_at_ms is None else now_ms,
            )

    def mark_skipped(self, count: int = 1) -> dict[str, Any]:
        """Reserve one or more uncovered host ticks after a baseline record."""

        with self._lock:
            if self._stopped:
                return self._error_envelope_locked("stopped", "producer_stopped")
            if type(count) is not int or count < 1:
                return self._error_envelope_locked("error", "field_invalid")
            if not self._records:
                # Warming ticks are not an interval and do not move the first
                # complete record away from the required sequence 1.
                return self._error_envelope_locked("warming", "warming")
            self._last_sequence += count
            return self._error_envelope_locked("unavailable", "sequence_gap")

    def mark_unavailable(self, reason: str = "publisher_missing") -> dict[str, Any]:
        """Close the current point/interval without serving an old success."""

        with self._lock:
            if self._stopped:
                return self._error_envelope_locked("stopped", "producer_stopped")
            if reason not in HOST_TELEMETRY_REASONS:
                reason = "field_invalid"
            self._reserve_uncovered_tick_locked()
            return self._error_envelope_locked("unavailable", reason)

    def latest(self, *, now_ms: int | None = None) -> dict[str, Any]:
        with self._lock:
            if self._stopped:
                read_at_ms = now_ms if _is_epoch_ms(now_ms) else None
                return self._error_envelope_locked(
                    "stopped", "producer_stopped", read_at_ms
                )
            try:
                read_at_ms = (
                    self._clock_now_locked() if now_ms is None else now_ms
                )
                if not _is_epoch_ms(read_at_ms):
                    raise ValueError("read_at_ms_invalid")
            except (TypeError, ValueError, OverflowError):
                return self._error_envelope_locked(
                    "error", "field_invalid", remember_error=False
                )
            except Exception:
                return self._error_envelope_locked(
                    "error", "observer_error", remember_error=False
                )
            if (
                self._last_clock_ms is not None
                and read_at_ms < self._last_clock_ms
            ):
                return self._error_envelope_locked(
                    "error",
                    "sequence_regressed",
                    read_at_ms,
                    remember_error=False,
                )
            if self._last_error_reason is not None:
                return self._error_envelope_locked(
                    self._last_error_status or "error",
                    self._last_error_reason,
                    read_at_ms,
                    remember_error=False,
                )
            if not self._records:
                return self._error_envelope_locked(
                    "warming", "warming", read_at_ms, remember_error=False
                )
            return self._envelope_for_record_locked(self._records[-1], read_at_ms)

    def records(
        self,
        *,
        after_sequence: int | str,
        limit: int | str,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            try:
                after, bounded_limit = parse_records_query(
                    {"after_sequence": after_sequence, "limit": limit}
                )
            except (TypeError, ValueError, OverflowError):
                return self._history_error_locked("error", "query_invalid")
            if self._stopped:
                return self._history_error_locked(
                    "stopped", "producer_stopped", self._safe_clock_locked()
                )
            try:
                read_at_ms = (
                    self._clock_now_locked() if now_ms is None else now_ms
                )
                if not _is_epoch_ms(read_at_ms):
                    raise ValueError("read_at_ms_invalid")
            except (TypeError, ValueError, OverflowError):
                return self._history_error_locked(
                    "error", "field_invalid"
                )
            except Exception:
                return self._history_error_locked(
                    "error", "observer_error"
                )
            if (
                self._last_clock_ms is not None
                and read_at_ms < self._last_clock_ms
            ):
                return self._history_error_locked(
                    "error", "sequence_regressed", read_at_ms
                )
            if self._last_error_reason is not None:
                return self._history_error_locked(
                    self._last_error_status or "error",
                    self._last_error_reason,
                    read_at_ms,
                )
            if not self._records:
                return self._history_error_locked("warming", "warming", read_at_ms)

            earliest = self._records[0]["host_tick"]["sequence"]
            if after < earliest - 1:
                return {
                    "schema": HOST_TELEMETRY_SCHEMA,
                    "ok": False,
                    "status": "unavailable",
                    "reason": "coverage_gap",
                    "coverage_reason": "history_evicted",
                    "read_at_ms": read_at_ms,
                    "records": [],
                }

            selected = [
                deepcopy(record)
                for record in self._records
                if record["host_tick"]["sequence"] > after
            ][:bounded_limit]
            payload: dict[str, Any] = {
                "schema": HOST_TELEMETRY_SCHEMA,
                "ok": True,
                "status": "healthy",
                "reason": "",
                "read_at_ms": read_at_ms,
                "records": selected,
                "coverage": "complete" if selected else "empty",
            }
            if selected and selected[0]["host_tick"]["sequence"] != after + 1:
                payload.update(
                    ok=False,
                    status="unavailable",
                    reason="sequence_gap",
                    coverage_reason="sequence_gap",
                )
            for record in selected:
                record_status = record.get("status")
                record_reason = record.get("reason") or ""
                tick = record["host_tick"]
                publication_age = read_at_ms - tick["published_at_ms"]
                if publication_age < -self.limits["future_skew_tolerance_ms"]:
                    payload.update(ok=False, status="error", reason="future_dated")
                elif publication_age > self.limits["max_publication_age_ms"]:
                    payload.update(ok=False, status="stale", reason="publication_stale")
                elif record_status == "stale":
                    payload.update(ok=False, status="stale", reason=record_reason or "source_stale")
                elif record_status != "healthy" or record.get("coverage_reason"):
                    payload.update(
                        ok=False,
                        status="unavailable",
                        reason=record_reason or record.get("coverage_reason") or "coverage_gap",
                    )
            return payload

    def _clock_now_locked(self) -> int:
        value = self._clock()
        if not _is_epoch_ms(value):
            raise ValueError("clock must return a non-negative integer epoch millisecond")
        return value

    def _reserve_uncovered_tick_locked(self) -> None:
        """Advance coverage over one attempted but unpublished host tick."""

        if self._records:
            self._last_sequence += 1

    def _observation_error_locked(
        self,
        status: str,
        reason: str,
    ) -> dict[str, Any]:
        self._reserve_uncovered_tick_locked()
        return self._error_envelope_locked(status, reason)

    def _error_envelope_locked(
        self,
        status: str,
        reason: str,
        read_at_ms: int | None = None,
        *,
        remember_error: bool = True,
    ) -> dict[str, Any]:
        if status not in {"warming", "unavailable", "error", "stopped"}:
            status = "error"
        if reason not in HOST_TELEMETRY_REASONS:
            reason = "field_invalid"
        if remember_error and status not in {"warming", "stopped"}:
            self._last_error_status = status
            self._last_error_reason = reason
        return {
            "schema": HOST_TELEMETRY_SCHEMA,
            "ok": False,
            "status": status,
            "reason": reason,
            "record": None,
            "read_at_ms": read_at_ms if read_at_ms is not None else self._safe_clock_locked(),
        }

    def _history_error_locked(
        self,
        status: str,
        reason: str,
        read_at_ms: int | None = None,
    ) -> dict[str, Any]:
        # History is an HTTP/read surface.  A malformed query or an old
        # reader must not poison the producer's next latest publication.
        envelope = self._error_envelope_locked(
            status, reason, read_at_ms, remember_error=False
        )
        envelope["records"] = []
        return envelope

    def _safe_clock_locked(self) -> int | None:
        try:
            return self._clock_now_locked()
        except Exception:
            return None

    def _envelope_for_record_locked(
        self,
        record: Mapping[str, Any],
        read_at_ms: int,
    ) -> dict[str, Any]:
        copy = deepcopy(dict(record))
        tick = copy["host_tick"]
        publication_age = read_at_ms - tick["published_at_ms"]
        status = copy.get("status", "error")
        reason = copy.get("reason") or ""
        ok = status == "healthy"
        if publication_age < -self.limits["future_skew_tolerance_ms"]:
            status, reason, ok = "error", "future_dated", False
        elif publication_age > self.limits["max_publication_age_ms"]:
            status, reason, ok = "stale", "publication_stale", False
        elif status == "stale":
            ok = False
            reason = reason or "source_stale"
        elif status != "healthy" or copy.get("coverage_reason"):
            status, ok = "unavailable", False
            reason = reason or copy.get("coverage_reason") or "coverage_gap"
        return {
            **copy,
            "ok": ok,
            "status": status,
            "reason": reason,
            "record": copy,
            "read_at_ms": read_at_ms,
            "publication_age_ms": max(0, publication_age),
        }


class DriveModeTelemetryAdapter:
    """Capture the post-selection ``DriveMode`` boundary in process memory."""

    def __init__(self, store: HostTelemetryStore) -> None:
        self.store = store
        self._clock = getattr(store, "_clock", epoch_ms)
        self._pending: dict[str, Any] | None = None
        self._source_frame: dict[str, Any] | None = None
        self._lock = threading.RLock()

    def set_source_frame(self, source_frame: Mapping[str, Any] | None) -> None:
        """Hand off the latest completed runtime-owned source-frame identity."""

        with self._lock:
            if source_frame is None:
                self._source_frame = None
                return
            self._source_frame = deepcopy(dict(source_frame))

    def latest(self, *, now_ms: int | None = None) -> dict[str, Any]:
        return self.store.latest(now_ms=now_ms)

    def records(
        self,
        *,
        after_sequence: int | str,
        limit: int | str,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        return self.store.records(
            after_sequence=after_sequence,
            limit=limit,
            now_ms=now_ms,
        )

    def stop(self) -> None:
        with self._lock:
            self._pending = None
            self._source_frame = None
        self.store.stop()

    def begin_tick(
        self,
        *,
        mode: str,
        user_input: Mapping[str, Any],
        pilot_output: Mapping[str, Any],
        source_frame: Mapping[str, Any] | None = None,
        observed_at_ms: int | None = None,
    ) -> None:
        """Capture inputs after ``DriveMode`` has computed its selection."""

        with self._lock:
            if source_frame is None:
                source_frame = self._source_frame
            if observed_at_ms is None:
                try:
                    observed_at_ms = self._clock()
                except Exception:
                    observed_at_ms = None
            self._pending = {
                "mode": mode,
                "user_input": deepcopy(dict(user_input)),
                "pilot_output": deepcopy(dict(pilot_output)),
                "source_frame": (
                    None if source_frame is None else deepcopy(dict(source_frame))
                ),
                "observed_at_ms": observed_at_ms,
            }

    def complete_tick(
        self,
        host_selected_output: Mapping[str, Any],
        *,
        published_at_ms: int | None = None,
        sequence: int | None = None,
    ) -> dict[str, Any]:
        """Publish one complete tick after the host output is selected."""

        with self._lock:
            pending = self._pending
            self._pending = None
        if pending is None:
            marker = getattr(self.store, "mark_unavailable", None)
            if callable(marker):
                return marker("publisher_missing")
            raise RuntimeError("host telemetry completion has no pending tick")
        if pending["source_frame"] is None:
            marker = getattr(self.store, "mark_unavailable", None)
            if callable(marker):
                return marker("publisher_missing")
            raise RuntimeError("host telemetry source frame is unavailable")
        return self.store.observe(
            mode=pending["mode"],
            user_input=pending["user_input"],
            pilot_output=pending["pilot_output"],
            host_selected_output=host_selected_output,
            source_frame=pending["source_frame"],
            observed_at_ms=pending["observed_at_ms"],
            published_at_ms=published_at_ms,
            sequence=sequence,
        )
