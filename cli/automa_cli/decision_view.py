"""Read-only live projection of accepted shadow decision cycles.

The runtime server owns this short-lived view.  It receives an accepted
``vehicle_decision_stream_frame_v0`` and the already-published capture bytes
for that frame, retaining a small number of immutable transactions.  It never
runs an engine, reads an image path, or follows a URL supplied by a client.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .decision import DECISION_STREAM_MAX_AGE_MS, accept_decision_stream_frame
from .perception_view import VIEW_RECORD_NAME
from .physical_observation import (
    HOST_TELEMETRY_PANEL_SCHEMA,
    build_host_telemetry_capture,
    host_telemetry_failure,
)


DECISION_VIEW_SCHEMA = "automa_live_decision_view_v1"
DECISION_VIEW_ID = "decision-combined-v0"
MAX_RETAINED_TRANSACTIONS = 8
MAX_PROBE_BYTES = 1024 * 1024


class DecisionViewError(Exception):
    """A bounded, operator-readable refusal for one decision-view request."""

    def __init__(self, status_code: int, reason: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class _Transaction:
    transaction_id: str
    stream_frame: dict[str, Any]
    frame_record: dict[str, Any]
    image_bytes: bytes
    content_type: str


def _now_ms() -> int:
    return int(time.time() * 1000)


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _supported_image_bbox(location: Any) -> bool:
    """Return whether D2 can render this location without coercion or guessing."""

    if not isinstance(location, dict) or location.get("frame") != "image":
        return False
    raw = location.get("bbox_xyxy_norm")
    if not isinstance(raw, list) or len(raw) != 4:
        return False
    values: list[float] = []
    for value in raw:
        if type(value) not in {int, float} or not math.isfinite(value):
            return False
        normalized = float(value)
        if not 0.0 <= normalized <= 1.0:
            return False
        values.append(normalized)
    x1, y1, x2, y2 = values
    return x2 > x1 and y2 > y1


def _evidence_projection(
    *,
    transaction_id: str,
    frame_id: str,
    observation: Any,
    memory: Any,
) -> dict[str, Any]:
    """Project only evidence that is exactly attributable to the current image.

    The accepted cycle remains available unchanged under ``provenance``. This
    projection is the machine-visible rendering gate: an unavailable record
    carries its raw provenance but never carries renderable geometry.
    """

    observation_value = (
        observation.get("value")
        if isinstance(observation, dict) and observation.get("status") == "ready"
        else None
    )
    memory_value = (
        memory.get("value")
        if isinstance(memory, dict) and memory.get("status") == "ready"
        else None
    )
    records = memory_value.get("records") if isinstance(memory_value, dict) else None
    raw_records = records if isinstance(records, list) else []
    observation_id = (
        observation_value.get("observation_id")
        if isinstance(observation_value, dict)
        else None
    )
    observation_things = (
        observation_value.get("things")
        if isinstance(observation_value, dict)
        else None
    )
    things = observation_things if isinstance(observation_things, list) else []
    observation_plugin_id = (
        observation_value.get("perception_plugin_id")
        if isinstance(observation_value, dict)
        else None
    )

    projected: list[dict[str, Any]] = []
    for record in raw_records:
        record_id = record.get("record_id") if isinstance(record, dict) else None
        provenance = record.get("provenance") if isinstance(record, dict) else None
        reason = ""
        matched_thing: dict[str, Any] | None = None
        if not isinstance(record, dict) or type(record_id) is not str or not record_id:
            reason = "invalid_record"
        elif not isinstance(provenance, dict):
            reason = "provenance_unavailable"
        elif provenance.get("frame_id") != frame_id:
            reason = "source_image_unavailable"
        elif type(observation_id) is not str or not observation_id:
            reason = "observation_unavailable"
        elif provenance.get("observation_id") != observation_id:
            reason = "observation_mismatch"
        elif type(provenance.get("evidence_id")) is not str or not provenance.get("evidence_id"):
            reason = "provenance_unavailable"
        else:
            matches = [
                thing
                for thing in things
                if isinstance(thing, dict)
                and thing.get("thing_id") == provenance["evidence_id"]
            ]
            if not matches:
                reason = "evidence_missing"
            elif len(matches) != 1:
                reason = "evidence_ambiguous"
            else:
                matched_thing = matches[0]

        if not reason and matched_thing is not None:
            source_plugin_id = matched_thing.get("source_plugin_id")
            if source_plugin_id is None:
                source_plugin_id = observation_plugin_id
            if (
                provenance.get("coordinate_frame") != "image"
                or provenance.get("source_plugin_id") != source_plugin_id
            ):
                reason = "provenance_mismatch"
            elif not _supported_image_bbox(record.get("location")):
                reason = "unsupported_geometry"
            elif record.get("location") != matched_thing.get("location"):
                reason = "geometry_mismatch"
            elif (
                record.get("kind") != matched_thing.get("kind")
                or record.get("label") != matched_thing.get("label")
            ):
                reason = "evidence_mismatch"

        available = not reason
        projected.append(
            {
                "record_id": record_id if type(record_id) is str else None,
                "status": "available" if available else "unavailable",
                "reason": reason,
                "provenance": _json_copy(provenance) if isinstance(provenance, dict) else None,
                "record": _json_copy(record) if available else None,
            }
        )

    available_count = sum(item["status"] == "available" for item in projected)
    if available_count == len(projected) and projected:
        status = "available"
        reason = ""
    elif available_count:
        status = "partial"
        reason = "evidence_association_partial"
    else:
        status = "unavailable"
        reason = "evidence_association_unavailable"
    return {
        "status": status,
        "reason": reason,
        "transaction_id": transaction_id,
        "frame_id": frame_id,
        "available_count": available_count,
        "record_count": len(projected),
        "records": projected,
    }


def _activation_identity(activation: dict[str, Any]) -> dict[str, Any]:
    decision = activation.get("decision") if isinstance(activation, dict) else None
    if not isinstance(decision, dict):
        raise ValueError("decision activation has no decision section")
    engine_id = decision.get("engine_id")
    activated_at_ms = activation.get("activated_at_ms")
    if type(engine_id) is not str or not engine_id:
        raise ValueError("decision activation engine_id is invalid")
    if type(activated_at_ms) is not int:
        raise ValueError("decision activation activated_at_ms is invalid")
    signed = {
        "engine_id": engine_id,
        "engine_spec": decision.get("engine_spec"),
        "engine_config": decision.get("engine_config"),
        "activated_at_ms": activated_at_ms,
    }
    return {
        "activation_engine_id": engine_id,
        "activation_activated_at_ms": activated_at_ms,
        "activation_sha256": _sha256_json(signed),
    }


def decision_view_identity(
    *,
    vehicle_id: str,
    run_id: str,
    worker_pid: int,
    activation: dict[str, Any],
) -> dict[str, Any]:
    if type(vehicle_id) is not str or not vehicle_id:
        raise ValueError("decision view vehicle_id is invalid")
    if type(run_id) is not str or not run_id:
        raise ValueError("decision view run_id is invalid")
    if type(worker_pid) is not int or worker_pid <= 0:
        raise ValueError("decision view worker_pid is invalid")
    return {
        "vehicle_id": vehicle_id,
        "run_id": run_id,
        "worker_pid": worker_pid,
        **_activation_identity(activation),
    }


def provider_decision_view_identity(
    *,
    vehicle_id: str,
    source_id: str,
    run_id: str,
    activation_engine_id: str,
    activation_activated_at_ms: int,
    producer_generation_id: str,
) -> dict[str, Any]:
    """Identify a non-local producer without inventing local process state."""

    for field, value in (
        ("vehicle_id", vehicle_id),
        ("source_id", source_id),
        ("run_id", run_id),
        ("activation_engine_id", activation_engine_id),
        ("producer_generation_id", producer_generation_id),
    ):
        if type(value) is not str or not value:
            raise ValueError(f"decision view {field} is invalid")
    if type(activation_activated_at_ms) is not int:
        raise ValueError("decision view activation_activated_at_ms is invalid")
    return {
        "vehicle_id": vehicle_id,
        "source_id": source_id,
        "run_id": run_id,
        "activation_engine_id": activation_engine_id,
        "activation_activated_at_ms": activation_activated_at_ms,
        "producer_generation_id": producer_generation_id,
    }


def generation_id(identity: dict[str, Any]) -> str:
    local_expected = {
        "vehicle_id",
        "run_id",
        "worker_pid",
        "activation_engine_id",
        "activation_activated_at_ms",
        "activation_sha256",
    }
    provider_expected = {
        "vehicle_id",
        "source_id",
        "run_id",
        "activation_engine_id",
        "activation_activated_at_ms",
        "producer_generation_id",
    }
    if frozenset(identity) not in {frozenset(local_expected), frozenset(provider_expected)}:
        raise ValueError("decision view identity has an unexpected key set")
    return _sha256_json(identity)


def _parse_single_query(query: str, *, required: set[str]) -> dict[str, str]:
    try:
        parsed = parse_qs(query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise DecisionViewError(400, "invalid_query", "decision view query is invalid") from exc
    if set(parsed) != required or any(len(parsed[key]) != 1 for key in required):
        raise DecisionViewError(400, "invalid_query", "decision view query is invalid")
    values = {key: parsed[key][0] for key in required}
    for value in values.values():
        if not isinstance(value, str) or not value:
            raise DecisionViewError(400, "invalid_query", "decision view query is invalid")
    return values


def parse_generation_query(query: str) -> str:
    generation = _parse_single_query(query, required={"generation"})["generation"]
    if len(generation) != 64 or any(char not in "0123456789abcdef" for char in generation):
        raise DecisionViewError(400, "invalid_generation", "decision view generation is invalid")
    return generation


def parse_image_query(query: str) -> tuple[str, str]:
    values = _parse_single_query(query, required={"generation", "id"})
    generation = values["generation"]
    transaction_id = values["id"]
    if (
        len(generation) != 64
        or len(transaction_id) != 64
        or any(char not in "0123456789abcdef" for char in generation)
        or any(char not in "0123456789abcdef" for char in transaction_id)
    ):
        raise DecisionViewError(400, "invalid_image_id", "decision image identity is invalid")
    return generation, transaction_id


class DecisionView:
    """One runtime generation's bounded decision/image transactions."""

    def __init__(
        self,
        *,
        vehicle_id: str,
        run_id: str | None,
        worker_pid: int | None,
        activation: dict[str, Any] | None,
        activation_path: Path,
        provider_identity: dict[str, Any] | None = None,
    ) -> None:
        self._activation_path = Path(activation_path)
        self._startup_activation = _json_copy(activation) if isinstance(activation, dict) else None
        self.identity: dict[str, Any] | None = None
        self.generation_id: str | None = None
        self._provider_identity = (
            _json_copy(provider_identity) if isinstance(provider_identity, dict) else None
        )
        if self._provider_identity is not None:
            self.identity = provider_decision_view_identity(**self._provider_identity)
            self.generation_id = generation_id(self.identity)
        elif self._startup_activation is not None and run_id is not None and worker_pid is not None:
            self.identity = decision_view_identity(
                vehicle_id=vehicle_id,
                run_id=run_id,
                worker_pid=worker_pid,
                activation=self._startup_activation,
            )
            self.generation_id = generation_id(self.identity)
        self._lock = threading.Lock()
        self._transactions: OrderedDict[str, _Transaction] = OrderedDict()
        self._latest_transaction_id: str | None = None
        self._stopped = False

    def health_payload(self) -> dict[str, Any]:
        with self._lock:
            return {
                "available": self.identity is not None and not self._stopped,
                "status": (
                    "stopped"
                    if self._stopped
                    else "warming"
                    if self.identity is not None and self._latest_transaction_id is None
                    else "running"
                    if self.identity is not None
                    else "unavailable"
                ),
                "generation_id": self.generation_id,
                "identity": _json_copy(self.identity) if self.identity is not None else None,
                "latest_transaction_id": self._latest_transaction_id,
            }

    def page_url(self) -> str | None:
        if self.generation_id is None:
            return None
        return f"/decision?{urlencode({'generation': self.generation_id})}"

    def require_generation(self, generation: str) -> None:
        if self.generation_id is None or self.identity is None:
            raise DecisionViewError(503, "decision_unavailable", "decision view is not configured")
        if self._stopped:
            raise DecisionViewError(503, "producer_stopped", "decision view producer is stopped")
        if generation != self.generation_id:
            raise DecisionViewError(409, "generation_mismatch", "decision view belongs to another generation")

    def invalidate_latest(self) -> None:
        """Hide a cached success when no complete transaction is available."""

        with self._lock:
            self._latest_transaction_id = None

    def publish(
        self,
        *,
        stream_frame: dict[str, Any],
        frame_record: dict[str, Any],
        image: tuple[bytes, str] | None,
    ) -> bool:
        """Store one exact transaction only after all local identities agree."""

        if self.identity is None or self._startup_activation is None or image is None:
            self.invalidate_latest()
            return False
        try:
            accept_decision_stream_frame(
                stream_frame,
                activation=self._startup_activation,
                automation_state={
                    "run_id": self.identity["run_id"],
                    "status": "running",
                    "pid": self.identity["worker_pid"],
                },
                now_ms=stream_frame.get("published_at_ms"),
                is_pid_alive=lambda _pid: True,
            )
            if any(
                stream_frame.get(key) != self.identity[key]
                for key in (
                    "vehicle_id",
                    "run_id",
                    "worker_pid",
                    "activation_engine_id",
                    "activation_activated_at_ms",
                )
            ):
                self.invalidate_latest()
                return False
            frame_id = stream_frame.get("frame_id")
            if frame_record.get("frame_id") != frame_id:
                self.invalidate_latest()
                return False
            if frame_record.get("run_id") != self.identity["run_id"]:
                self.invalidate_latest()
                return False
            if frame_record.get("worker_pid") != self.identity["worker_pid"]:
                self.invalidate_latest()
                return False
            image_bytes, content_type = image
            if (
                type(image_bytes) is not bytes
                or not image_bytes
                or content_type not in {"image/png", "image/jpeg"}
                or not self._activation_matches()
            ):
                self.invalidate_latest()
                return False
            copied_stream = _json_copy(stream_frame)
            copied_record = _json_copy(frame_record)
        except Exception:  # noqa: BLE001 - publication must not affect the worker cycle
            # DecisionSurfaceError is intentionally not imported: this owner is
            # fail-closed for every bad publication shape.
            self.invalidate_latest()
            return False

        return self._store_transaction(
            stream_frame=copied_stream,
            frame_record=copied_record,
            image_bytes=image_bytes,
            content_type=content_type,
        )

    def publish_provider_transaction(
        self,
        *,
        stream_frame: dict[str, Any],
        frame_record: dict[str, Any],
        image: tuple[bytes, str] | None,
    ) -> bool:
        """Store one already-accepted provider transaction without local PID state."""

        if self.identity is None or self._provider_identity is None or image is None:
            self.invalidate_latest()
            return False
        try:
            if (
                not isinstance(stream_frame, dict)
                or not isinstance(frame_record, dict)
                or not isinstance(image, tuple)
                or len(image) != 2
            ):
                raise ValueError("provider transaction shape is invalid")
            expected = {
                "vehicle_id": stream_frame.get("vehicle_id"),
                "source_id": stream_frame.get("source_id"),
                "run_id": stream_frame.get("run_id"),
                "activation_engine_id": stream_frame.get("activation_engine_id"),
                "activation_activated_at_ms": stream_frame.get("activation_activated_at_ms"),
                "producer_generation_id": stream_frame.get("producer_generation_id"),
            }
            cycle = stream_frame.get("cycle")
            frame_id = stream_frame.get("frame_id")
            image_bytes, content_type = image
            if expected != self.identity:
                raise ValueError("provider identity changed")
            if (
                type(frame_id) is not str
                or not frame_id
                or type(stream_frame.get("published_at_ms")) is not int
            ):
                raise ValueError("provider frame identity is invalid")
            if not isinstance(cycle, dict) or cycle.get("frame_id") != frame_id:
                raise ValueError("provider cycle frame does not match")
            if frame_record.get("frame_id") != frame_id:
                raise ValueError("provider image frame does not match")
            if frame_record.get("run_id") != self.identity["run_id"]:
                raise ValueError("provider image run does not match")
            if (
                type(image_bytes) is not bytes
                or not image_bytes
                or content_type not in {"image/png", "image/jpeg"}
            ):
                raise ValueError("provider image is invalid")
            copied_stream = _json_copy(stream_frame)
            copied_record = _json_copy(frame_record)
        except Exception:  # noqa: BLE001 - publication must fail closed
            self.invalidate_latest()
            return False

        return self._store_transaction(
            stream_frame=copied_stream,
            frame_record=copied_record,
            image_bytes=image_bytes,
            content_type=content_type,
        )

    def _store_transaction(
        self,
        *,
        stream_frame: dict[str, Any],
        frame_record: dict[str, Any],
        image_bytes: bytes,
        content_type: str,
    ) -> bool:
        transaction_id = _sha256_json(
            {
                "generation": self.generation_id,
                "frame_id": stream_frame["frame_id"],
                "published_at_ms": stream_frame["published_at_ms"],
                "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
            }
        )
        transaction = _Transaction(
            transaction_id=transaction_id,
            stream_frame=stream_frame,
            frame_record=frame_record,
            image_bytes=bytes(image_bytes),
            content_type=content_type,
        )
        with self._lock:
            if self._stopped:
                return False
            self._transactions[transaction_id] = transaction
            self._transactions.move_to_end(transaction_id)
            while len(self._transactions) > MAX_RETAINED_TRANSACTIONS:
                self._transactions.popitem(last=False)
            self._latest_transaction_id = transaction_id
        return True

    def latest_payload(self, *, generation: str, now_ms: int | None = None) -> dict[str, Any]:
        self.require_generation(generation)
        if not self._activation_matches():
            raise DecisionViewError(
                503,
                "activation_mismatch",
                "decision activation no longer matches this producer generation",
            )
        with self._lock:
            transaction_id = self._latest_transaction_id
            transaction = (
                self._transactions.get(transaction_id)
                if transaction_id is not None
                else None
            )
        if transaction is None:
            raise DecisionViewError(503, "decision_warming", "no accepted decision cycle is available")
        served_at_ms = _now_ms() if now_ms is None else now_ms
        published_at_ms = transaction.stream_frame["published_at_ms"]
        captured_at_ms = transaction.frame_record.get("captured_at_ms")
        age_ms = served_at_ms - published_at_ms
        if not 0 <= age_ms <= DECISION_STREAM_MAX_AGE_MS:
            raise DecisionViewError(503, "decision_stale", "accepted decision cycle has expired")
        capture_age_ms = (
            served_at_ms - captured_at_ms if type(captured_at_ms) is int else None
        )
        image_sha256 = hashlib.sha256(transaction.image_bytes).hexdigest()
        image_url = "/api/decision/image?" + urlencode(
            {"generation": generation, "id": transaction.transaction_id}
        )
        cycle = transaction.stream_frame["cycle"]
        source = cycle.get("source") if isinstance(cycle, dict) else None
        authority = transaction.stream_frame.get("authority_summary")
        observation = source.get("observation") if isinstance(source, dict) else None
        # Serialized retained evidence from shared_memory["decision.snapshot"],
        # carried through the cycle publication.
        memory = source.get("memory") if isinstance(source, dict) else None
        evidence = _evidence_projection(
            transaction_id=transaction.transaction_id,
            frame_id=transaction.frame_record["frame_id"],
            observation=observation,
            memory=memory,
        )
        return {
            "schema": DECISION_VIEW_SCHEMA,
            "view_id": DECISION_VIEW_ID,
            "status": "current",
            "generation_id": generation,
            "identity": _json_copy(self.identity),
            "transaction_id": transaction.transaction_id,
            "served_at_ms": served_at_ms,
            "freshness": {
                "captured_at_ms": captured_at_ms,
                "capture_age_ms": capture_age_ms,
                "published_at_ms": published_at_ms,
                "age_ms": age_ms,
                "max_age_ms": DECISION_STREAM_MAX_AGE_MS,
                "expires_at_ms": published_at_ms + DECISION_STREAM_MAX_AGE_MS,
            },
            "decision": _json_copy(transaction.stream_frame),
            "current_image": {
                "status": "available",
                "frame_id": transaction.frame_record["frame_id"],
                "frame_index": transaction.frame_record["frame_index"],
                "captured_at_ms": transaction.frame_record.get("captured_at_ms"),
                "content_type": transaction.content_type,
                "byte_length": len(transaction.image_bytes),
                "sha256": image_sha256,
                "url": image_url,
            },
            "provenance": {
                "sensor_snapshot": _json_copy(transaction.frame_record.get("sensor_snapshot")),
                "observation": _json_copy(observation),
                "memory": _json_copy(memory),
            },
            "evidence": evidence,
            "authority": _json_copy(authority) if isinstance(authority, dict) else None,
            "host_telemetry": _json_copy(transaction.frame_record.get("host_telemetry"))
            if isinstance(transaction.frame_record.get("host_telemetry"), dict)
            else None,
        }

    def image_response(self, *, generation: str, transaction_id: str) -> tuple[bytes, str]:
        self.require_generation(generation)
        if not self._activation_matches():
            raise DecisionViewError(503, "activation_mismatch", "decision activation no longer matches")
        with self._lock:
            transaction = self._transactions.get(transaction_id)
        if transaction is None:
            raise DecisionViewError(404, "image_unavailable", "exact decision image is no longer retained")
        return transaction.image_bytes, transaction.content_type

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            self._latest_transaction_id = None
            self._transactions.clear()

    def _activation_matches(self) -> bool:
        if self._provider_identity is not None:
            return True
        if self._startup_activation is None:
            return False
        try:
            payload = json.loads(self._activation_path.read_text(encoding="utf-8"))
            return _activation_identity(payload) == _activation_identity(self._startup_activation)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False


def decision_error_payload(view: DecisionView, *, reason: str) -> dict[str, Any]:
    return {
        "schema": DECISION_VIEW_SCHEMA,
        "view_id": DECISION_VIEW_ID,
        "status": "unavailable",
        "reason": reason,
        "generation_id": view.generation_id,
        "identity": _json_copy(view.identity) if view.identity is not None else None,
    }


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def get_decision_view_status(
    *,
    automation_dir: Path,
    vehicle_id: str,
    activation: dict[str, Any],
    timeout_s: float = 0.25,
) -> dict[str, Any]:
    """Boundedly probe the already-running local producer without starting it."""

    record_path = Path(automation_dir) / VIEW_RECORD_NAME
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return _unavailable_status("runtime view record is unavailable")
    if not isinstance(record, dict):
        return _unavailable_status("runtime view record is invalid")
    url = record.get("url")
    run_id = record.get("run_id")
    worker_pid = record.get("worker_pid")
    parsed = urlparse(url) if isinstance(url, str) else None
    if (
        parsed is None
        or parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.port is None
        or type(run_id) is not str
        or type(worker_pid) is not int
    ):
        return _unavailable_status("runtime view record has no valid loopback producer")
    try:
        identity = decision_view_identity(
            vehicle_id=vehicle_id,
            run_id=run_id,
            worker_pid=worker_pid,
            activation=activation,
        )
        generation = generation_id(identity)
    except (TypeError, ValueError):
        return _unavailable_status("decision activation cannot identify a live view")
    health_url = urljoin(url, "api/health")
    api_url = f"{urljoin(url, 'api/decision/latest')}?{urlencode({'generation': generation})}"
    page_url = f"{urljoin(url, 'decision')}?{urlencode({'generation': generation})}"
    try:
        opener = build_opener(_NoRedirect())
        with opener.open(Request(health_url, method="GET"), timeout=max(0.05, timeout_s)) as response:
            body = response.read(MAX_PROBE_BYTES + 1)
        if len(body) > MAX_PROBE_BYTES:
            return _unavailable_status("decision view probe response is too large")
        payload = json.loads(body.decode("utf-8"))
    except (HTTPError, URLError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return _unavailable_status("decision view producer is unavailable")
    decision = payload.get("decision") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or not isinstance(decision, dict)
        or payload.get("run_id") != run_id
        or payload.get("worker_pid") != worker_pid
        or decision.get("generation_id") != generation
        or decision.get("identity") != identity
        or decision.get("status") not in {"warming", "running"}
    ):
        return _unavailable_status("decision view producer returned a mismatched response")
    return {
        "available": True,
        "status": "current" if decision.get("status") == "running" else "warming",
        "reason": None,
        "generation_id": generation,
        "identity": identity,
        "api_url": api_url,
        "url": page_url,
    }


def _unavailable_status(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "status": "unavailable",
        "reason": reason,
        "generation_id": None,
        "identity": None,
        "api_url": None,
        "url": None,
    }


def project_host_telemetry_panel(
    panel: object,
    *,
    decision_frame_id: str | None = None,
) -> dict[str, Any]:
    """Return a safe additive telemetry panel for the live decision view."""

    if not isinstance(panel, dict):
        return host_telemetry_failure(
            "schema_invalid",
            message="Host telemetry panel is not an object.",
        )
    if panel.get("schema") != HOST_TELEMETRY_PANEL_SCHEMA:
        return host_telemetry_failure(
            "schema_invalid",
            message="Host telemetry panel schema is invalid.",
        )
    if "authority" in panel or "host_application" in panel:
        return host_telemetry_failure(
            "field_invalid",
            message="Host telemetry panel cannot contain decision authority fields.",
        )
    if decision_frame_id is not None:
        decision = panel.get("decision")
        if isinstance(decision, dict) and decision.get("frame_id") != decision_frame_id:
            return host_telemetry_failure(
                "identity_mismatch",
                message="Host telemetry panel frame does not match the decision frame.",
            )
    try:
        copied = _json_copy(panel)
    except (TypeError, ValueError):
        return host_telemetry_failure(
            "field_invalid",
            message="Host telemetry panel is not strict JSON.",
        )
    return copied if isinstance(copied, dict) else host_telemetry_failure("schema_invalid")


def project_decision_with_host_telemetry(
    decision_payload: object,
    panel: object,
    *,
    decision_frame_id: str | None = None,
) -> dict[str, Any]:
    """Add telemetry as a sibling while preserving decision authority bytes."""

    if not isinstance(decision_payload, dict):
        raise ValueError("Decision view payload is not an object.")
    copied = _json_copy(decision_payload)
    if not isinstance(copied, dict):
        raise ValueError("Decision view payload is not an object.")
    frame_id = decision_frame_id
    if frame_id is None:
        frame_id = copied.get("frame_id")
        if frame_id is None and isinstance(copied.get("decision"), dict):
            frame_id = copied["decision"].get("frame_id")
    copied["host_telemetry"] = project_host_telemetry_panel(
        panel,
        decision_frame_id=frame_id if isinstance(frame_id, str) else None,
    )
    return copied


def build_decision_host_telemetry_capture(
    *,
    decision_payload: object,
    panel: object,
    records_result: dict[str, Any] | None = None,
    vehicle_id: str | None = None,
) -> dict[str, Any]:
    """Build a capture envelope with decision and telemetry as siblings."""

    if not isinstance(decision_payload, dict):
        raise ValueError("Decision capture payload is not an object.")
    safe_panel = project_host_telemetry_panel(panel)
    capture = build_host_telemetry_capture(
        joined_point=safe_panel,
        records_result=records_result,
        vehicle_id=vehicle_id,
    )
    return {
        "schema": "automa_physical_decision_capture_v0",
        "decision": _json_copy(decision_payload),
        "host_telemetry": capture,
    }


def render_host_telemetry_panel_html(panel: object) -> str:
    """Render the additive telemetry panel used by static/live consumers."""

    safe_panel = project_host_telemetry_panel(panel)
    serialized = html.escape(json.dumps(safe_panel, indent=2, sort_keys=True), quote=True)
    return (
        '<section id="host_telemetry" aria-label="Host telemetry separate observation">'
        "<h2>Host telemetry · separate observation</h2>"
        f"<pre>{serialized}</pre>"
        "</section>"
    )


def unavailable_host_telemetry_panel(
    reason: str = "publisher_missing",
    *,
    message: str | None = None,
) -> dict[str, Any]:
    return host_telemetry_failure(reason, message=message)
