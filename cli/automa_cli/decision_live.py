"""Read-only adapter from physical decision publications to RuntimeViewServer."""

from __future__ import annotations

import threading
import time
import webbrowser
from dataclasses import dataclass
from typing import Any, TextIO

from .decision import (
    CommandResult,
    DecisionSurfaceError,
    accept_physical_decision_publication,
    physical_decision_view_frame,
)
from .decision_view import (
    build_decision_host_telemetry_capture,
    project_decision_with_host_telemetry,
    unavailable_host_telemetry_panel,
)
from .physical_observation import (
    fetch_decision_publication,
    fetch_observation_frame,
    frame_id_from_headers,
    HostTelemetryError,
    fetch_host_telemetry_capture,
    fetch_host_telemetry_latest,
    fetch_host_telemetry_records,
    join_host_telemetry_to_decision,
    normalize_host_telemetry_record,
    normalize_host_telemetry_records,
    physical_decision_identity,
    physical_observation_dir,
    picar_base_url,
)
from .runtime_view import RuntimeViewServer
from .vehicles import (
    discover_active_vehicles,
    find_vehicle_by_id,
    format_active_vehicles_snapshot,
)


@dataclass(frozen=True)
class _ResolvedPhysicalVehicle:
    vehicle_id: str
    base_url: str


def _resolve_physical_vehicle(
    vehicle_id: str,
    *,
    timeout_s: float,
) -> tuple[_ResolvedPhysicalVehicle | None, str | None]:
    discovery = discover_active_vehicles(
        timeout_s=timeout_s,
        include_picar=True,
        include_chase_sim=False,
        include_inactive=True,
    )
    vehicle, error = find_vehicle_by_id(discovery, vehicle_id)
    if error:
        return None, "\n\n".join(
            [
                error,
                "Discovery snapshot:",
                format_active_vehicles_snapshot(discovery, include_inactive=True),
            ]
        )
    if vehicle is None:
        return None, f"Vehicle {vehicle_id!r} was not found."
    if vehicle.get("provider") != "picar":
        return None, f"Live decision view supports PiCar only; got {vehicle.get('provider')!r}."
    base_url = picar_base_url(vehicle)
    if not base_url:
        return None, f"Vehicle {vehicle_id!r} has no physical base URL."
    return _ResolvedPhysicalVehicle(vehicle_id, base_url), None


def _provider_identity(normalized: dict[str, Any]) -> dict[str, Any]:
    return {
        "vehicle_id": normalized["vehicle_id"],
        "source_id": normalized["source_id"],
        "run_id": normalized["run_id"],
        "activation_engine_id": normalized["activation_engine_id"],
        "activation_activated_at_ms": normalized["activation_activated_at_ms"],
        "producer_generation_id": normalized["generation_id"],
    }


def _frame_record(normalized: dict[str, Any]) -> dict[str, Any]:
    cycle = normalized["decision"]["cycle"]
    source = cycle.get("source") if isinstance(cycle, dict) else None
    observation = source.get("observation") if isinstance(source, dict) else None
    observation_value = (
        observation.get("value")
        if isinstance(observation, dict) and observation.get("status") == "ready"
        else None
    )
    observation_value = observation_value if isinstance(observation_value, dict) else None
    memory = source.get("memory") if isinstance(source, dict) else None
    memory_value = (
        memory.get("value")
        if isinstance(memory, dict) and memory.get("status") == "ready"
        else None
    )
    memory_value = memory_value if isinstance(memory_value, dict) else None
    sensor_snapshot = (
        observation_value.get("sensor_snapshot")
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
    return {
        "frame_id": normalized["frame_id"],
        "frame_index": normalized["frame_index"],
        "captured_at_ms": normalized["timestamp_ms"],
        "run_id": normalized["run_id"],
        "sensor_snapshot": sensor_snapshot,
        "perception_completed_at_ms": (
            observation_value.get("created_at_ms")
            if isinstance(observation_value, dict)
            else None
        ),
        "perception": perception,
        "observation": observation_value,
        "memory": memory_value,
        "algorithm": (
            observation_value.get("perception_plugin_id")
            if isinstance(observation_value, dict)
            else None
        ),
        "action_policy": "observe_only",
        "control_source": "physical_onboard",
        "control_application": "donkey_drive_mode",
    }


def _accepted_pair(
    base_url: str,
    *,
    vehicle_id: str,
    timeout_s: float,
) -> tuple[dict[str, Any], tuple[bytes, str]]:
    """Read image first and accept only an exact physical decision/image pair."""

    deadline = time.monotonic() + min(1.0, max(0.2, float(timeout_s)))
    last_error = "matched physical decision image is unavailable"
    while time.monotonic() < deadline:
        image_bytes, image_headers = fetch_observation_frame(base_url, timeout_s=timeout_s)
        image_frame_id = frame_id_from_headers(image_headers)
        publication = fetch_decision_publication(base_url, timeout_s=timeout_s)
        try:
            normalized = accept_physical_decision_publication(
                publication,
                vehicle_id=vehicle_id,
                now_ms=int(time.time() * 1000),
            )
        except DecisionSurfaceError as exc:
            # PiCar and the CLI may have a few milliseconds of clock skew.
            # Keep the future-dated rejection fail-closed, but retry the
            # read-only pair while the published cycle becomes current.
            if exc.details.get("reason") != "future_dated":
                raise
            last_error = exc.message_text
            time.sleep(0.04)
            continue
        if image_frame_id != normalized["frame_id"]:
            last_error = (
                f"image frame {image_frame_id!r} did not match decision frame "
                f"{normalized['frame_id']!r}"
            )
            time.sleep(0.04)
            continue
        content_type = image_headers.get("content-type", "").split(";", 1)[0].strip()
        if content_type not in {"image/jpeg", "image/png"}:
            raise ValueError(f"unsupported physical decision image type {content_type!r}")
        return normalized, (image_bytes, content_type)
    raise ConnectionError(last_error)


class PhysicalDecisionViewAdapter:
    """Publish accepted PiRacer transactions through the shared decision view."""

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

    def publish_snapshot(
        self,
        normalized: dict[str, Any],
        image: tuple[bytes, str],
    ) -> bool:
        frame_record = _frame_record(normalized)
        stream_frame = physical_decision_view_frame(normalized)
        frame_record["host_telemetry"] = read_host_telemetry_panel(
            self.base_url,
            normalized_decision=normalized,
            vehicle_id=self.vehicle_id,
            timeout_s=self.timeout_s,
        )
        published = self.view_server.decision.publish_provider_transaction(
            stream_frame=stream_frame,
            frame_record=frame_record,
            image=image,
        )
        if not published:
            self.view_server.decision.invalidate_latest()
            return False

        # Keep the physical decision, perception, and memory pages on one
        # RuntimeViewServer session. The decision image is already the exact
        # matched image, so do not fetch a second potentially different frame.
        try:
            image_bytes, content_type = image
            suffix = ".png" if content_type == "image/png" else ".jpg"
            frame_path = self.view_server.automation_dir / f"latest_frame{suffix}"
            frame_path.write_bytes(image_bytes)
            self.view_server.perception.publish_frame(
                frame_path=frame_path,
                frame_record=frame_record,
            )
            self.view_server.perception.publish_perception(frame_record=frame_record)
        except Exception:  # noqa: BLE001 - side view publication is nonfatal
            pass
        return published

    def refresh(self) -> bool:
        normalized, image = _accepted_pair(
            self.base_url,
            vehicle_id=self.vehicle_id,
            timeout_s=self.timeout_s,
        )
        return self.publish_snapshot(normalized, image)


def run_live_decision_monitor(
    *,
    vehicle_id: str,
    port: int = 0,
    open_browser: bool = False,
    timeout_s: float = 2.0,
    output: TextIO | None = None,
) -> CommandResult:
    """Serve the shared RuntimeViewServer decision page until Ctrl-C."""

    if not 0 <= int(port) <= 65535:
        return CommandResult(2, "--port must be between 0 and 65535.")
    try:
        resolved, error = _resolve_physical_vehicle(
            vehicle_id,
            timeout_s=max(0.1, float(timeout_s)),
        )
    except (OSError, TypeError, ValueError) as exc:
        return CommandResult(2, f"Vehicle discovery failed: {type(exc).__name__}: {exc}")
    if error is not None or resolved is None:
        return CommandResult(2, error or f"Vehicle {vehicle_id!r} could not be resolved.")

    server: RuntimeViewServer | None = None
    try:
        normalized, image = _accepted_pair(
            resolved.base_url,
            vehicle_id=resolved.vehicle_id,
            timeout_s=timeout_s,
        )
        server = RuntimeViewServer(
            vehicle_id=resolved.vehicle_id,
            automation_dir=physical_observation_dir(vehicle_id),
            port=port,
            run_id=normalized["run_id"],
            decision_provider_identity=_provider_identity(normalized),
        ).start()
        adapter = PhysicalDecisionViewAdapter(
            vehicle_id=resolved.vehicle_id,
            base_url=resolved.base_url,
            view_server=server,
            timeout_s=max(0.1, float(timeout_s)),
        )
        if not adapter.publish_snapshot(normalized, image):
            return CommandResult(2, "physical decision transaction was rejected")
        page_path = server.decision.page_url()
        if page_path is None or server.url is None:
            return CommandResult(2, "physical decision view did not expose a generation URL")
        view_url = f"{server.url.rstrip('/')}{page_path}"
        if output is not None:
            print(
                f"Live decision view: {view_url}\n"
                f"Vehicle: {resolved.vehicle_id} ({resolved.base_url})\n"
                "Read-only shadow view; no vehicle commands are sent. Ctrl-C stops it.",
                file=output,
                flush=True,
            )
        if open_browser and not webbrowser.open(view_url, new=2) and output is not None:
            print(f"Open the view manually: {view_url}", file=output, flush=True)
        while True:
            try:
                adapter.refresh()
            except Exception:  # noqa: BLE001 - every incomplete refresh fails closed
                server.decision.invalidate_latest()
            threading.Event().wait(0.2)
    except KeyboardInterrupt:
        return CommandResult(0, "Live decision view stopped.")
    except (OSError, ValueError, TypeError, ConnectionError) as exc:
        return CommandResult(2, f"live decision view unavailable: {type(exc).__name__}: {exc}")
    finally:
        if server is not None:
            server.stop()


def read_host_telemetry_panel(
    base_url: str,
    *,
    normalized_decision: dict[str, Any],
    vehicle_id: str,
    timeout_s: float = 3.0,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Fetch the PiCar record and join it to the exact decision identity."""

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
            decision_identity = physical_decision_identity(normalized_decision)
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
            "engine_id": activation.get("engine_id"),
            "activated_at_ms": activation.get("activated_at_ms"),
            "generation_id": activation.get("generation_id"),
        },
        "source_frame": {
            "frame_id": source_frame.get("frame_id"),
            "frame_index": source_frame.get("frame_index"),
            "captured_at_ms": source_frame.get("captured_at_ms"),
            "completed_at_ms": source_frame.get("completed_at_ms"),
        },
    } == decision_identity


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
    return project_decision_with_host_telemetry(decision_payload, host_telemetry_panel)


def build_live_decision_capture(
    *,
    decision_payload: dict[str, Any],
    host_telemetry_panel: dict[str, Any],
    records_result: dict[str, Any] | None = None,
    vehicle_id: str | None = None,
) -> dict[str, Any]:
    return build_decision_host_telemetry_capture(
        decision_payload=decision_payload,
        panel=host_telemetry_panel,
        records_result=records_result,
        vehicle_id=vehicle_id,
    )
