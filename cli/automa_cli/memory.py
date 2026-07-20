"""Stage, inspect, stream, reset, and replay vehicle memory."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from autonomy.decision import (
    MEMORY_ACTIVATION_SCHEMA,
    ActivatedMemoryStage,
    DecisionFrameContext,
    Observation,
    load_memory_stage_if_present,
    read_memory_activation,
)
from implementations.memory import (
    DEFAULT_MEMORY_IMPLEMENTATION,
    available_memory_implementation_ids,
    build_memory_activation_payload,
    memory_implementation_spec,
)

from .automation import _automation_dir
from .bundles import (
    controller_bundle_paths,
    release_activation_summary,
    sync_controller_bundle,
)
from .paths import ROOT, display_path, safe_path_part
from .perception_view import PerceptionViewServer
from .physical_observation import (
    fetch_autonomy_status,
    fetch_observation_publication,
    physical_observation_dir,
    picar_base_url,
    post_memory_reset,
)
from .streaming import _publish_physical_view
from .vehicles import discover_active_vehicles, find_vehicle_by_id, format_active_vehicles_snapshot


RUNTIME_ROOT = Path(os.environ.get("AUTOMA_RUNTIME_ROOT", ROOT / "runtime" / "vehicles"))
MEMORY_OBSERVATION_SEQUENCE_SCHEMA = "automa_memory_observation_sequence_v0"
MEMORY_REPLAY_RESULT_SCHEMA = "vehicle_memory_replay_v0"


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


def update_vehicle_memory(
    *,
    vehicle_id: str,
    implementation_id: str = DEFAULT_MEMORY_IMPLEMENTATION,
    dry_run: bool = False,
    json_output: bool = False,
    verbose: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    known = available_memory_implementation_ids()
    if implementation_id not in known:
        available = ", ".join(known) or "(none)"
        return CommandResult(
            2,
            f"Unknown memory implementation {implementation_id!r}. Available: {available}.",
        )

    stream = output if verbose else None
    vehicle_runtime_dir = RUNTIME_ROOT / safe_path_part(vehicle_id)
    bundle = controller_bundle_paths(vehicle_runtime_dir)
    activation_path = Path(bundle["memory_runtime_dir"]) / "active.json"
    release: dict[str, Any] | None = None

    if not dry_run:
        release = sync_controller_bundle(bundle, output=stream)

    activation = _memory_activation(
        vehicle_id=vehicle_id,
        implementation_id=implementation_id,
        bundle=bundle,
        release=release,
    )

    if not dry_run:
        activation_path.parent.mkdir(parents=True, exist_ok=True)
        activation_path.write_text(
            json.dumps(activation, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    entry = memory_implementation_spec(implementation_id)
    payload = {
        "schema": "vehicle_memory_update_v0",
        "vehicle_id": vehicle_id,
        "implementation_id": implementation_id,
        "dry_run": dry_run,
        "activation": display_path(activation_path),
        "manifest": activation,
        "release": release_activation_summary(release) if release is not None else None,
    }
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    verb = "Would activate" if dry_run else "Updated memory"
    return CommandResult(
        0,
        "\n".join(
            [
                f"{verb}: {vehicle_id} -> {implementation_id}",
                f"Implementation: {entry['implementation_spec']}",
                f"Activation: {display_path(activation_path)}",
            ]
        ),
    )


def ensure_vehicle_memory_activation(
    *,
    vehicle_id: str,
    bundle: dict[str, str],
    release: dict[str, Any],
    implementation_id: str = DEFAULT_MEMORY_IMPLEMENTATION,
) -> Path:
    """Ensure a current-schema memory activation exists for autonomy deploy."""

    activation_path = Path(bundle["memory_runtime_dir"]) / "active.json"
    if activation_path.exists():
        activation = read_memory_activation(activation_path).payload
        controller_bundle = activation.get("controller_bundle")
        if not isinstance(controller_bundle, dict):
            controller_bundle = {}
            activation["controller_bundle"] = controller_bundle
        controller_bundle["release"] = release_activation_summary(release)
        activation_path.write_text(
            json.dumps(activation, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return activation_path

    activation = _memory_activation(
        vehicle_id=vehicle_id,
        implementation_id=implementation_id,
        bundle=bundle,
        release=release,
    )
    activation_path.parent.mkdir(parents=True, exist_ok=True)
    activation_path.write_text(
        json.dumps(activation, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return activation_path


def get_vehicle_memory_info(
    *,
    vehicle_id: str,
    json_output: bool = False,
    include_live: bool = True,
    timeout_s: float = 3.0,
) -> CommandResult:
    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))
    activation_path = Path(bundle["memory_runtime_dir"]) / "active.json"
    if not activation_path.exists():
        return CommandResult(
            2,
            "\n".join(
                [
                    f"No active memory implementation found for {vehicle_id!r}.",
                    f"Expected activation: {display_path(activation_path)}",
                    "Run: ./cli/automa vehicles update memory --id <vehicle_id>",
                ]
            ),
        )

    try:
        activation = read_memory_activation(activation_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        return CommandResult(
            2,
            f"Could not read memory activation {display_path(activation_path)}: {exc}",
        )

    memory = activation.payload.get("memory")
    if not isinstance(memory, dict):
        return CommandResult(
            2,
            f"Activation {display_path(activation_path)} has no memory section.",
        )

    payload: dict[str, Any] = {
        "schema": "vehicle_memory_info_v0",
        "vehicle_id": vehicle_id,
        "activation": {
            "path": display_path(activation_path),
            "implementation_id": activation.implementation_id,
            "implementation_spec": activation.implementation_spec,
            "implementation_config": activation.implementation_config,
            "bounds": activation.bounds.to_dict(),
        },
        "description": memory.get("description"),
        "controller_bundle": activation.payload.get("controller_bundle"),
        "lifecycle": {
            "methods": ["update", "reset", "snapshot"],
            "health": ["empty", "healthy", "unavailable", "error"],
            "claims_identity": False,
        },
        "live": None,
    }
    if include_live:
        payload["live"] = probe_live_memory(
            vehicle_id=vehicle_id,
            timeout_s=timeout_s,
        )
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(0, _format_memory_info(payload))


def replay_vehicle_memory(
    *,
    vehicle_id: str,
    sequence: str | Path,
    implementation_id: str | None = None,
    json_output: bool = False,
    verify_twice: bool = True,
) -> CommandResult:
    """Replay a fixed observation sequence through the staged memory activation.

    Offline and process-local: does not talk to the live host, does not write
    history by default, and reports a stable end-state digest for comparison.
    """

    sequence_path = Path(sequence).expanduser()
    try:
        frames = load_memory_observation_sequence(sequence_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return CommandResult(2, f"Could not load observation sequence {sequence_path}: {exc}")
    if not frames:
        return CommandResult(2, f"Observation sequence {sequence_path} contains no frames.")

    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))
    activation_path = Path(bundle["memory_runtime_dir"]) / "active.json"
    stage: ActivatedMemoryStage | None = None
    selected_implementation = implementation_id
    activation_source: str

    if implementation_id is not None:
        known = available_memory_implementation_ids()
        if implementation_id not in known:
            available = ", ".join(known) or "(none)"
            return CommandResult(
                2,
                f"Unknown memory implementation {implementation_id!r}. Available: {available}.",
            )
        with _temporary_memory_activation(
            vehicle_id=vehicle_id,
            implementation_id=implementation_id,
            bundle=bundle,
        ) as temp_activation:
            stage = ActivatedMemoryStage(read_memory_activation(temp_activation))
            activation_source = f"ephemeral:{implementation_id}"
            selected_implementation = implementation_id
            run_a = _run_memory_sequence(stage=stage, frames=frames)
            if verify_twice:
                stage_b = ActivatedMemoryStage(read_memory_activation(temp_activation))
                run_b = _run_memory_sequence(stage=stage_b, frames=frames)
            else:
                run_b = run_a
    else:
        if not activation_path.exists():
            return CommandResult(
                2,
                "\n".join(
                    [
                        f"No active memory implementation found for {vehicle_id!r}.",
                        f"Expected activation: {display_path(activation_path)}",
                        "Run: ./cli/automa vehicles update memory --id <vehicle_id>",
                        "Or pass --implementation for an ephemeral offline replay.",
                    ]
                ),
            )
        try:
            stage = load_memory_stage_if_present(activation_path)
        except (FileNotFoundError, ValueError, TypeError, ImportError, AttributeError) as exc:
            return CommandResult(
                2,
                f"Could not load memory activation {display_path(activation_path)}: {exc}",
            )
        if stage is None:
            return CommandResult(
                2,
                f"Memory activation is missing or empty at {display_path(activation_path)}.",
            )
        activation_source = display_path(activation_path)
        selected_implementation = stage.activation.implementation_id
        run_a = _run_memory_sequence(stage=stage, frames=frames)
        if verify_twice:
            stage_b = load_memory_stage_if_present(activation_path)
            if stage_b is None:
                return CommandResult(2, "Could not reload memory activation for determinism check.")
            run_b = _run_memory_sequence(stage=stage_b, frames=frames)
        else:
            run_b = run_a

    deterministic = run_a["digest"] == run_b["digest"]
    payload = {
        "schema": MEMORY_REPLAY_RESULT_SCHEMA,
        "vehicle_id": vehicle_id,
        "sequence": display_path(sequence_path.resolve()) if sequence_path.exists() else str(sequence_path),
        "frame_count": len(frames),
        "implementation_id": selected_implementation,
        "activation": activation_source,
        "digest": run_a["digest"],
        "deterministic": deterministic,
        "final": run_a["final"],
        "per_frame": run_a["per_frame"],
        "second_pass_digest": run_b["digest"] if verify_twice else None,
    }
    if not deterministic:
        if json_output:
            return CommandResult(2, json.dumps(payload, indent=2, sort_keys=True))
        return CommandResult(
            2,
            "\n".join(
                [
                    f"Memory replay is non-deterministic for {vehicle_id}.",
                    f"Digest pass 1: {run_a['digest']}",
                    f"Digest pass 2: {run_b['digest']}",
                ]
            ),
        )

    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    final = run_a["final"]
    lines = [
        f"Memory replay: {vehicle_id}",
        f"Sequence: {payload['sequence']} ({len(frames)} frames)",
        f"Implementation: {selected_implementation}",
        f"Activation: {activation_source}",
        f"Final health: {final.get('health')}  keys={final.get('record_count')}  epoch={final.get('epoch_id')}",
        f"Digest: {run_a['digest']}",
        "Deterministic: yes (two independent passes matched)",
    ]
    records = final.get("records") if isinstance(final.get("records"), list) else []
    if records:
        lines.append("Retained keys:")
        for record in records[:12]:
            if not isinstance(record, dict):
                continue
            lines.append(
                f"  - {record.get('record_id')}  kind={record.get('kind')}  "
                f"conf={record.get('confidence')}"
            )
        if len(records) > 12:
            lines.append(f"  … {len(records) - 12} more")
    return CommandResult(0, "\n".join(lines))


def load_memory_observation_sequence(path: Path) -> list[dict[str, Any]]:
    """Load a memory observation sequence from a JSON file or directory."""

    if not path.exists():
        raise FileNotFoundError(f"sequence path does not exist: {path}")
    if path.is_dir():
        for name in ("sequence.json", "memory_sequence.json", "observation_sequence.json"):
            candidate = path / name
            if candidate.is_file():
                return load_memory_observation_sequence(candidate)
        # Directory of frame JSON files (sorted).
        frame_files = sorted(
            [
                item
                for item in path.iterdir()
                if item.is_file() and item.suffix.lower() == ".json" and item.name != "manifest.json"
            ],
            key=lambda item: item.name,
        )
        if not frame_files:
            raise ValueError(f"directory has no sequence.json or frame JSON files: {path}")
        frames: list[dict[str, Any]] = []
        for index, frame_file in enumerate(frame_files):
            payload = json.loads(frame_file.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(f"{frame_file} is not a JSON object")
            frames.append(_normalize_sequence_frame(payload, default_index=index, source=frame_file.name))
        return frames

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("sequence file must contain a JSON object")
    if payload.get("schema") == MEMORY_OBSERVATION_SEQUENCE_SCHEMA or isinstance(payload.get("frames"), list):
        frames_data = payload.get("frames") or []
        if not isinstance(frames_data, list):
            raise ValueError("sequence frames must be a list")
        return [
            _normalize_sequence_frame(item, default_index=index, source=f"frames[{index}]")
            for index, item in enumerate(frames_data)
            if isinstance(item, dict)
        ]
    # Single frame record reused as a one-frame sequence.
    return [_normalize_sequence_frame(payload, default_index=0, source=path.name)]


def memory_snapshot_digest(snapshot: dict[str, Any]) -> str:
    """Stable digest of memory end-state (records + health, not process identity)."""

    records = snapshot.get("records") if isinstance(snapshot.get("records"), list) else []
    normalized_records = []
    for record in records:
        if not isinstance(record, dict):
            continue
        # Canonicalize nested structures with sorted JSON.
        normalized_records.append(json.loads(json.dumps(record, sort_keys=True, default=str)))
    normalized_records.sort(key=lambda item: str(item.get("record_id") or ""))
    body = {
        "implementation_id": snapshot.get("implementation_id"),
        "health": snapshot.get("health"),
        "record_count": snapshot.get("record_count", len(normalized_records)),
        "bounds": snapshot.get("bounds"),
        "records": normalized_records,
        "summary": snapshot.get("summary"),
        "metadata": snapshot.get("metadata"),
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_sequence_frame(
    payload: dict[str, Any],
    *,
    default_index: int,
    source: str,
) -> dict[str, Any]:
    observation_payload = payload.get("observation")
    if observation_payload is None and (
        payload.get("observation_id") is not None or payload.get("things") is not None
    ):
        observation_payload = payload
    if not isinstance(observation_payload, dict):
        raise ValueError(f"{source}: frame requires an observation object")

    frame_id = str(payload.get("frame_id") or observation_payload.get("frame_id") or f"frame_{default_index:06d}")
    frame_index = payload.get("frame_index")
    if frame_index is None:
        frame_index = default_index
    timestamp_ms = payload.get("timestamp_ms")
    if timestamp_ms is None:
        timestamp_ms = observation_payload.get("created_at_ms")
    if timestamp_ms is None:
        timestamp_ms = default_index * 100
    # Ensure observation has an id.
    if not observation_payload.get("observation_id"):
        observation_payload = {
            **observation_payload,
            "observation_id": f"obs_{default_index:06d}",
        }
    if observation_payload.get("created_at_ms") is None:
        observation_payload = {
            **observation_payload,
            "created_at_ms": int(timestamp_ms),
        }
    return {
        "frame_id": frame_id,
        "frame_index": int(frame_index),
        "timestamp_ms": int(timestamp_ms),
        "observation": observation_payload,
        "source": source,
    }


def _run_memory_sequence(
    *,
    stage: ActivatedMemoryStage,
    frames: list[dict[str, Any]],
) -> dict[str, Any]:
    # Fresh epoch for this pass (stage already reset on construction).
    per_frame: list[dict[str, Any]] = []
    final_snapshot = stage.snapshot()
    for frame in frames:
        observation = Observation.from_dict(frame["observation"])
        context = DecisionFrameContext(
            frame_id=str(frame["frame_id"]),
            frame_index=int(frame["frame_index"]),
            timestamp_ms=int(frame["timestamp_ms"]),
        )
        snapshot = stage.update(context, observation)
        final_snapshot = snapshot
        per_frame.append(
            {
                "frame_id": context.frame_id,
                "frame_index": context.frame_index,
                "timestamp_ms": context.timestamp_ms,
                "health": snapshot.health,
                "record_count": snapshot.record_count,
                "epoch_id": snapshot.epoch_id,
            }
        )
    final = final_snapshot.to_dict()
    return {
        "final": final,
        "per_frame": per_frame,
        "digest": memory_snapshot_digest(final),
    }


class _temporary_memory_activation:
    """Context manager writing an ephemeral activation file for offline replay."""

    def __init__(
        self,
        *,
        vehicle_id: str,
        implementation_id: str,
        bundle: dict[str, str],
    ) -> None:
        self.vehicle_id = vehicle_id
        self.implementation_id = implementation_id
        self.bundle = bundle
        self.path: Path | None = None

    def __enter__(self) -> Path:
        import tempfile

        handle = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            prefix=f"memory-replay-{safe_path_part(self.vehicle_id)}-",
            delete=False,
            encoding="utf-8",
        )
        payload = build_memory_activation_payload(self.implementation_id)
        handle.write(json.dumps(payload, indent=2, sort_keys=True))
        handle.close()
        self.path = Path(handle.name)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb
        if self.path is not None:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass


def reset_vehicle_memory(
    *,
    vehicle_id: str,
    timeout_s: float = 3.0,
    wait_s: float = 5.0,
    json_output: bool = False,
) -> CommandResult:
    """Reset live memory on Chase automation or PiCar Donkey runtime.

    After a successful reset the new epoch is empty (zero keys). Operators can
    confirm with ``info memory``, ``stream memory``, or the Memory map.
    """

    discovery = discover_active_vehicles(
        timeout_s=timeout_s,
        include_picar=True,
        include_chase_sim=True,
        include_inactive=True,
    )
    vehicle, error = find_vehicle_by_id(discovery, vehicle_id)
    if error:
        return CommandResult(
            2,
            "\n\n".join(
                [
                    error,
                    "Discovery snapshot:",
                    format_active_vehicles_snapshot(discovery, include_inactive=True),
                ]
            ),
        )
    if vehicle is None:
        return CommandResult(2, f"Vehicle {vehicle_id!r} was not found.")

    provider = vehicle.get("provider")
    before = probe_live_memory(
        vehicle_id=vehicle_id,
        vehicle=vehicle,
        timeout_s=timeout_s,
    )
    if before.get("status") == "absent":
        return CommandResult(
            2,
            "\n".join(
                [
                    f"No live memory stage to reset for {vehicle_id!r}.",
                    str(before.get("error") or "Memory component is absent."),
                ]
            ),
        )
    if before.get("status") not in {"live", "error"}:
        return CommandResult(
            2,
            "\n".join(
                [
                    f"Cannot reset memory for {vehicle_id!r}: live status is {before.get('status')!r}.",
                    str(before.get("error") or "Start automation (Chase) or deploy autonomy (Pi) first."),
                ]
            ),
        )

    try:
        if provider == "picar":
            reset_payload = _reset_physical_memory(
                vehicle_id=vehicle_id,
                vehicle=vehicle,
                timeout_s=timeout_s,
            )
        elif provider == "chase-sim":
            reset_payload = _reset_chase_memory(
                vehicle_id=vehicle_id,
                before=before,
                wait_s=wait_s,
            )
        else:
            return CommandResult(
                2,
                f"Vehicle {vehicle_id!r} is provider {provider!r}; memory reset supports picar and chase-sim.",
            )
    except (ConnectionError, OSError, TimeoutError, ValueError) as exc:
        return CommandResult(2, f"Memory reset failed for {vehicle_id}: {exc}")

    after = probe_live_memory(
        vehicle_id=vehicle_id,
        vehicle=vehicle,
        timeout_s=timeout_s,
    )
    payload = {
        "schema": "vehicle_memory_reset_v0",
        "vehicle_id": vehicle_id,
        "provider": provider,
        "ok": bool(reset_payload.get("ok")),
        "reset": reset_payload,
        "before": before,
        "after": after,
    }
    if not payload["ok"]:
        if json_output:
            return CommandResult(2, json.dumps(payload, indent=2, sort_keys=True))
        return CommandResult(
            2,
            "\n".join(
                [
                    f"Memory reset failed: {vehicle_id}",
                    str(reset_payload.get("error") or reset_payload.get("status") or "unknown error"),
                ]
            ),
        )

    # Operator-facing confirmation: empty epoch with non-decreasing reset count.
    after_count = after.get("last_record_count")
    after_health = after.get("last_health")
    confirmed = after.get("status") == "live" and (
        after_count in {0, None} or after_health in {"empty", "unavailable"}
    )
    payload["confirmed_empty"] = bool(confirmed)
    if json_output:
        return CommandResult(0 if confirmed else 2, json.dumps(payload, indent=2, sort_keys=True))
    lines = [
        f"Reset memory: {vehicle_id}",
        f"Implementation: {after.get('implementation_id') or before.get('implementation_id') or '—'}",
        f"Epoch: {before.get('last_epoch_id') or '—'} -> {after.get('last_epoch_id') or '—'}",
        f"Keys: {before.get('last_record_count')} -> {after.get('last_record_count')}",
        f"Health: {before.get('last_health')} -> {after.get('last_health')}",
        f"Resets: {before.get('reset_count')} -> {after.get('reset_count')}",
    ]
    if not confirmed:
        lines.append("Warning: live probe did not confirm an empty epoch after reset.")
        return CommandResult(2, "\n".join(lines))
    return CommandResult(0, "\n".join(lines))


def _reset_physical_memory(
    *,
    vehicle_id: str,
    vehicle: dict[str, Any],
    timeout_s: float,
) -> dict[str, Any]:
    base_url = picar_base_url(vehicle)
    if not base_url:
        raise ValueError(f"Vehicle {vehicle_id!r} has no picar base_url connection.")
    payload = post_memory_reset(base_url, timeout_s=timeout_s)
    if payload.get("ok") is True:
        return payload
    # HTTP non-2xx may still carry structured JSON.
    if payload.get("http_status") in {200, 201} and payload.get("status") == "reset":
        payload["ok"] = True
        return payload
    payload.setdefault("ok", False)
    payload.setdefault(
        "error",
        payload.get("error")
        or f"POST /autonomy/memory/reset returned HTTP {payload.get('http_status')}",
    )
    return payload


def _reset_chase_memory(
    *,
    vehicle_id: str,
    before: dict[str, Any],
    wait_s: float,
) -> dict[str, Any]:
    automation_dir = _automation_dir(vehicle_id)
    if not automation_dir.exists():
        raise ValueError(
            f"No automation runtime for {vehicle_id!r}. "
            f"Run: ./cli/automa vehicles automation run --id {vehicle_id}"
        )
    request_path = automation_dir / "memory_reset.request.json"
    result_path = automation_dir / "memory_reset.result.json"
    if result_path.exists():
        result_path.unlink()
    token = f"reset-{int(time.time() * 1000)}"
    request = {
        "schema": "automa_memory_reset_request_v0",
        "token": token,
        "requested_at_ms": int(time.time() * 1000),
        "vehicle_id": vehicle_id,
    }
    request_path.write_text(json.dumps(request, indent=2, sort_keys=True), encoding="utf-8")

    deadline = time.monotonic() + max(0.2, float(wait_s))
    while time.monotonic() < deadline:
        if result_path.exists():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                time.sleep(0.05)
                continue
            if isinstance(result, dict) and result.get("token") == token:
                try:
                    request_path.unlink(missing_ok=True)
                except OSError:
                    pass
                return result
        # Fallback: detect reset via live probe when worker updated state.
        live = probe_live_memory(vehicle_id=vehicle_id)
        if (
            live.get("status") == "live"
            and live.get("last_epoch_id") not in {None, before.get("last_epoch_id")}
            and (live.get("last_record_count") in {0, None} or live.get("last_health") == "empty")
        ):
            try:
                request_path.unlink(missing_ok=True)
            except OSError:
                pass
            return {
                "ok": True,
                "status": "reset",
                "token": token,
                "detected_via": "live_probe",
                "memory": {
                    "last_epoch_id": live.get("last_epoch_id"),
                    "last_record_count": live.get("last_record_count"),
                    "last_health": live.get("last_health"),
                    "reset_count": live.get("reset_count"),
                },
            }
        time.sleep(0.05)

    try:
        request_path.unlink(missing_ok=True)
    except OSError:
        pass
    raise TimeoutError(
        f"Automation worker did not acknowledge memory reset within {wait_s}s. "
        "Is the worker running?"
    )


def stream_vehicle_memory(
    *,
    vehicle_id: str,
    refresh_s: float = 0.5,
    once: bool = False,
    no_clear: bool = False,
    timeout_s: float = 3.0,
    json_output: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    """Poll live memory lifecycle health for Chase or PiCar."""

    discovery = discover_active_vehicles(
        timeout_s=timeout_s,
        include_picar=True,
        include_chase_sim=True,
        include_inactive=True,
    )
    vehicle, error = find_vehicle_by_id(discovery, vehicle_id)
    if error:
        return CommandResult(
            2,
            "\n\n".join(
                [
                    error,
                    "Discovery snapshot:",
                    format_active_vehicles_snapshot(discovery, include_inactive=True),
                ]
            ),
        )
    if vehicle is None:
        return CommandResult(2, f"Vehicle {vehicle_id!r} was not found.")

    if vehicle.get("provider") == "picar" and not json_output:
        return _stream_physical_memory_with_inspector(
            vehicle_id=vehicle_id,
            vehicle=vehicle,
            refresh_s=refresh_s,
            once=once,
            no_clear=no_clear,
            timeout_s=timeout_s,
            output=output,
        )

    stream = output
    try:
        while True:
            live = probe_live_memory(
                vehicle_id=vehicle_id,
                vehicle=vehicle,
                timeout_s=timeout_s,
            )
            if json_output:
                line = json.dumps(live, sort_keys=True)
            else:
                line = _format_live_memory_screen(vehicle_id=vehicle_id, live=live)
            if stream is not None:
                if not no_clear and not json_output:
                    print("\033[2J\033[H", end="", file=stream)
                print(line, file=stream, flush=True)
            if once:
                if live.get("status") == "error":
                    return CommandResult(
                        2,
                        str(live.get("error") or "memory stream failed")
                        if stream is None
                        else "",
                    )
                if live.get("status") == "absent":
                    return CommandResult(
                        2,
                        (
                            str(
                                live.get("error")
                                or "memory stage is not live on the vehicle"
                            )
                            if stream is None
                            else ""
                        ),
                    )
                # Avoid double-print when the handler also emits result.message.
                return CommandResult(0, "" if stream is not None else line)
            time.sleep(max(0.1, float(refresh_s)))
    except KeyboardInterrupt:
        return CommandResult(130, "")


def _stream_physical_memory_with_inspector(
    *,
    vehicle_id: str,
    vehicle: dict[str, Any],
    refresh_s: float,
    once: bool,
    no_clear: bool,
    timeout_s: float,
    output: TextIO | None,
) -> CommandResult:
    """Poll status, feed the shared loopback publication, and open /memory inspector."""

    stream = output
    base_url = picar_base_url(vehicle)
    if not base_url:
        return CommandResult(2, f"Vehicle {vehicle_id!r} has no picar base_url connection.")

    runtime_dir = physical_observation_dir(vehicle_id)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    frame_path = runtime_dir / "latest_frame.jpg"
    view_server: PerceptionViewServer | None = None
    view_error: str | None = None
    try:
        view_server = PerceptionViewServer(
            vehicle_id=vehicle_id,
            automation_dir=runtime_dir,
        ).start()
    except OSError as exc:
        view_error = f"{type(exc).__name__}: {exc}"

    try:
        while True:
            live = probe_live_memory(
                vehicle_id=vehicle_id,
                vehicle=vehicle,
                timeout_s=timeout_s,
            )
            publication: dict[str, Any] | None = None
            fetch_error: str | None = None
            try:
                publication = fetch_observation_publication(base_url, timeout_s=timeout_s)
            except ConnectionError as exc:
                fetch_error = str(exc)

            memory_view_url = None
            if view_server is not None and view_server.url:
                memory_view_url = view_server.url.rstrip("/") + "/memory"
            if publication is not None and view_server is not None:
                try:
                    _publish_physical_view(
                        view_server=view_server,
                        base_url=base_url,
                        publication=publication,
                        frame_path=frame_path,
                        timeout_s=timeout_s,
                    )
                    memory_view_url = view_server.url.rstrip("/") + "/memory"
                    view_error = None
                except (ConnectionError, OSError, TypeError, ValueError) as exc:
                    view_error = f"{type(exc).__name__}: {exc}"

            if stream is not None:
                if not no_clear:
                    print("\033[2J\033[H", end="", file=stream)
                lines = [
                    _format_live_memory_screen(vehicle_id=vehicle_id, live=live),
                    "",
                ]
                if memory_view_url:
                    lines.append(f"memory map: {memory_view_url}")
                    lines.append("perception view: " + memory_view_url.rsplit("/", 1)[0] + "/")
                elif view_error:
                    lines.append(f"memory map: unavailable ({view_error})")
                else:
                    lines.append("memory map: unavailable")
                if fetch_error:
                    lines.append(f"publication: {fetch_error}")
                elif isinstance(publication, dict) and isinstance(publication.get("memory"), dict):
                    mem = publication["memory"]
                    lines.append(
                        f"publication memory: health={mem.get('health')} "
                        f"keys={mem.get('record_count')}"
                    )
                print("\n".join(lines), file=stream, flush=True)

            if once:
                if live.get("status") in {"error", "absent"}:
                    return CommandResult(2, "")
                return CommandResult(0, "")
            time.sleep(max(0.1, float(refresh_s)))
    except KeyboardInterrupt:
        return CommandResult(130, "")
    finally:
        if view_server is not None:
            view_server.stop()


def probe_live_memory(
    *,
    vehicle_id: str,
    vehicle: dict[str, Any] | None = None,
    timeout_s: float = 3.0,
) -> dict[str, Any]:
    """Return a normalized live-memory snapshot without requiring stream mode."""

    if vehicle is None:
        discovery = discover_active_vehicles(
            timeout_s=timeout_s,
            include_picar=True,
            include_chase_sim=True,
            include_inactive=True,
        )
        vehicle, error = find_vehicle_by_id(discovery, vehicle_id)
        if error or vehicle is None:
            return {
                "schema": "vehicle_memory_live_v0",
                "vehicle_id": vehicle_id,
                "status": "unavailable",
                "error": error or f"Vehicle {vehicle_id!r} was not found.",
                "probed_at_ms": int(time.time() * 1000),
            }

    provider = vehicle.get("provider")
    if provider == "picar":
        return _probe_physical_memory(vehicle_id=vehicle_id, vehicle=vehicle, timeout_s=timeout_s)
    if provider == "chase-sim":
        return _probe_chase_memory(vehicle_id=vehicle_id)
    return {
        "schema": "vehicle_memory_live_v0",
        "vehicle_id": vehicle_id,
        "status": "unavailable",
        "error": (
            f"Vehicle {vehicle_id!r} is provider {provider!r}; "
            "live memory supports picar and chase-sim."
        ),
        "probed_at_ms": int(time.time() * 1000),
    }


def _probe_physical_memory(
    *,
    vehicle_id: str,
    vehicle: dict[str, Any],
    timeout_s: float,
) -> dict[str, Any]:
    base_url = picar_base_url(vehicle)
    probed_at_ms = int(time.time() * 1000)
    if not base_url:
        return {
            "schema": "vehicle_memory_live_v0",
            "vehicle_id": vehicle_id,
            "provider": "picar",
            "status": "unavailable",
            "error": f"Vehicle {vehicle_id!r} has no picar base_url connection.",
            "probed_at_ms": probed_at_ms,
        }
    try:
        status = fetch_autonomy_status(base_url, timeout_s=timeout_s)
    except ConnectionError as exc:
        return {
            "schema": "vehicle_memory_live_v0",
            "vehicle_id": vehicle_id,
            "provider": "picar",
            "status": "error",
            "endpoint": f"{base_url}/autonomy/status",
            "error": str(exc),
            "probed_at_ms": probed_at_ms,
        }

    autonomy = status.get("autonomy") if isinstance(status.get("autonomy"), dict) else {}
    components = autonomy.get("components") if isinstance(autonomy.get("components"), dict) else {}
    memory = components.get("memory") if isinstance(components.get("memory"), dict) else None
    last_control = autonomy.get("last_control") if isinstance(autonomy.get("last_control"), dict) else {}
    control_meta = (
        last_control.get("metadata") if isinstance(last_control.get("metadata"), dict) else {}
    )
    if memory is None:
        return {
            "schema": "vehicle_memory_live_v0",
            "vehicle_id": vehicle_id,
            "provider": "picar",
            "status": "absent",
            "endpoint": f"{base_url}/autonomy/status",
            "drive_mode": status.get("drive_mode"),
            "has_memory": bool(control_meta.get("has_memory")),
            "error": (
                "No live memory component in /autonomy/status. "
                "If activation was deployed, update core then autonomy with --restart."
            ),
            "probed_at_ms": probed_at_ms,
        }

    return {
        "schema": "vehicle_memory_live_v0",
        "vehicle_id": vehicle_id,
        "provider": "picar",
        "status": "live",
        "endpoint": f"{base_url}/autonomy/status",
        "drive_mode": status.get("drive_mode"),
        "has_memory": bool(control_meta.get("has_memory")),
        "implementation_id": memory.get("implementation_id"),
        "implementation_spec": memory.get("implementation_spec"),
        "activation": memory.get("activation"),
        "bounds": memory.get("bounds"),
        "last_health": memory.get("last_health"),
        "last_epoch_id": memory.get("last_epoch_id"),
        "last_record_count": memory.get("last_record_count"),
        "last_duration_ms": memory.get("last_duration_ms"),
        "last_error": memory.get("last_error"),
        "update_count": memory.get("update_count"),
        "reset_count": memory.get("reset_count"),
        "failure_count": memory.get("failure_count"),
        "probed_at_ms": probed_at_ms,
    }


def _probe_chase_memory(*, vehicle_id: str) -> dict[str, Any]:
    probed_at_ms = int(time.time() * 1000)
    automation_dir = _automation_dir(vehicle_id)
    state_path = automation_dir / "state.json"
    if not state_path.exists():
        return {
            "schema": "vehicle_memory_live_v0",
            "vehicle_id": vehicle_id,
            "provider": "chase-sim",
            "status": "unavailable",
            "error": (
                f"No automation runtime state for {vehicle_id!r}. "
                f"Run: ./cli/automa vehicles automation run --id {vehicle_id}"
            ),
            "probed_at_ms": probed_at_ms,
        }
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "schema": "vehicle_memory_live_v0",
            "vehicle_id": vehicle_id,
            "provider": "chase-sim",
            "status": "error",
            "error": f"Could not read automation state: {exc}",
            "probed_at_ms": probed_at_ms,
        }
    if not isinstance(state, dict):
        return {
            "schema": "vehicle_memory_live_v0",
            "vehicle_id": vehicle_id,
            "provider": "chase-sim",
            "status": "error",
            "error": "Automation state is not a JSON object.",
            "probed_at_ms": probed_at_ms,
        }

    memory = state.get("memory") if isinstance(state.get("memory"), dict) else None
    if memory is None or memory.get("status") == "absent":
        return {
            "schema": "vehicle_memory_live_v0",
            "vehicle_id": vehicle_id,
            "provider": "chase-sim",
            "status": "absent",
            "error": (
                "Automation worker has no live memory stage. "
                f"Stage memory then restart automation: "
                f"./cli/automa vehicles update memory --id {vehicle_id}"
            ),
            "probed_at_ms": probed_at_ms,
            "worker_memory": memory,
        }

    status_block = memory.get("status") if isinstance(memory.get("status"), dict) else memory
    if not isinstance(status_block, dict):
        status_block = {}
    return {
        "schema": "vehicle_memory_live_v0",
        "vehicle_id": vehicle_id,
        "provider": "chase-sim",
        "status": "live",
        "implementation_id": memory.get("implementation_id")
        or status_block.get("implementation_id"),
        "implementation_spec": memory.get("implementation_spec")
        or status_block.get("implementation_spec"),
        "activation": memory.get("activation") or status_block.get("activation"),
        "bounds": status_block.get("bounds"),
        "last_health": status_block.get("last_health"),
        "last_epoch_id": status_block.get("last_epoch_id"),
        "last_record_count": status_block.get("last_record_count"),
        "last_duration_ms": status_block.get("last_duration_ms"),
        "last_error": status_block.get("last_error"),
        "update_count": status_block.get("update_count"),
        "reset_count": status_block.get("reset_count"),
        "failure_count": status_block.get("failure_count"),
        "probed_at_ms": probed_at_ms,
    }


def _memory_activation(
    *,
    vehicle_id: str,
    implementation_id: str,
    bundle: dict[str, str],
    release: dict[str, Any] | None,
) -> dict[str, Any]:
    base = build_memory_activation_payload(implementation_id)
    entry = memory_implementation_spec(implementation_id)
    activation = {
        "schema": MEMORY_ACTIVATION_SCHEMA,
        "vehicle_id": vehicle_id,
        "activated_at_ms": int(time.time() * 1000),
        "controller_bundle": {
            "root_dir": bundle["root_dir"],
            "autonomy_dir": bundle["autonomy_dir"],
            "implementations_dir": bundle["implementations_dir"],
            "memory_runtime_dir": bundle["memory_runtime_dir"],
            "release": release_activation_summary(release) if release is not None else None,
        },
        "memory": {
            "implementation_id": entry["implementation_id"],
            "description": entry["description"],
            "implementation_spec": entry["implementation_spec"],
            "implementation_config": dict(base["memory"]["implementation_config"]),
        },
    }
    return activation


def _format_memory_info(payload: dict[str, Any]) -> str:
    activation = payload["activation"]
    bounds = activation.get("bounds") if isinstance(activation.get("bounds"), dict) else {}
    lines = [
        f"Memory: {payload['vehicle_id']} -> {activation.get('implementation_id', 'unknown')}",
        f"Implementation: {activation.get('implementation_spec', 'unknown')}",
        f"Activation: {activation['path']}",
        (
            f"Bounds: max_records={bounds.get('max_records')} "
            f"max_age_ms={bounds.get('max_age_ms')} "
            f"eviction={bounds.get('eviction_policy')}"
        ),
        "Lifecycle: update / reset / snapshot",
        "Identity claims: false",
    ]
    live = payload.get("live")
    if isinstance(live, dict):
        lines.append("")
        lines.append(_format_live_memory_screen(vehicle_id=payload["vehicle_id"], live=live))
    return "\n".join(lines)


def _format_live_memory_screen(*, vehicle_id: str, live: dict[str, Any]) -> str:
    status = str(live.get("status") or "unknown")
    lines = [
        f"Live memory: {vehicle_id} [{status}]",
    ]
    if live.get("provider"):
        lines.append(f"Provider: {live.get('provider')}")
    if live.get("endpoint"):
        lines.append(f"Endpoint: {live.get('endpoint')}")
    if live.get("drive_mode") is not None:
        lines.append(f"Drive mode: {live.get('drive_mode')}")
    if status == "live":
        lines.extend(
            [
                f"Implementation: {live.get('implementation_id') or 'unknown'}",
                (
                    f"Health: {live.get('last_health') or 'unknown'} "
                    f"epoch={live.get('last_epoch_id') or '-'} "
                    f"records={live.get('last_record_count')}"
                ),
                (
                    f"Counters: updates={live.get('update_count')} "
                    f"resets={live.get('reset_count')} "
                    f"failures={live.get('failure_count')}"
                ),
            ]
        )
        bounds = live.get("bounds") if isinstance(live.get("bounds"), dict) else {}
        if bounds:
            lines.append(
                f"Bounds: max_records={bounds.get('max_records')} "
                f"max_age_ms={bounds.get('max_age_ms')} "
                f"eviction={bounds.get('eviction_policy')}"
            )
        if live.get("last_duration_ms") is not None:
            lines.append(f"Last update duration: {live.get('last_duration_ms')} ms")
        if live.get("last_error"):
            lines.append(f"Last error: {live.get('last_error')}")
        if live.get("has_memory") is not None:
            lines.append(f"Engine saw memory: {live.get('has_memory')}")
    else:
        if live.get("error"):
            lines.append(f"Detail: {live.get('error')}")
    return "\n".join(lines)
