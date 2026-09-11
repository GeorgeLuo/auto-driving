"""Offline decision scenarios shared by the CLI and its standalone inspector."""

from __future__ import annotations

import hashlib
import json
import threading
import webbrowser
from dataclasses import asdict
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlparse

from autonomy.decision import ShadowProposalsConfig, canonical_json_utf8
from implementations.decision.catalog import create_shadow_proposals_engine

from .decision import (
    APPLY_SEQUENCE_SCHEMA,
    DECISION_APPLY_MAX_FRAMES,
    DECISION_APPLY_MAX_SEQUENCE_FILE_BYTES,
    ENGINE_ID,
    RUNTIME_ROOT,
    CommandResult,
    DecisionSurfaceError,
    _error_result,
    _normalize_apply_frames,
    _read_surface_activation,
    controller_bundle_paths,
    safe_path_part,
    strict_decode_apply_memory,
    validate_shadow_engine_config,
)
from .loopback_http import (
    LoopbackHTTPRequestHandler,
    LoopbackHTTPServer,
    start_server_thread,
    stop_server_thread,
)


def inspect_decision_sequence(
    from_run: str | Path, *, frame_index: int = 0, vehicle_id: str | None = None,
) -> dict[str, Any]:
    """Load one saved frame and run the real shadow engine for both sides.

    frame_index is a zero-based position in the sequence. Source timestamps stay
    fixed, so an old capture can be inspected without a worker or a wall clock.
    """

    path = Path(from_run).expanduser()
    if path.is_dir():
        path /= "sequence.json"
    with path.open("rb") as source:
        raw = source.read(DECISION_APPLY_MAX_SEQUENCE_FILE_BYTES + 1)
    if len(raw) > DECISION_APPLY_MAX_SEQUENCE_FILE_BYTES:
        raise ValueError("Decision sequence exceeds the supported size limit.")
    sequence = json.loads(raw)
    if not isinstance(sequence, dict) or sequence.get("schema") != APPLY_SEQUENCE_SCHEMA:
        raise ValueError(f"Expected a {APPLY_SEQUENCE_SCHEMA} sequence.")
    frames = sequence.get("frames")
    if not isinstance(frames, list) or not 1 <= len(frames) <= DECISION_APPLY_MAX_FRAMES:
        raise ValueError(f"Sequence must contain 1..{DECISION_APPLY_MAX_FRAMES} frames.")
    if not 0 <= frame_index < len(frames):
        raise ValueError(f"--frame must be between 0 and {len(frames) - 1}.")
    if vehicle_id and "vehicle_id" in sequence and sequence["vehicle_id"] != vehicle_id:
        raise ValueError("Sequence vehicle_id does not match --id.")
    frame = _normalize_apply_frames([frames[frame_index]], vehicle_id=vehicle_id or "offline")[0]
    config = ShadowProposalsConfig()
    if vehicle_id:
        bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))
        activation = _read_surface_activation(
            Path(bundle["decision_runtime_dir"]) / "active.json", vehicle_id=vehicle_id,
        )
        decision = activation["decision"]
        if decision.get("engine_id") != ENGINE_ID:
            raise ValueError(f"Inspector requires the {ENGINE_ID} engine.")
        config = validate_shadow_engine_config(decision["engine_config"])
    if frame["memory"] is None:
        raise ValueError("Selected frame has no retained memory to reposition. Choose a frame with image evidence.")

    scenarios = {}
    for side in ("left", "right"):
        memory = frame["memory"].to_dict()
        changed = []
        for record in memory["records"]:
            location = record.get("location")
            if location and location.get("frame") == "image" and record["kind"] in config.accepted_kinds:
                location.update(zone=side, bbox_xyxy_norm=None, polygon_xy_norm=None)
                changed.append(record["record_id"])
        if not changed:
            raise ValueError("Selected frame has no supported retained image evidence to reposition.")
        cycle, _ = create_shadow_proposals_engine(config).run_cycle(
            frame_id=frame["frame_id"], frame_index=frame["frame_index"],
            timestamp_ms=frame["timestamp_ms"], observation=frame["observation"],
            observation_error=frame["observation_error"],
            memory=strict_decode_apply_memory(memory), host_application=None,
        )
        command = cycle.authority.proposed
        steering = command.steering if command is not None else 0
        scenarios[side] = {
            "obstruction_side": side,
            "steering_direction": "steer left" if steering < 0 else "steer right" if steering > 0 else "hold",
            "changed_record_ids": changed,
            "cycle": cycle.to_dict(),
        }
    return {
        "schema": "automa_decision_inspection_v1",
        "engine_id": ENGINE_ID,
        "engine_config": asdict(config),
        "config_source": "staged" if vehicle_id else "packaged defaults",
        "input": {
            "name": path.name, "sha256": hashlib.sha256(raw).hexdigest(),
            "frame_position": frame_index, "frame_count": len(frames),
            "frame_id": frame["frame_id"], "timestamp_ms": frame["timestamp_ms"],
        },
        "scenarios": scenarios,
    }


class DecisionInspectorServer:
    """Serve a fixed pair of artifacts; the browser only selects what to display."""

    def __init__(self, inspection: dict[str, Any], *, port: int = 0):
        self._payload = canonical_json_utf8(inspection)
        self._httpd = LoopbackHTTPServer.bind_with_ephemeral_fallback(
            host="127.0.0.1", preferred_port=port, handler=_InspectorHandler,
        )
        self._httpd.inspector = self
        self._thread = None

    @property
    def url(self) -> str:
        return self._httpd.loopback_url

    def start(self) -> "DecisionInspectorServer":
        self._thread = start_server_thread(self._httpd, name="automa-decision-inspector")
        return self

    def stop(self) -> None:
        stop_server_thread(self._httpd, self._thread)


class _InspectorHandler(LoopbackHTTPRequestHandler):
    def do_GET(self) -> None:
        self._get(include_body=True)

    def do_HEAD(self) -> None:
        self._get(include_body=False)

    def _get(self, *, include_body: bool) -> None:
        route = urlparse(self.path).path
        if route in {"/", "/index.html"}:
            self._send(200, Path(__file__).with_name("decision_view.html").read_bytes(),
                       "text/html; charset=utf-8", include_body=include_body)
        elif route == "/api/inspection":
            self._send(200, self.server.inspector._payload,
                       "application/json; charset=utf-8", include_body=include_body)
        elif route == "/favicon.ico":
            self._send(204, b"", "image/x-icon", include_body=False)
        else:
            self._send_json(404, {"error": "not found"}, include_body=include_body)


def run_decision_inspector(
    from_run: str | Path, *, frame_index: int = 0, vehicle_id: str | None = None,
    port: int = 0, open_browser: bool = False, json_output: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    """Print the inspection artifacts or serve their page until Ctrl-C."""

    server = None
    try:
        if json_output and open_browser:
            raise ValueError("--json cannot be combined with --open.")
        if not 0 <= port <= 65535:
            raise ValueError("--port must be between 0 and 65535.")
        inspection = inspect_decision_sequence(from_run, frame_index=frame_index, vehicle_id=vehicle_id)
        if json_output:
            return CommandResult(0, json.dumps(inspection, indent=2, sort_keys=True))
        server = DecisionInspectorServer(inspection, port=port).start()
        if output:
            print(f"Decision inspector: {server.url}\nInput: {Path(from_run)} (frame {frame_index})\n"
                  "Offline shadow scenarios. Ctrl-C stops the inspector.", file=output, flush=True)
        if open_browser and not webbrowser.open(server.url, new=2) and output:
            print(f"Open the inspector manually: {server.url}", file=output, flush=True)
        threading.Event().wait()
    except KeyboardInterrupt:
        return CommandResult(0, "Decision inspector stopped.")
    except DecisionSurfaceError as exc:
        return _error_result(exc, json_output=json_output)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return _error_result(DecisionSurfaceError("inspection_invalid", str(exc)), json_output=json_output)
    finally:
        if server:
            server.stop()
