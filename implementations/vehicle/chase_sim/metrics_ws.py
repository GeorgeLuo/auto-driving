from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import socket
import ssl
import struct
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


PASSIVE_SESSION_FIELDS = (
    "game_id",
    "scenario_id",
    "simulation_epoch",
    "playback",
    "control_source",
    "control_input",
)


class MetricsUiWebSocketError(RuntimeError):
    """Raised when the Metrics UI WebSocket control channel fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        self.code = code
        self.details = details or {}
        super().__init__(message)


def _read_exact(sock: socket.socket, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise MetricsUiWebSocketError("WebSocket connection closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


@dataclass(frozen=True)
class MetricsUiCommandResponse:
    message: dict[str, Any]
    ack: dict[str, Any] | None = None

    @property
    def payload(self) -> Any:
        return self.message.get("payload")


class _WebSocketConnection:
    """Small RFC6455 client for local Metrics UI control messages."""

    def __init__(self, url: str, *, timeout_s: float = 5.0):
        self.url = url
        self.timeout_s = float(timeout_s)
        self.sock: socket.socket | None = None

    def __enter__(self) -> _WebSocketConnection:
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def connect(self) -> None:
        parsed = urlparse(self.url)
        if parsed.scheme not in {"ws", "wss"}:
            raise MetricsUiWebSocketError(
                f"Only ws:// or wss:// URLs are supported, got {self.url!r}"
            )
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        raw_sock = socket.create_connection((host, port), timeout=self.timeout_s)
        if parsed.scheme == "wss":
            try:
                sock = ssl.create_default_context().wrap_socket(
                    raw_sock,
                    server_hostname=host,
                )
            except Exception:
                raw_sock.close()
                raise
        else:
            sock = raw_sock
        sock.settimeout(self.timeout_s)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        host_header = f"{host}:{port}" if parsed.port else host
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("ascii"))
        header_bytes = b""
        while b"\r\n\r\n" not in header_bytes:
            chunk = sock.recv(4096)
            if not chunk:
                break
            header_bytes += chunk
            if len(header_bytes) > 65536:
                raise MetricsUiWebSocketError("WebSocket handshake header too large")

        headers = header_bytes.decode("iso-8859-1", errors="replace")
        status_line = headers.split("\r\n", 1)[0]
        if " 101 " not in status_line:
            raise MetricsUiWebSocketError(f"WebSocket handshake failed: {status_line}")

        expected_accept = base64.b64encode(
            hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii"),
            ).digest(),
        ).decode("ascii")
        if expected_accept not in headers:
            raise MetricsUiWebSocketError("WebSocket handshake did not return expected accept key")
        self.sock = sock

    def close(self) -> None:
        sock = self.sock
        if not sock:
            return
        try:
            self._send_frame(b"", opcode=0x8)
        except OSError:
            pass
        finally:
            self.sock = None
        try:
            sock.close()
        except OSError:
            pass

    def send_json(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
        self._send_frame(payload, opcode=0x1)

    def recv_json(self, *, timeout_s: float | None = None) -> dict[str, Any]:
        sock = self._require_sock()
        if timeout_s is not None:
            sock.settimeout(timeout_s)
        while True:
            try:
                opcode, payload = self._recv_frame()
            except TimeoutError as exc:
                raise MetricsUiWebSocketError("Timed out waiting for WebSocket message") from exc
            if opcode == 0x1:
                decoded = payload.decode("utf-8")
                data = json.loads(decoded)
                if not isinstance(data, dict):
                    raise MetricsUiWebSocketError("Expected JSON object from WebSocket")
                return data
            if opcode == 0x8:
                raise MetricsUiWebSocketError("WebSocket closed by server")
            if opcode == 0x9:
                self._send_frame(payload, opcode=0xA)
            if opcode == 0xA:
                continue

    def _require_sock(self) -> socket.socket:
        if not self.sock:
            raise MetricsUiWebSocketError("WebSocket is not connected")
        return self.sock

    def _send_frame(self, payload: bytes, *, opcode: int) -> None:
        sock = self._require_sock()
        header = bytearray([0x80 | (opcode & 0x0F)])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length <= 0xFFFF:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))

        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        sock.sendall(bytes(header) + mask + masked)

    def _recv_frame(self) -> tuple[int, bytes]:
        sock = self._require_sock()
        first, second = _read_exact(sock, 2)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", _read_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", _read_exact(sock, 8))[0]

        mask = _read_exact(sock, 4) if masked else b""
        payload = _read_exact(sock, length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return opcode, payload


class MetricsUiWsClient:
    """Agent-role client for the Metrics UI `/ws/control` endpoint."""

    def __init__(self, url: str = "ws://localhost:5050/ws/control", *, timeout_s: float = 5.0):
        self.url = url
        self.timeout_s = float(timeout_s)
        self._counter = 0

    def command(
        self,
        message: dict[str, Any],
        *,
        response_type: str | None = None,
        timeout_s: float | None = None,
        wait_for_frontend_ack: bool = False,
    ) -> MetricsUiCommandResponse:
        request_id = str(message.get("request_id") or self._next_request_id(message.get("type")))
        command = {**message, "request_id": request_id}
        operation_timeout = self.timeout_s if timeout_s is None else float(timeout_s)
        if not math.isfinite(operation_timeout) or operation_timeout <= 0:
            raise MetricsUiWebSocketError(
                "Metrics UI command timeout must be finite and greater than zero"
            )
        deadline = time.monotonic() + operation_timeout
        ack: dict[str, Any] | None = None

        connect_timeout = min(self.timeout_s, deadline - time.monotonic())
        if connect_timeout <= 0:
            raise MetricsUiWebSocketError(
                f"Timed out before connecting for {command['type']}"
            )
        with _WebSocketConnection(self.url, timeout_s=connect_timeout) as ws:
            ws.send_json({"type": "register", "role": "agent"})
            self._wait_for_registration(ws, deadline)
            ws.send_json(command)

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MetricsUiWebSocketError(
                        f"Timed out waiting for {response_type or 'ack'} for {command['type']}",
                    )
                response = ws.recv_json(timeout_s=remaining)
                if response.get("request_id") not in (request_id, None):
                    continue
                if response.get("type") == "error":
                    payload = (
                        response.get("payload")
                        if isinstance(response.get("payload"), dict)
                        else {}
                    )
                    raise MetricsUiWebSocketError(
                        str(response.get("error") or "Metrics UI error"),
                        code=(
                            str(payload.get("code"))
                            if payload.get("code")
                            else None
                        ),
                        details=payload,
                    )
                if response.get("type") == "ack":
                    ack = response
                    if wait_for_frontend_ack:
                        payload = response.get("payload")
                        if isinstance(payload, dict) and payload.get("command") == command["type"]:
                            return MetricsUiCommandResponse(message=response, ack=ack)
                        continue
                    if response_type is None:
                        return MetricsUiCommandResponse(message=response, ack=ack)
                    continue
                if response_type is None or response.get("type") == response_type:
                    return MetricsUiCommandResponse(message=response, ack=ack)

    def get_state(self, *, timeout_s: float | None = None) -> dict[str, Any]:
        response = self.command({"type": "get_state"}, response_type="state_update", timeout_s=timeout_s)
        payload = response.payload
        return payload if isinstance(payload, dict) else {}

    def play(self) -> dict[str, Any]:
        return self.command({"type": "play"}, wait_for_frontend_ack=True).message

    def set_play_app(self) -> dict[str, Any]:
        return self.command(
            {"type": "set_sidebar_app", "app": "play"},
            wait_for_frontend_ack=True,
        ).message

    def play_game_command(self, command_id: str, payload: Any = None) -> dict[str, Any]:
        return self.command(
            {
                "type": "play_game_command",
                "commandId": command_id,
                "payload": payload,
            },
        ).message

    def play_game_query(
        self,
        query_id: str,
        payload: Any = None,
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        response = self.command(
            {
                "type": "play_game_query",
                "queryId": query_id,
                "payload": payload,
            },
            response_type="play_game_query_result",
            timeout_s=timeout_s,
        )
        envelope = response.payload
        if not isinstance(envelope, dict):
            raise MetricsUiWebSocketError("Play game query returned no result envelope")
        if envelope.get("queryId") != query_id:
            raise MetricsUiWebSocketError(
                f"Play game query response id {envelope.get('queryId')!r} "
                f"does not match {query_id!r}"
            )
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise MetricsUiWebSocketError(
                f"Play game query {query_id!r} returned a non-object result"
            )
        return result

    def get_play_debug(self, *, timeout_s: float | None = None) -> dict[str, Any]:
        response = self.command(
            {"type": "get_play_debug"},
            response_type="play_debug",
            timeout_s=timeout_s,
        )
        payload = response.payload
        return payload if isinstance(payload, dict) else {}

    def get_play_front_view_snapshot(
        self,
        *,
        actor_id: str = "chaser",
        width: int = 640,
        height: int = 480,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        response = self.command(
            {
                "type": "get_play_front_view_snapshot",
                "actorId": actor_id,
                "width": int(width),
                "height": int(height),
            },
            response_type="play_front_view_snapshot",
            timeout_s=timeout_s,
        )
        payload = response.payload
        return payload if isinstance(payload, dict) else {}

    def _next_request_id(self, command_type: object) -> str:
        self._counter += 1
        prefix = str(command_type or "command").replace("_", "-")
        return f"auto-driving-{prefix}-{self._counter}-{uuid.uuid4().hex[:8]}"

    def _wait_for_registration(self, ws: _WebSocketConnection, deadline: float) -> None:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MetricsUiWebSocketError("Timed out registering with Metrics UI")
            response = ws.recv_json(timeout_s=remaining)
            if response.get("type") == "ack" and response.get("payload") == "registered as agent":
                return
            if response.get("type") == "error":
                payload = (
                    response.get("payload")
                    if isinstance(response.get("payload"), dict)
                    else {}
                )
                raise MetricsUiWebSocketError(
                    str(response.get("error") or "Registration failed"),
                    code=(
                        str(payload.get("code"))
                        if payload.get("code")
                        else None
                    ),
                    details=payload,
                )


def build_chase_session_fingerprint(
    *,
    state: dict[str, Any],
    debug: dict[str, Any],
) -> dict[str, Any]:
    """Build the documented read-only Chase session fields used for preservation."""

    sidebar = _play_sidebar_values(state)
    playback_source = state.get("playback")
    playback_source = playback_source if isinstance(playback_source, dict) else {}
    playback = {
        key: playback_source.get(key)
        for key in ("isPlaying", "playbackRate", "rate")
        if key in playback_source
    }
    control_input_source = _first_dict(
        _nested_get(debug, ("actions", "chaserInput")),
        _nested_get(debug, ("actors", "chaser", "input")),
    )
    control_input = _bounded_input(control_input_source)
    control_source = _first_nonempty(
        sidebar.get("chaser-control-source"),
        _nested_get(debug, ("actions", "chaserInput", "source")),
        _nested_get(debug, ("actions", "chaserAction", "source")),
        _nested_get(debug, ("actors", "chaser", "action", "source")),
    )
    fingerprint = {
        "game_id": _first_nonempty(debug.get("gameId"), state.get("gameId")),
        "scenario_id": _first_nonempty(
            sidebar.get("scenario-select"),
            debug.get("scenarioId"),
        ),
        "simulation_epoch": _first_nonempty(
            debug.get("simulationEpoch"),
            _nested_get(debug, ("frameIdentity", "simulationEpoch")),
        ),
        "playback": playback or None,
        "control_source": control_source,
        "control_input": control_input,
    }
    unknown_fields = [
        field_name
        for field_name in PASSIVE_SESSION_FIELDS
        if fingerprint.get(field_name) is None
    ]
    return {
        "schema": "chase_session_fingerprint_v1",
        **fingerprint,
        "unknown_fields": unknown_fields,
    }


def compare_chase_session_fingerprints(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    unknown = sorted(
        {
            *(
                str(item)
                for item in before.get("unknown_fields", [])
                if isinstance(item, str)
            ),
            *(
                str(item)
                for item in after.get("unknown_fields", [])
                if isinstance(item, str)
            ),
        }
    )
    changed = [
        field_name
        for field_name in PASSIVE_SESSION_FIELDS
        if before.get(field_name) != after.get(field_name)
    ]
    return {
        "preserved": not unknown and not changed,
        "unknown_fields": unknown,
        "changed_fields": changed,
        "before": before,
        "after": after,
    }


def _play_sidebar_values(state: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    sections = state.get("playSidebarSections")
    if not isinstance(sections, list):
        return values
    for section in sections:
        if not isinstance(section, dict):
            continue
        rows = section.get("rows")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_id = row.get("id")
            if isinstance(row_id, str) and "value" in row:
                values[row_id] = row.get("value")
    return values


def _nested_get(record: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = record
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _first_nonempty(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_dict(*values: Any) -> dict[str, Any] | None:
    return next((value for value in values if isinstance(value, dict)), None)


def _bounded_input(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    source = _first_nonempty(value.get("source"))
    steering = value.get("steering")
    if (
        source is None
        or isinstance(steering, bool)
        or not isinstance(steering, (int, float))
        or not math.isfinite(float(steering))
    ):
        return None
    motion = _first_nonempty(value.get("motion"))
    forward = value.get("forward")
    reverse = value.get("reverse")
    if motion is None and (
        not isinstance(forward, bool) or not isinstance(reverse, bool)
    ):
        return None
    result: dict[str, Any] = {
        "source": source,
        "steering": float(steering),
    }
    if motion is not None:
        result["motion"] = motion
    if isinstance(forward, bool) and isinstance(reverse, bool):
        result["forward"] = forward
        result["reverse"] = reverse
    return result
