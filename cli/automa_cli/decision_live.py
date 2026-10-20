"""Read-only adapter from a runtime host's decision publications to RuntimeViewServer."""

from __future__ import annotations

import base64
import time
from typing import Any

from .decision import (
    DecisionSurfaceError,
    accept_decision_publication,
    decision_view_frame,
)
from .decision_view import unavailable_host_telemetry_panel
from .host_publications import (
    fetch_decision_transaction, TransactionCache,
    HostTelemetryError,
    fetch_host_telemetry_latest,
    fetch_host_telemetry_records,
    join_host_telemetry_to_decision,
    normalize_host_telemetry_record,
    normalize_host_telemetry_records,
    decision_publication_identity,
)
from .host_sources import host_source_loader
from .runtime_view import RuntimeViewServer


def _provider_identity(normalized: dict[str, Any]) -> dict[str, Any]:
    return {
        "vehicle_id": normalized["vehicle_id"],
        "source_id": normalized["source_id"],
        "run_id": normalized["run_id"],
        "producer_generation_id": normalized["generation_id"],
    }


def _frame_record(normalized: dict[str, Any]) -> dict[str, Any]:
    mode = normalized["decision"]["application"]["mode"]
    values = normalized["decision"]["values"]
    observation_value = values.get("observation")
    observation_value = observation_value if isinstance(observation_value, dict) else None
    sensor_frame = (
        observation_value.get("sensor_frame")
        if isinstance(observation_value, dict)
        else None
    )
    summary = observation_value.get("summary") if isinstance(observation_value, dict) else None
    summary = [str(item) for item in summary] if isinstance(summary, list) else []
    perception = None
    if observation_value is not None:
        metadata = observation_value.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        limits = metadata.get("limits")
        perception = {
            "schema": observation_value.get("perception_schema") or "perception_text_v2",
            "status": "ok",
            "text": "\n".join(summary),
            "lines": summary,
            "signals": observation_value.get("signals")
            if isinstance(observation_value.get("signals"), list)
            else [],
            "things": observation_value.get("things")
            if isinstance(observation_value.get("things"), list)
            else [],
            "artifacts": observation_value.get("artifacts")
            if isinstance(observation_value.get("artifacts"), dict)
            else {},
            "plugin_id": observation_value.get("perception_plugin_id"),
            "plugin_runs": [],
            "measurements": {},
            "limits": limits if isinstance(limits, list) else [],
        }
    timing = normalized["decision"]["values"].get("timing")
    return {
        "frame_id": normalized["frame_id"],
        "frame_index": normalized["frame_index"],
        "captured_at_ms": normalized["timestamp_ms"],
        "timing": timing if isinstance(timing, dict) else None,
        "run_id": normalized["run_id"],
        "sensor_frame": sensor_frame,
        "perception_completed_at_ms": (
            observation_value.get("created_at_ms")
            if isinstance(observation_value, dict)
            else None
        ),
        "perception": perception,
        "observation": observation_value,
        "memory": values.get("memory"),
        "action_policy": mode,
        "control_source": "runtime_host",
        "control_application": "shared_execution" if mode == "autonomy" else "not_applied",
    }


def _transaction_image(transaction: dict[str, Any]) -> tuple[bytes, str] | None:
    """The transaction's decoded image and content type, or None when absent or malformed."""

    encoded = transaction.get("image_base64")
    content_type = transaction.get("image_content_type")
    if not isinstance(encoded, str) or content_type not in {"image/jpeg", "image/png"}:
        return None
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        return None
    return (image_bytes, content_type) if image_bytes else None


def _accepted_pair(
    base_url: str,
    *,
    vehicle_id: str,
    timeout_s: float,
    cache: TransactionCache | None = None,
) -> tuple[dict[str, Any], tuple[bytes, str]]:
    """Accept the decision and image the host published as one snapshot."""

    deadline = time.monotonic() + min(1.0, max(0.2, float(timeout_s)))
    last_error = "matched decision image is unavailable"
    while time.monotonic() < deadline:
        transaction = fetch_decision_transaction(base_url, timeout_s=timeout_s, cache=cache)
        publication = transaction.get("publication") if isinstance(transaction, dict) else None
        try:
            normalized = accept_decision_publication(
                publication,
                vehicle_id=vehicle_id,
                now_ms=int(time.time() * 1000),
                load_source=host_source_loader(base_url, timeout_s=timeout_s),
            )
        except DecisionSurfaceError as exc:
            # The host and the CLI may have a few milliseconds of clock skew.
            # Keep the future-dated rejection fail-closed, but retry the
            # read-only pair while the published cycle becomes current.
            if exc.details.get("reason") != "future_dated":
                raise
            last_error = exc.message_text
            time.sleep(0.04)
            continue
        image = cache.image if cache is not None and cache.image is not None else _transaction_image(transaction)
        if image is None:
            last_error = "matched decision image is unavailable"
            time.sleep(0.04)
            continue
        if cache is not None:
            cache.image = image
        return normalized, image
    raise ConnectionError(last_error)


class DecisionViewAdapter:
    """Publish a runtime host's accepted transactions through the shared decision view."""

    def __init__(
        self,
        *,
        vehicle_id: str,
        base_url: str,
        view_server: RuntimeViewServer,
        timeout_s: float,
    ) -> None:
        self.vehicle_id = vehicle_id
        self.base_url = base_url
        self.view_server = view_server
        self.timeout_s = timeout_s
        self._transaction_cache = TransactionCache()

    def publish_frame(
        self,
        normalized: dict[str, Any],
        image: tuple[bytes, str],
    ) -> bool:
        self.view_server.decision.adopt(_provider_identity(normalized))
        frame_record = _frame_record(normalized)
        report = decision_view_frame(normalized)
        frame_record["host_telemetry"] = read_host_telemetry_panel(
            self.base_url,
            normalized_decision=normalized,
            vehicle_id=self.vehicle_id,
            timeout_s=self.timeout_s,
        )
        published = self.view_server.decision.publish(
            report=report,
            frame_record=frame_record,
            image=image,
        )
        if not published:
            self.view_server.decision.invalidate_latest()
            return False
        return published

    def refresh(self) -> bool:
        normalized, image = _accepted_pair(
            self.base_url,
            vehicle_id=self.vehicle_id,
            timeout_s=self.timeout_s, cache=self._transaction_cache,
        )
        return self.publish_frame(normalized, image)


def read_host_telemetry_panel(
    base_url: str,
    *,
    normalized_decision: dict[str, Any],
    vehicle_id: str,
    timeout_s: float = 3.0,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Fetch the host's record and join it to the exact decision identity."""

    effective_now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    try:
        raw = fetch_host_telemetry_latest(base_url, timeout_s=timeout_s)
        point = normalize_host_telemetry_record(
            raw,
            now_ms=effective_now_ms,
            vehicle_id=vehicle_id,
        )
        try:
            panel = join_host_telemetry_to_decision(
                point,
                normalized_decision,
                vehicle_id=vehicle_id,
            )
            return _with_host_telemetry_interval_coverage(
                panel,
                base_url,
                vehicle_id=vehicle_id,
                point_sequence=point["host_tick"]["sequence"],
                timeout_s=timeout_s,
                now_ms=effective_now_ms,
            )
        except HostTelemetryError as latest_error:
            if latest_error.reason != "identity_mismatch":
                raise
            decision_identity = decision_publication_identity(normalized_decision)
            sequence = (raw.get("host_tick") or {}).get("sequence")
            if type(sequence) is not int or sequence < 1:
                raise latest_error
            history = fetch_host_telemetry_records(
                base_url,
                after_sequence=max(0, sequence - 128),
                limit=128,
                timeout_s=timeout_s,
            )
            raw_records = history.get("records")
            if not isinstance(raw_records, list):
                raise latest_error
            for raw_record in reversed(raw_records):
                if not _host_record_matches_identity(raw_record, decision_identity):
                    continue
                candidate = normalize_host_telemetry_record(
                    raw_record,
                    now_ms=effective_now_ms,
                    vehicle_id=vehicle_id,
                )
                panel = join_host_telemetry_to_decision(
                    candidate,
                    normalized_decision,
                    vehicle_id=vehicle_id,
                )
                return _with_host_telemetry_interval_coverage(
                    panel,
                    base_url,
                    vehicle_id=vehicle_id,
                    point_sequence=candidate["host_tick"]["sequence"],
                    timeout_s=timeout_s,
                    now_ms=effective_now_ms,
                    initial_history=history,
                )
            else:
                raise latest_error
    except HostTelemetryError as exc:
        return unavailable_host_telemetry_panel(exc.reason, message=exc.message_text)
    except (ConnectionError, OSError, TypeError, ValueError) as exc:
        return unavailable_host_telemetry_panel(
            "publisher_missing",
            message=f"Host telemetry is unavailable: {type(exc).__name__}: {exc}",
        )


def _with_host_telemetry_interval_coverage(
    panel: dict[str, Any],
    base_url: str,
    *,
    vehicle_id: str,
    point_sequence: int,
    timeout_s: float,
    now_ms: int,
    initial_history: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Add bounded history coverage without changing the joined point.

    The live panel is attached to one exact decision frame, but a point alone
    cannot prove temporal coverage.  Read a short history window around that
    point and replace only the panel's coverage summary with the normalized
    result.  The original point coverage remains in ``details`` for review.
    Any history failure is retained as an explicit limited/unavailable
    coverage result; it never turns the joined point into a synthetic success.
    """

    point_coverage = panel.get("coverage")
    point_coverage = point_coverage if isinstance(point_coverage, dict) else {}
    records_result: dict[str, Any]
    try:
        window = 8
        after_sequence = max(0, point_sequence - window)
        if initial_history is None:
            payload = fetch_host_telemetry_records(
                base_url,
                after_sequence=after_sequence,
                limit=window,
                timeout_s=timeout_s,
            )
        else:
            raw_records = initial_history.get("records")
            raw_records = raw_records if isinstance(raw_records, list) else []
            bounded_records = [
                record
                for record in raw_records
                if isinstance(record, dict)
                and isinstance(record.get("host_tick"), dict)
                and type(record["host_tick"].get("sequence")) is int
                and after_sequence < record["host_tick"]["sequence"] <= point_sequence
            ]
            payload = {
                **initial_history,
                "records": bounded_records,
            }
        records_result = normalize_host_telemetry_records(
            payload,
            now_ms=now_ms,
            after_sequence=after_sequence,
            limit=window,
            vehicle_id=vehicle_id,
        )

        # The latest endpoint and the history endpoint are independent reads.
        # A live producer can publish the joined point between those reads, so
        # give the bounded history route one short retry before reporting an
        # honest coverage gap.  This only applies to the normal latest-point
        # path; the identity-matching fallback already has a history snapshot.
        point_present = any(
            isinstance(record, dict)
            and isinstance(record.get("host_tick"), dict)
            and record["host_tick"].get("sequence") == point_sequence
            for record in records_result.get("records", [])
        )
        if initial_history is None and not point_present:
            time.sleep(min(0.05, max(0.0, timeout_s / 20)))
            try:
                retry_payload = fetch_host_telemetry_records(
                    base_url,
                    after_sequence=after_sequence,
                    limit=window,
                    timeout_s=timeout_s,
                )
                records_result = normalize_host_telemetry_records(
                    retry_payload,
                    now_ms=now_ms,
                    after_sequence=after_sequence,
                    limit=window,
                    vehicle_id=vehicle_id,
                )
            except (HostTelemetryError, ConnectionError, OSError, TypeError, ValueError):
                pass
    except (HostTelemetryError, ConnectionError, OSError, TypeError, ValueError) as exc:
        reason = exc.reason if isinstance(exc, HostTelemetryError) else "publisher_missing"
        records_result = {
            "status": "unavailable" if reason == "publisher_missing" else "limited",
            "reason": reason,
            "records": [],
            "coverage": {
                "status": "unavailable",
                "reason": reason,
                "interval_covered": False,
                "record_count": 0,
            },
        }

    coverage = records_result.get("coverage")
    coverage = coverage if isinstance(coverage, dict) else {}
    sequences = {
        item.get("host_tick", {}).get("sequence")
        for item in records_result.get("records", [])
        if isinstance(item, dict) and isinstance(item.get("host_tick"), dict)
    }
    if point_sequence not in sequences:
        coverage = {
            **coverage,
            "status": "limited",
            "reason": "coverage_gap",
            "interval_covered": False,
            "coverage_reason": "joined_point_outside_history",
        }

    enriched = dict(panel)
    enriched["coverage"] = {
        **coverage,
        "sequence": point_sequence,
        "point_status": point_coverage.get("status"),
        "point_interval_covered": point_coverage.get("interval_covered") is True,
    }
    details = panel.get("details")
    details = dict(details) if isinstance(details, dict) else {}
    details["point_coverage"] = point_coverage
    details["history_status"] = records_result.get("status")
    details["history_reason"] = records_result.get("reason", "")
    enriched["details"] = details
    return enriched


def _host_record_matches_identity(
    record: object,
    decision_identity: dict[str, Any],
) -> bool:
    if not isinstance(record, dict):
        return False
    activation = record.get("activation")
    source_frame = record.get("source_frame")
    if not isinstance(activation, dict) or not isinstance(source_frame, dict):
        return False
    return {
        "vehicle_id": record.get("vehicle_id"),
        "source_id": record.get("source_id"),
        "run_id": record.get("run_id"),
        "generation_id": record.get("generation_id"),
        "activation": {
            "generation_id": activation.get("generation_id"),
        },
        "source_frame": {
            "frame_id": source_frame.get("frame_id"),
            "frame_index": source_frame.get("frame_index"),
            "captured_at_ms": source_frame.get("captured_at_ms"),
            "completed_at_ms": source_frame.get("completed_at_ms"),
        },
    } == decision_identity
