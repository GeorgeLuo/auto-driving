"""Read-only adapter from a runtime host's decision publications to RuntimeViewServer."""

from __future__ import annotations

import base64
import time
from typing import Any

from ..publications.decision import (
    DecisionSurfaceError,
    accept_decision_publication,
    decision_view_frame,
)
from ..publications.host_publications import (
    fetch_decision_transaction, TransactionCache,
    fetch_host_telemetry_latest,
)
from ..publications.host_telemetry import (
    HostTelemetryError,
    host_telemetry_failure,
    join_host_telemetry_to_decision,
    normalize_host_telemetry_record,
)
from ...frontend.server import RuntimeViewServer
from ...shared.memory_report import evidence_records


def _provider_identity(normalized: dict[str, Any]) -> dict[str, Any]:
    return {
        "host_name": normalized["host_name"],
        "source_id": normalized["source_id"],
        "run_id": normalized["run_id"],
        "producer_generation_id": normalized["generation_id"],
    }


def _frame_record(normalized: dict[str, Any]) -> dict[str, Any]:
    mode = normalized["decision"]["application"]["mode"]
    cycle = normalized["decision"]["cycle"]
    observation_value = cycle["observation"]
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
            "schema": observation_value.get("perception_schema") or "perception_text",
            "status": "ok",
            "text": "\n".join(summary),
            "lines": summary,
            "signals": observation_value.get("signals")
            if isinstance(observation_value.get("signals"), list)
            else [],
            "things": observation_value.get("things")
            if isinstance(observation_value.get("things"), list)
            else [],
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
        "memory": cycle["memory"],
        "memory_evidence": evidence_records(cycle["memory"]),
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
    host_name: str,
    timeout_s: float,
    cache: TransactionCache | None = None,
) -> tuple[dict[str, Any], tuple[bytes, str]]:
    """Accept the decision and image the host published as one transaction."""

    deadline = time.monotonic() + min(1.0, max(0.2, float(timeout_s)))
    last_error = "matched decision image is unavailable"
    while time.monotonic() < deadline:
        transaction = fetch_decision_transaction(base_url, timeout_s=timeout_s, cache=cache)
        publication = transaction.get("publication") if isinstance(transaction, dict) else None
        try:
            normalized = accept_decision_publication(
                publication,
                host_name=host_name,
                now_ms=int(time.time() * 1000),
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
        host_name: str,
        base_url: str,
        view_server: RuntimeViewServer,
        timeout_s: float,
    ) -> None:
        self.host_name = host_name
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
            host_name=self.host_name,
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
            host_name=self.host_name,
            timeout_s=self.timeout_s, cache=self._transaction_cache,
        )
        return self.publish_frame(normalized, image)


def read_host_telemetry_panel(
    base_url: str,
    *,
    normalized_decision: dict[str, Any],
    host_name: str,
    timeout_s: float = 3.0,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Read one latest host telemetry point and join it to this decision.

    A refresh performs one latest read. Identity, freshness, and transport
    results stay attached to that read.
    """

    effective_now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    try:
        raw = fetch_host_telemetry_latest(base_url, timeout_s=timeout_s)
        point = normalize_host_telemetry_record(
            raw,
            now_ms=effective_now_ms,
            host_name=host_name,
        )
        return join_host_telemetry_to_decision(
            point,
            normalized_decision,
            host_name=host_name,
        )
    except HostTelemetryError as exc:
        return host_telemetry_failure(exc.reason, message=exc.message_text)
    except (ConnectionError, OSError, TypeError, ValueError) as exc:
        return host_telemetry_failure(
            "publisher_missing",
            message=f"Host telemetry is unavailable: {type(exc).__name__}: {exc}",
        )
