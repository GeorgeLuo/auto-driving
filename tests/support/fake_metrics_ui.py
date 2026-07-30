from __future__ import annotations

import base64
import hashlib
import json
import socketserver
import struct
import threading
from contextlib import contextmanager
from typing import Iterator


_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@contextmanager
def fake_metrics_ui_server() -> Iterator[str]:
    """Serve the read-only Chase protocol used by deterministic CLI gates."""

    server = _ThreadingWebSocketServer(("127.0.0.1", 0), _MetricsUiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"ws://{host}:{port}/ws/control"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


class _ThreadingWebSocketServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _MetricsUiHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        headers = _read_http_headers(self.request)
        key = _header_value(headers, "Sec-WebSocket-Key")
        if key is None:
            return
        accept = base64.b64encode(
            hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
            ).digest()
        ).decode("ascii")
        self.request.sendall(
            (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n"
                "\r\n"
            ).encode("ascii")
        )
        while True:
            frame = _read_frame(self.request)
            if frame is None:
                return
            opcode, payload = frame
            if opcode == 0x8:
                return
            if opcode != 0x1:
                continue
            message = json.loads(payload.decode("utf-8"))
            response = _response_for(message)
            if response is not None:
                _send_text(self.request, json.dumps(response))


def _response_for(message: dict) -> dict | None:
    request_id = message.get("request_id")
    message_type = message.get("type")
    if message_type == "register":
        return {
            "type": "ack",
            "payload": "registered as agent",
        }
    if message_type == "get_state":
        return _message("state_update", request_id, _state())
    if message_type == "get_play_debug":
        return _message("play_debug", request_id, _debug())
    if message_type == "play_game_query":
        query_id = message.get("queryId")
        return _message(
            "play_game_query_result",
            request_id,
            {
                "queryId": query_id,
                "result": _atomic_capture(),
            },
        )
    return {
        "type": "error",
        "request_id": request_id,
        "error": f"unsupported fake command {message_type!r}",
    }


def _message(
    message_type: str,
    request_id: object,
    payload: dict,
) -> dict:
    return {
        "type": message_type,
        "request_id": request_id,
        "payload": payload,
    }


def _read_http_headers(connection: object) -> str:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = connection.recv(4096)
        if not chunk:
            break
        data += chunk
    return data.decode("iso-8859-1", errors="replace")


def _header_value(headers: str, name: str) -> str | None:
    prefix = f"{name.lower()}:"
    for line in headers.split("\r\n")[1:]:
        if line.lower().startswith(prefix):
            return line.split(":", 1)[1].strip()
    return None


def _read_exact(connection: object, count: int) -> bytes | None:
    data = b""
    while len(data) < count:
        chunk = connection.recv(count - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def _read_frame(connection: object) -> tuple[int, bytes] | None:
    header = _read_exact(connection, 2)
    if header is None:
        return None
    first, second = header
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        encoded = _read_exact(connection, 2)
        if encoded is None:
            return None
        length = struct.unpack("!H", encoded)[0]
    elif length == 127:
        encoded = _read_exact(connection, 8)
        if encoded is None:
            return None
        length = struct.unpack("!Q", encoded)[0]
    mask = _read_exact(connection, 4) if masked else b""
    if mask is None:
        return None
    payload = _read_exact(connection, length) if length else b""
    if payload is None:
        return None
    if masked:
        payload = bytes(
            byte ^ mask[index % 4] for index, byte in enumerate(payload)
        )
    return opcode, payload


def _send_text(connection: object, text: str) -> None:
    payload = text.encode("utf-8")
    header = bytearray([0x81])
    if len(payload) < 126:
        header.append(len(payload))
    elif len(payload) <= 0xFFFF:
        header.append(126)
        header.extend(struct.pack("!H", len(payload)))
    else:
        header.append(127)
        header.extend(struct.pack("!Q", len(payload)))
    connection.sendall(bytes(header) + payload)


def _state() -> dict:
    return {
        "gameId": "chase",
        "playback": {"isPlaying": True, "playbackRate": 1},
        "playSidebarSections": [
            {
                "rows": [
                    {"id": "scenario-select", "value": "fixture-scenario"},
                    {
                        "id": "chaser-control-source",
                        "value": "programmatic",
                    },
                ]
            }
        ],
    }


def _debug() -> dict:
    return {
        "gameId": "chase",
        "simulationEpoch": "fixture-epoch",
        "actions": {
            "chaserInput": {
                "source": "programmatic",
                "motion": "forward",
                "forward": True,
                "reverse": False,
                "steering": 0.25,
            }
        },
    }


def _atomic_capture() -> dict:
    return {
        "contractVersion": 1,
        "captureId": "fixture-capture-12",
        "actorId": "chaser",
        "frameIdentity": {
            "gameId": "chase",
            "simulationEpoch": "fixture-epoch",
            "frameIndex": 12,
        },
        "playback": {"advanced": False},
        "sensor": {
            "image": {
                "contentType": "image/png",
                "width": 1,
                "height": 1,
                "dataUrl": _PNG_DATA_URL,
            }
        },
        "evaluator": {
            "classification": "non-sensor",
        },
    }
