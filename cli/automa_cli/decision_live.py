"""Read-only live consumer adapter for the accepted host telemetry seam.

This module intentionally does not assume PR #202's live decision server.  It
can be called by that server when it exists, or by a later physical evidence
owner, while keeping telemetry fetch and interval coverage independent from
decision authority.
"""

from __future__ import annotations

import time
from typing import Any

from .decision_view import (
    build_decision_host_telemetry_capture,
    project_decision_with_host_telemetry,
    unavailable_host_telemetry_panel,
)
from .physical_observation import (
    HostTelemetryError,
    fetch_host_telemetry_capture,
    fetch_host_telemetry_latest,
    join_host_telemetry_to_decision,
    normalize_host_telemetry_record,
)


def read_host_telemetry_panel(
    base_url: str,
    *,
    normalized_decision: dict[str, Any],
    vehicle_id: str,
    timeout_s: float = 3.0,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Fetch and exactly join one latest telemetry point to a decision."""

    effective_now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    try:
        raw = fetch_host_telemetry_latest(base_url, timeout_s=timeout_s)
        point = normalize_host_telemetry_record(
            raw,
            now_ms=effective_now_ms,
            vehicle_id=vehicle_id,
        )
        return join_host_telemetry_to_decision(
            point,
            normalized_decision,
            vehicle_id=vehicle_id,
        )
    except HostTelemetryError as exc:
        return unavailable_host_telemetry_panel(
            exc.reason,
            message=exc.message_text,
        )
    except (ConnectionError, OSError, TypeError, ValueError) as exc:
        return unavailable_host_telemetry_panel(
            "publisher_missing",
            message=f"Host telemetry is unavailable: {type(exc).__name__}: {exc}",
        )


def read_host_telemetry_capture(
    base_url: str,
    *,
    normalized_decision: dict[str, Any],
    vehicle_id: str,
    after_sequence: int = 0,
    limit: int = 128,
    timeout_s: float = 3.0,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Fetch the latest point plus bounded history for a capture package."""

    return fetch_host_telemetry_capture(
        base_url,
        normalized_decision=normalized_decision,
        vehicle_id=vehicle_id,
        after_sequence=after_sequence,
        limit=limit,
        now_ms=now_ms,
        timeout_s=timeout_s,
    )


def project_live_decision_payload(
    decision_payload: dict[str, Any],
    host_telemetry_panel: dict[str, Any],
) -> dict[str, Any]:
    """Return a live payload with telemetry as an additive sibling."""

    return project_decision_with_host_telemetry(
        decision_payload,
        host_telemetry_panel,
    )


def build_live_decision_capture(
    *,
    decision_payload: dict[str, Any],
    host_telemetry_panel: dict[str, Any],
    records_result: dict[str, Any] | None = None,
    vehicle_id: str | None = None,
) -> dict[str, Any]:
    """Pair an existing decision payload with a separate telemetry capture."""

    return build_decision_host_telemetry_capture(
        decision_payload=decision_payload,
        panel=host_telemetry_panel,
        records_result=records_result,
        vehicle_id=vehicle_id,
    )


__all__ = [
    "build_live_decision_capture",
    "project_live_decision_payload",
    "read_host_telemetry_capture",
    "read_host_telemetry_panel",
]
