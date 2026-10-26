from __future__ import annotations

import base64
import hashlib
import itertools
import json
import socketserver
import struct
import threading
import time
from contextlib import contextmanager
from typing import Iterator


_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


_JPEG_DATA_URL = 'data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDzeiiiuw5D/9k='

@contextmanager
def fake_metrics_ui_server() -> Iterator[str]:
    """Serve the read-only Chase protocol used by deterministic CLI gates."""

    with fake_metrics_ui() as server:
        yield server.url


@contextmanager
def fake_metrics_ui(port: int = 0) -> Iterator[_ThreadingWebSocketServer]:
    """The same server, for tests that act on the agents attached to it."""

    server = _ThreadingWebSocketServer(("127.0.0.1", port), _MetricsUiHandler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


class _ThreadingWebSocketServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, *args: object) -> None:
        super().__init__(*args)
        self._agents_lock = threading.Lock()
        # Like Metrics UI, each registered agent's description, listed to the others.
        self._agents: dict[_MetricsUiHandler, dict] = {}
        self._refusals: dict[str, dict] = {}

    def refuse(self, message_type: str, error: str, code: str | None = None) -> None:
        """Answer every ``message_type`` request with this error, as Metrics UI does."""

        self._refusals[message_type] = {"error": error, "payload": {"code": code}}

    def refusal(self, message: dict) -> dict | None:
        refusal = self._refusals.get(message.get("type"))
        if refusal is None:
            return None
        return {"type": "error", "request_id": message.get("request_id"), **refusal}

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"ws://{host}:{port}/ws/control"

    def add_agent(self, handler: _MetricsUiHandler, client: object) -> None:
        connected_at = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        with self._agents_lock:
            self._agents[handler] = {
                "client": client if isinstance(client, dict) else None,
                "connectedAt": connected_at,
            }

    def remove_agent(self, handler: _MetricsUiHandler) -> None:
        with self._agents_lock:
            self._agents.pop(handler, None)

    def list_agents(self, requester: _MetricsUiHandler) -> list[dict]:
        with self._agents_lock:
            return [agent for handler, agent in self._agents.items() if handler is not requester]

    def close_agents(self, reason: str) -> None:
        """Close every agent socket the way a Metrics UI shutdown does."""

        with self._agents_lock:
            handlers = list(self._agents)
        for handler in handlers:
            with handler.send_lock:
                _send_close(handler.request, 1001, reason)


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
        self.send_lock = send_lock = threading.Lock()
        stopped = threading.Event()
        sender = None
        def send(message):
            with send_lock:
                _send_text(self.request, json.dumps(message))
        def produce():
            while not stopped.wait(1 / 30):
                try:
                    send({"type": "play_camera_stream_frame", "payload": _stream_frame(next(_FRAME_INDEXES))})
                except OSError:
                    stopped.set()
        try:
            while True:
                frame = _read_frame(self.request)
                if frame is None or frame[0] == 0x8:
                    return
                if frame[0] != 0x1:
                    continue
                message = json.loads(frame[1].decode("utf-8"))
                refusal = self.server.refusal(message)
                if refusal is not None:
                    send(refusal)
                elif message.get("type") == "play_camera_stream_subscribe":
                    first = _atomic_capture(next(_FRAME_INDEXES))
                    send(_message("play_camera_stream_result", message.get("request_id"), {
                        "event": "subscribed", "subscriptionId": "fixture-stream",
                        "cameraStream": {"supported": True}, "playback": {"advanced": False},
                        "preservation": first["passiveObservation"]["preservation"],
                        "frame": _stream_frame(first["frameIdentity"]["frameIndex"]),
                    }))
                    sender = threading.Thread(target=produce, daemon=True)
                    sender.start()
                elif message.get("type") == "play_camera_stream_unsubscribe":
                    stopped.set()
                elif message.get("type") == "list_agents":
                    send(_message("agent_list", message.get("request_id"),
                                  {"agents": self.server.list_agents(self)}))
                else:
                    if message.get("type") == "register":
                        self.server.add_agent(self, message.get("client"))
                    response = _response_for(message)
                    if response is not None:
                        send(response)
        except (ConnectionError, OSError):
            return
        finally:
            self.server.remove_agent(self)
            stopped.set()
            if sender is not None:
                sender.join(timeout=1)


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
    if message_type == "get_play_game_usage":
        return _message("play_game_usage", request_id, {"protocol": {"cameraStream": {"supported": True}}})
    if message_type == "get_play_debug":
        return _message("play_debug", request_id, _debug())
    if message_type == "play_game_query":
        query_id = message.get("queryId")
        return _message(
            "play_game_query_result",
            request_id,
            {
                "queryId": query_id,
                "result": _atomic_capture(next(_FRAME_INDEXES)),
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
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
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


def _send_close(connection: object, code: int, reason: str) -> None:
    payload = struct.pack("!H", code) + reason.encode("utf-8")
    connection.sendall(bytes([0x88, len(payload)]) + payload)


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


# The fixture plays: each capture sees the next simulator frame.
_FRAME_INDEXES = itertools.count(12)


def _atomic_capture(frame_index: int) -> dict:
    fingerprint = {
        "gameId": "chase",
        "scenarioId": "fixture-scenario",
        "simulationEpoch": "fixture-epoch",
        "playback": {
            "frameIndex": frame_index,
            "phase": "running",
            "pendingAction": False,
        },
        "controlSource": "programmatic",
        "controlInput": None,
        "actorId": "chaser",
        "cameraId": "front_camera",
    }
    return {
        "contractVersion": 1,
        "captureId": f"fixture-capture-{frame_index}",
        "actorId": "chaser",
        "frameIdentity": {
            "gameId": "chase",
            "simulationEpoch": "fixture-epoch",
            "frameIndex": frame_index,
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
        "passiveObservation": {
            "supported": True,
            "queryId": "atomic-evaluation-capture",
            "actorId": "chaser",
            "cameraId": "front_camera",
            "preservedFields": [
                "gameId",
                "scenarioId",
                "simulationEpoch",
                "playback",
                "controlSource",
                "controlInput",
                "actorId",
                "cameraId",
            ],
            "preservation": {
                "preserved": True,
                "before": fingerprint,
                "after": dict(fingerprint),
            },
        },
    }


def _stream_frame(index):
    capture = _atomic_capture(index)
    capture["sensor"]["image"] = {"contentType": "image/jpeg", "width": 1, "height": 1,
                                  "dataUrl": _JPEG_DATA_URL}
    now_us = time.monotonic_ns() // 1000
    return {"subscriptionId": "fixture-stream", "actorId": "chaser", "cameraId": "front_camera",
            "frameIdentity": capture["frameIdentity"], "sensor": capture["sensor"],
            "playback": {"advanced": False}, "sourceTimestampUs": now_us, "publishedAtUs": now_us}
