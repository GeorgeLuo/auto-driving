"""d2-fixture: deterministic fixture; not Chase/Pi live evidence.

Run with an explicit disposable --runtime-root and --vehicle-id. This stages
the real shadow engine through the public CLI, but arranges synthetic capture,
observation, memory and automation state directly. It does not exercise the
automation scheduler, a device, host telemetry, or browser visual acceptance.
Source time is a finite synthetic sequence; only publication time is refreshed.
The typed host reports are explicitly synthetic consumer-contract examples.
"""

from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import re
import shlex
import signal
import sys
import threading
import time

# Permit the spec's direct script invocation as well as unittest imports.
REPOSITORY = Path(__file__).resolve().parents[3]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from PIL import Image, ImageDraw

from autonomy.decision.decision_data import ready_envelope
from autonomy.decision.memory import MemoryBounds, MemoryProvenance, MemorySnapshot, RetainedEvidence
from autonomy.decision.observation import Observation
from autonomy.perception import ViewLocation
from cli.automa_cli.decision import (
    ENGINE_ID,
    publish_shadow_decision_frame,
    write_latest_decision_frame,
)
from cli.automa_cli.decision_view import DecisionViewPublisher, probe_decision_view
from cli.automa_cli.perception_view import PerceptionViewServer
from implementations.decision.catalog import create_shadow_proposals_engine
from tests.support.cli_runner import run_automa


LABEL = "d2-fixture: deterministic fixture; not Chase/Pi live evidence"
SCENARIOS = (
    "current-left", "current-right", "retained", "stale", "inactive",
    "missing-image", "missing-geometry", "host-unavailable", "host-zero",
    "host-nonzero", "generation-mismatch", "stopped",
)
LEFT_BOX = (0.1, 0.25, 0.35, 0.75)
RIGHT_BOX = (0.65, 0.25, 0.9, 0.75)


def write_json(path: Path, value: object) -> None:
    """Atomic arrangement of files owned by this disposable fixture only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".d2-fixture-tmp")
    pending.write_text(json.dumps(value, sort_keys=True, indent=2), encoding="utf-8")
    pending.replace(path)


def synthetic_png(side: str) -> bytes:
    image = Image.new("RGB", (320, 180), (30, 40, 55))
    draw = ImageDraw.Draw(image)
    box = LEFT_BOX if side == "left" else RIGHT_BOX
    draw.rectangle(tuple(int(v * (320 if i % 2 == 0 else 180)) for i, v in enumerate(box)),
                   fill=(220, 100, 40) if side == "left" else (50, 180, 210))
    draw.text((8, 8), "d2-fixture / synthetic / " + side, fill="white")
    draw.text((8, 160), "NOT Chase/Pi live evidence", fill="white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class DecisionFixture:
    """Own one real loopback listener and a finite set of synthetic sources."""

    def __init__(self, runtime_root: Path, vehicle_id: str = "d2-fixture", *, port: int = 0):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", vehicle_id):
            raise ValueError("vehicle-id must be a single safe path component")
        self.runtime_root = Path(runtime_root).resolve()
        self.vehicle_id = vehicle_id
        self.vehicle_runtime = self.runtime_root / vehicle_id
        if self.vehicle_runtime.is_symlink() or (
            self.vehicle_runtime.exists()
            and (not self.vehicle_runtime.is_dir() or any(self.vehicle_runtime.iterdir()))
        ):
            raise ValueError("refusing a nonempty or symlinked preexisting vehicle runtime")
        self.vehicle_runtime.mkdir(parents=True, exist_ok=True)
        run_automa("vehicles", "update", "decision", "--id", vehicle_id,
                   "--engine", ENGINE_ID, "--json", runtime_root=self.runtime_root)
        runtime = self.vehicle_runtime / "bundle" / "runtime"
        self.activation_path = runtime / "decision" / "active.json"
        self.activation = json.loads(self.activation_path.read_text())
        self.automation_dir = runtime / "automation"
        self.state_path = self.automation_dir / "state.json"
        self.latest_path = self.automation_dir / "latest_decision.json"
        self.source_dir = self.automation_dir / "d2-fixture"
        self.run_id = "d2-fixture-run"
        self.state = {
            "schema": "automa_automation_run_state_v0", "vehicle_id": vehicle_id,
            "run_id": self.run_id, "pid": os.getpid(), "status": "running",
            "fixture": LABEL,
        }
        write_json(self.state_path, self.state)
        self.publisher = DecisionViewPublisher(
            vehicle_id=vehicle_id, automation_dir=self.automation_dir,
            activation=self.activation, run_id=self.run_id, worker_pid=os.getpid(),
        )
        self.server = PerceptionViewServer(
            vehicle_id=vehicle_id, automation_dir=self.automation_dir, port=port,
            run_id=self.run_id, worker_pid=os.getpid(), decision_publisher=self.publisher,
        ).start()
        self.engine = create_shadow_proposals_engine()
        self.images: dict[str, bytes] = {}
        self.captures: dict[str, dict] = {}
        self.observations: dict[str, Observation] = {}
        self.cycle = None
        self.frame: dict = {}

    @property
    def api_path(self) -> str:
        return "/api/decision/latest?generation=" + self.publisher.generation_id

    def capture(self, index: int, *, side: str = "left", timestamp: int = 1000,
                geometry: bool = True, ingress: bool = True) -> RetainedEvidence:
        frame_id = f"d2-fixture-frame-{index:03d}"
        path = self.source_dir / (frame_id + ".png")
        data = synthetic_png(side)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        snapshot = {"readings": {"front_camera": {
            "sensor_id": "d2-fixture-camera", "timestamp_ms": timestamp,
            "metadata": {"content_type": "image/png", "fixture": LABEL},
        }}}
        location = ViewLocation(
            frame="image", zone=side,
            bbox_xyxy_norm=(LEFT_BOX if side == "left" else RIGHT_BOX) if geometry else None,
        )
        record = RetainedEvidence(
            record_id=f"d2-fixture-record-{index:03d}", kind="floor_boundary",
            label=LABEL, confidence=0.8, location=location,
            provenance=MemoryProvenance(
                observation_id=f"d2-fixture-observation-{index:03d}",
                evidence_id=f"d2-fixture-thing-{index:03d}", coordinate_frame="image",
                observed_at_ms=timestamp, updated_at_ms=timestamp,
                source_plugin_id="d2-fixture-observer", frame_id=frame_id,
            ), properties={"fixture": LABEL},
        )
        observation = Observation(
            observation_id=record.provenance.observation_id, created_at_ms=timestamp,
            sensor_snapshot=snapshot, perception_plugin_id="d2-fixture-observer",
            perception_schema="perception_text_v2", summary=(LABEL,),
            things=({"thing_id": record.provenance.evidence_id,
                     "kind": record.kind, "label": LABEL, "confidence": 0.8,
                     "location": location.to_dict(), "properties": {"fixture": LABEL},
                     "source_plugin_id": "d2-fixture-observer"},),
            artifacts={"frame_image": str(path)}, metadata={"fixture": LABEL},
        )
        capture = {"frame_id": frame_id, "frame_index": index,
                   "captured_at_ms": timestamp, "sensor_snapshot": snapshot,
                   "fixture": LABEL}
        self.images[frame_id] = data
        self.captures[frame_id] = capture
        self.observations[frame_id] = observation
        write_json(self.source_dir / (frame_id + ".json"), {
            "fixture": LABEL, "capture": capture, "observation": observation.to_dict(),
            "memory_record": record.to_dict(),
        })
        if ingress:
            self.server.publish_frame(frame_path=path, frame_record=capture)
        return record

    def publish(self, index: int, records: tuple[RetainedEvidence, ...], *,
                timestamp: int = 1000, host: str = "unavailable") -> dict:
        frame_id = f"d2-fixture-frame-{index:03d}"
        memory = MemorySnapshot(
            memory_id="d2-fixture-memory", epoch_id="d2-fixture-epoch",
            health="healthy" if records else "empty", bounds=MemoryBounds(max_records=16),
            created_at_ms=timestamp, records=records, summary=(LABEL,),
            implementation_id="d2-fixture", metadata={"fixture": LABEL},
        )
        host_envelope = None
        if host != "unavailable":
            value = {
                "schema": "automa_host_observation_v1", "identity": self.publisher.identity,
                "frame_id": frame_id, "frame_index": index, "observed_at_ms": timestamp,
                "observer": "d2-fixture-synthetic-host-output", "mode": "user",
                "user_input": {"steering": 0.0, "throttle": 0.0},
                "pilot_output": {"steering": 0.0, "throttle": 0.0},
                "host_output": {"steering": 0.2 if host == "nonzero" else 0.0, "throttle": 0.0},
            }
            host_envelope = ready_envelope(value, updated_at_ms=timestamp)
        self.cycle, _ = self.engine.run_cycle(
            frame_id=frame_id, frame_index=index, timestamp_ms=timestamp,
            observation=self.observations[frame_id], memory=memory, host_application=host_envelope,
        )
        return self.refresh()

    def refresh(self) -> dict:
        if self.cycle is None:
            raise ValueError("publish a synthetic cycle before refreshing")
        if not publish_shadow_decision_frame(
            cycle_result=self.cycle, context_frame_id=self.cycle.frame_id,
            vehicle_id=self.vehicle_id, vehicle_runtime_dir=self.vehicle_runtime,
            run_id=self.run_id, worker_pid=os.getpid(), activation=self.activation,
            staged_engine_id=ENGINE_ID,
        ):
            raise RuntimeError("production shadow publisher refused fixture cycle")
        self.frame = json.loads(self.latest_path.read_text())
        if not self.server.publish_decision_frame(self.frame):
            raise RuntimeError("production D2 publisher refused fixture cycle")
        return self.frame

    def arrange(self, scenario: str) -> None:
        if scenario not in SCENARIOS:
            raise ValueError("unknown fixture scenario")
        record = self.capture(
            1, side="right" if scenario == "current-right" else "left",
            geometry=scenario != "missing-geometry", ingress=scenario != "missing-image",
        )
        host = "nonzero" if scenario == "host-nonzero" else "zero" if scenario == "host-zero" else "unavailable"
        self.publish(1, (record,), host=host)
        if scenario in {"retained", "stale", "inactive"}:
            stamp = 2501 if scenario == "stale" else 1500
            self.capture(2, side="right", timestamp=stamp)
            self.publish(2, () if scenario == "inactive" else (record,), timestamp=stamp)
        if scenario == "generation-mismatch":
            self.state["run_id"] = "d2-fixture-other-run"
            write_json(self.state_path, self.state)
        elif scenario == "stopped":
            # Keep the shell reachable to demonstrate refusal from a stopped producer.
            self.state["status"] = "stopped"
            write_json(self.state_path, self.state)
            self.publisher.stop()

    def info(self) -> dict:
        result = run_automa("vehicles", "info", "decision", "--id", self.vehicle_id,
                            "--json", runtime_root=self.runtime_root)
        return json.loads(result.stdout)

    def receipt(self, scenario: str) -> dict:
        info_command = shlex.join([
            "env", "AUTOMA_RUNTIME_ROOT=" + str(self.runtime_root), "PYTHONDONTWRITEBYTECODE=1",
            str(REPOSITORY / "cli" / "automa"), "vehicles", "info", "decision",
            "--id", self.vehicle_id, "--json",
        ])
        view = self.info()["combined_view"]
        return {"fixture": LABEL, "scenario": scenario, "runtime_root": str(self.runtime_root),
                "pid": os.getpid(), "info_command": info_command, "url": view["url"],
                "combined_view": view, "diagnostic_api_url": self.server.url.rstrip("/") + self.api_path,
                "source_records": str(self.source_dir),
                "limitation": "Synthetic state/cycles; publication time refreshed; no scheduler/device/browser acceptance."}

    def close(self) -> None:
        self.state["status"] = "stopped"
        write_json(self.state_path, self.state)
        self.server.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=LABEL)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--vehicle-id", required=True)
    parser.add_argument("--scenario", choices=SCENARIOS, default="retained")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--duration-s", type=float, default=300,
                        help="finite lifetime, 0 < seconds <= 3600 (default: 300)")
    args = parser.parse_args()
    if not 0 < args.duration_s <= 3600:
        parser.error("duration-s must be finite and in (0, 3600]")
    if not 0 <= args.port <= 65535:
        parser.error("port must be in 0..65535")
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda _signum, _frame: stop.set())
    fixture = None
    try:
        fixture = DecisionFixture(args.runtime_root, args.vehicle_id, port=args.port)
        fixture.arrange(args.scenario)
        print(json.dumps(fixture.receipt(args.scenario), sort_keys=True), flush=True)
        deadline = time.monotonic() + args.duration_s
        while not stop.wait(min(1.0, max(0.0, deadline - time.monotonic()))):
            if time.monotonic() >= deadline:
                break
            if args.scenario not in {"generation-mismatch", "stopped"}:
                fixture.refresh()
        return 0
    except (ValueError, RuntimeError, AssertionError, OSError) as exc:
        print(f"d2-fixture refused: {exc}", file=sys.stderr)
        return 2
    finally:
        if fixture is not None:
            fixture.close()


if __name__ == "__main__":
    raise SystemExit(main())
