"""Stage, inspect, stream and reset vehicle memory."""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from autonomy.decision_cycle.activation import read_step_activation
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionSteps
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.plugins import DuplicatePluginIdError
from implementations.decision_cycle.catalog import selection_activation
from implementations.decision_cycle.memory.presets import (
    MEMORY_PRESETS,
    available_memory_preset_ids,
)

from .automation import (
    CHASE_WORKER_PROBE_MAX_AGE_MS,
    _automation_dir,
    assess_chase_worker_liveness,
)
from .bundles import (
    controller_bundle_paths,
    release_activation_summary,
    sync_controller_bundle,
)
from .inspection_runs import recorded_selection, selection_record
from .memory_report import last_plugin_state, memory_summary
from .paths import ROOT, display_path, safe_path_part
from .physical_observation import (
    fetch_autonomy_status,
    fetch_observation_publication,
    physical_observation_dir,
    picar_base_url,
    post_memory_reset,
)
from .runtime_view import RuntimeViewServer
from .step_activations import (
    refresh_release,
    stage_activation,
    staging_vehicle,
    step_update_error,
)
from .streaming import (
    _publish_physical_view,
    once_stream_outcome,
    unavailable_stream_outcome,
)
from .vehicles import (
    DEFAULT_CHASE_READINESS_TIMEOUT_S,
    discover_active_vehicles,
    find_vehicle_by_id,
    format_active_vehicles,
)
from .workbench_frames import run_frame
from .workbench_source import (
    SourceValidationError,
    normalize_image_directory,
    normalize_image_file,
    read_image_manifest,
)

RUNTIME_ROOT = Path(os.environ.get("AUTOMA_RUNTIME_ROOT", ROOT / "runtime" / "vehicles"))
INSPECT_ROOT = Path(
    os.environ.get("AUTOMA_MEMORY_INSPECT_ROOT", ROOT / "runtime" / "memory-inspections")
)
MEMORY_INSPECT_SCHEMA = "memory_inspect_v0"


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


def update_vehicle_memory(
    *,
    vehicle_id: str,
    preset: str | None = None,
    plugins: list[str] | None = None,
    timeout_s: float = DEFAULT_CHASE_READINESS_TIMEOUT_S,
    dry_run: bool = False,
    json_output: bool = False,
    verbose: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    activation, error = _selected_memory(preset, plugins)
    if error is not None:
        return error

    selected = list(activation.plugins)
    stream = output if verbose else None
    vehicle, unknown = staging_vehicle(vehicle_id, runtime_root=RUNTIME_ROOT, timeout_s=timeout_s, output=stream)
    if unknown is not None:
        return CommandResult(*step_update_error(vehicle_id, "memory", "unknown_vehicle", unknown, json_output=json_output))
    vehicle_runtime_dir = RUNTIME_ROOT / safe_path_part(vehicle_id)
    bundle = controller_bundle_paths(vehicle_runtime_dir)
    activation_path = Path(bundle["memory_runtime_dir"]) / "active.json"
    release: dict[str, Any] | None = None

    if not dry_run:
        release = sync_controller_bundle(bundle, output=stream)
        stage_activation(bundle, activation, vehicle_id=vehicle_id, release=release, vehicle=vehicle)

    payload = {
        "schema": "vehicle_memory_update_v1",
        "vehicle_id": vehicle_id,
        "preset": activation.metadata["preset"],
        "plugins": selected,
        "dry_run": dry_run,
        "activation": display_path(activation_path),
        "manifest": activation.to_payload(),
        "release": release_activation_summary(release) if release is not None else None,
    }
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    verb = "Would activate" if dry_run else "Updated memory"
    return CommandResult(
        0,
        "\n".join(
            [
                f"{verb}: {vehicle_id} -> {', '.join(selected)}",
                f"Preset: {activation.metadata['preset']}",
                *(f"Plugin: {plugin_id} ({activation.plugin_specs[plugin_id]})" for plugin_id in selected),
                f"Activation: {display_path(activation_path)}",
            ]
        ),
    )


def ensure_vehicle_memory_activation(
    *,
    vehicle_id: str,
    bundle: dict[str, str],
    release: dict[str, Any],
    plugins: list[str] | None = None,
) -> Path:
    """Ensure a memory activation exists for autonomy deploy, recording ``release``."""

    path = refresh_release(bundle, "memory", release)
    if path is not None:
        return path
    return stage_activation(
        bundle,
        selection_activation("memory", plugins=plugins),
        vehicle_id=vehicle_id,
        release=release,
    )


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
                    f"No active memory preset found for {vehicle_id!r}.",
                    f"Expected activation: {display_path(activation_path)}",
                    "Run: ./cli/automa vehicles update memory --id <vehicle_id>",
                ]
            ),
        )

    try:
        activation = read_step_activation(activation_path, "memory")
        manager = activation.plugin_manager()
        available = manager.available
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        return CommandResult(
            2,
            f"Could not read memory activation {display_path(activation_path)}: {exc}",
        )

    payload: dict[str, Any] = {
        "schema": "vehicle_memory_info_v0",
        "vehicle_id": vehicle_id,
        "activation": {
            "path": display_path(activation_path),
            "preset": activation.metadata.get("preset"),
            "plugins": list(manager.selected_ids),
            "available_plugins": sorted(item.plugin_id for item in available),
            "plugin_specs": {item.plugin_id: item.entrypoint for item in available},
            "plugin_configs": {item.plugin_id: dict(item.config) for item in available},
        },
        "controller_bundle": activation.metadata.get("controller_bundle"),
        "lifecycle": {
            "methods": ["update", "reset", "status"],
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


def inspect_memory(
    source: str,
    *,
    preset: str | None = None,
    plugins: list[str] | None = None,
    record: bool = False,
    json_output: bool = False,
) -> CommandResult:
    """Run an image source through perception, observation and memory, frame by frame.

    Reports what each memory plugin retained after every frame. A recorded run
    restores its executable perception and memory selections; explicit flags
    override memory. Otherwise each step uses its default. It reads the source
    only; live frames come from ``perception inspect --record``.
    """

    activation, error = _selected_memory(preset, plugins)
    if error is not None:
        return error
    path = Path(source).expanduser()
    try:
        image_source = normalize_image_file(path) if path.is_file() else normalize_image_directory(path)
        _manifest_path, source_manifest = (
            read_image_manifest(image_source.source_path) if path.is_dir() else (None, None)
        )
    except SourceValidationError as exc:
        return CommandResult(2, f"Could not read memory inspect source: {exc}")
    try:
        source_manifest = source_manifest or {}
        perception_activation = (
            recorded_selection("perception", source_manifest) or selection_activation("perception")
        )
        if preset is None and plugins is None:
            activation = recorded_selection("memory", source_manifest) or activation
        mapper = PerceptionRunner.from_activation(perception_activation)
        memory_step = MemoryRunner.from_activation(activation)
    except Exception as exc:  # Plugin construction is a CLI preflight boundary.
        return CommandResult(2, f"Could not load plugins for memory inspect: {type(exc).__name__}: {exc}")

    shared_memory: dict[str, Any] = {}
    frames: list[dict[str, Any]] = []
    for frame in image_source.frames:
        seen: dict[str, Observation | None] = {}

        def memory(context: DecisionFrameContext, observation: Observation | None) -> dict[str, Any]:
            seen["observation"] = observation
            return memory_step(context, observation)

        try:
            outcome = run_frame(
                frame,
                perception_step=mapper,
                memory_step=memory,
                steps=DecisionSteps(),
                shared_memory=shared_memory,
            )
        except Exception as exc:  # Plugins are third-party code; name the frame that broke.
            return CommandResult(
                2, f"Memory inspect failed at {frame.frame_id}: {type(exc).__name__}: {exc}"
            )
        result = outcome.result
        frames.append(
            {
                "frame_id": frame.frame_id,
                "frame_index": frame.frame_index,
                "timestamp_ms": frame.timestamp_ms,
                "image_path": str(frame.image_path) if frame.image_path is not None else None,
                "absence_reason": frame.absence_reason,
                "plugins": [
                    {"plugin_id": item.get("plugin_id"), **memory_summary(item.get("state"))}
                    for item in (result.memory or {}).get("plugins") or []
                ],
                "observation": _observation_counts(seen.get("observation")),
            }
        )

    run_id = _inspect_run_id(image_source.source_id)
    report = {
        "schema": MEMORY_INSPECT_SCHEMA,
        "run_id": run_id,
        "source_id": image_source.source_id,
        "source": {
            "path": str(image_source.source_path),
            "source_id": image_source.source_id,
            "frame_count": len(frames),
        },
        "perception": {
            **selection_record(perception_activation),
            "plugins": list(perception_activation.plugins),
        },
        "memory": {**selection_record(activation), "plugins": list(activation.plugins)},
        "frames": frames,
        "final": memory_step.report(),
        "run_dir": None,
    }
    if record:
        run_dir = INSPECT_ROOT / run_id
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
            report["run_dir"] = str(run_dir)
            frames_dir = run_dir / "frames"
            frames_dir.mkdir()
            for frame, recorded_frame in zip(image_source.frames, report["frames"]):
                if frame.image_path is not None:
                    relative = (
                        Path("frames") / f"frame_{frame.position:06d}{frame.image_path.suffix.lower()}"
                    )
                    shutil.copyfile(frame.image_path, run_dir / relative)
                    recorded_frame["image_path"] = relative.as_posix()
            (run_dir / "report.json").write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            return CommandResult(2, f"Could not record memory inspect run {run_dir}: {exc}")
    if json_output:
        return CommandResult(0, json.dumps(report, indent=2, sort_keys=True))
    return CommandResult(0, _format_inspect_report(report))


def _selected_memory(preset: str | None, plugins: list[str] | None) -> tuple[Any, CommandResult | None]:
    """The memory selection a command names, or the error to report for it."""

    if preset is not None and plugins:
        return None, CommandResult(2, "Choose either --preset or --plugin, not both.")
    if preset is not None and preset not in MEMORY_PRESETS:
        available = ", ".join(available_memory_preset_ids())
        return None, CommandResult(
            2, f"Unknown memory preset {preset!r}. Available presets: {available}."
        )
    try:
        return selection_activation("memory", preset=preset, plugins=plugins or None), None
    except DuplicatePluginIdError:
        # A packaged-catalog clash is not a bad selection; the CLI reports it.
        raise
    except ValueError as exc:
        return None, CommandResult(2, str(exc))


def _observation_counts(observation: Observation | None) -> dict[str, Any] | None:
    if observation is None:
        return None
    return {
        "observation_id": observation.observation_id,
        "things": len(observation.things),
        "signals": len(observation.signals),
    }


def _inspect_run_id(source_id: str) -> str:
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    return f"inspect-{safe_path_part(source_id)}-{stamp}"


def _format_inspect_report(report: dict[str, Any]) -> str:
    source = report["source"]
    lines = [
        "Memory inspect",
        "--------------",
        f"Source: {source['path']} ({source['frame_count']} frames)",
        f"Perception: {report['perception']['preset']} ({', '.join(report['perception']['plugins'])})",
        f"Memory: {report['memory']['preset']} ({', '.join(report['memory']['plugins'])})",
        "",
        "Frame  Plugin  Health  Records  Epoch  Observation",
    ]
    for frame in report["frames"]:
        observation = frame["observation"]
        seen = f"{observation['things']}t/{observation['signals']}s" if observation else "-"
        for item in frame["plugins"]:
            lines.append(
                f"{frame['frame_index']}  {item['plugin_id']}  {item['health']}  "
                f"{item['record_count']}  {item['epoch_id']}  {seen}"
            )
    lines.append("")
    lines.append("Final")
    for item in (report["final"] or {}).get("plugins") or []:
        summary = memory_summary(item.get("state"))
        lines.append(
            f"  {item.get('plugin_id')}: {summary['health']}, "
            f"{summary['record_count']} records, epoch {summary['epoch_id']}"
        )
    if report["run_dir"]:
        lines.extend(["", f"Recorded: {display_path(Path(report['run_dir']))}"])
    return "\n".join(lines)


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
                    "Discovery:",
                    format_active_vehicles(discovery, include_inactive=True),
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
                    f"No live memory step to reset for {vehicle_id!r}.",
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
        f"Plugins: {', '.join(after.get('plugin_ids') or before.get('plugin_ids') or []) or '—'}",
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
        # This is already the Chase file protocol. Avoid general vehicle
        # discovery here: it can outlast the acknowledgement deadline and
        # hide a result the worker has already written.
        live = _probe_chase_memory(vehicle_id=vehicle_id)
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
    """Poll live memory lifecycle health for Chase or PiCar.

    JSON mode emits probes even when discovery fails. ``live`` means the
    retained step is available; update health stays in the plugin diagnostics.
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
            *unavailable_stream_outcome(
                step="memory",
                vehicle_id=vehicle_id,
                message="\n\n".join(
                    [
                        error,
                        "Discovery:",
                        format_active_vehicles(discovery, include_inactive=True),
                    ]
                ),
                json_output=json_output,
                stream=output,
            )
        )
    if vehicle is None:
        return CommandResult(
            *unavailable_stream_outcome(
                step="memory",
                vehicle_id=vehicle_id,
                message=f"Vehicle {vehicle_id!r} was not found.",
                json_output=json_output,
                stream=output,
            )
        )

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
                return CommandResult(
                    *once_stream_outcome(step="memory", live=live, line=line, stream=stream)
                )
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
    """Poll status, feed the shared loopback publication, and serve the /memory inspector."""

    stream = output
    base_url = picar_base_url(vehicle)
    if not base_url:
        return CommandResult(2, f"Vehicle {vehicle_id!r} has no picar base_url connection.")

    runtime_dir = physical_observation_dir(vehicle_id)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    frame_path = runtime_dir / "latest_frame.jpg"
    view_server: RuntimeViewServer | None = None
    view_error: str | None = None
    try:
        view_server = RuntimeViewServer(
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
                    lines.append("perception view: " + memory_view_url.rsplit("/", 1)[0] + "/perception")
                elif view_error:
                    lines.append(f"memory map: unavailable ({view_error})")
                else:
                    lines.append("memory map: unavailable")
                if fetch_error:
                    lines.append(f"publication: {fetch_error}")
                # The published memory report's last plugin state.
                elif isinstance(publication, dict) and last_plugin_state(publication.get("memory")):
                    mem = last_plugin_state(publication.get("memory"))
                    lines.append(
                        f"publication memory: health={mem.get('health')} "
                        f"keys={mem.get('record_count')}"
                    )
                print("\n".join(lines), file=stream, flush=True)

            if once:
                return CommandResult(
                    *once_stream_outcome(step="memory", live=live, line="", stream=stream)
                )
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
    """Return a normalized live-memory probe without requiring stream mode."""

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
    steps = autonomy.get("steps") if isinstance(autonomy.get("steps"), dict) else {}
    memory = steps.get("memory") if isinstance(steps.get("memory"), dict) else None
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
                "No live memory step in /autonomy/status. "
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
        "activation": memory.get("activation"),
        "plugin_ids": memory.get("plugin_ids", []),
        "selected_plugin_ids": memory.get("selected_plugin_ids", []),
        "plugins": memory.get("plugins", []),
        "plugin_report": memory.get("plugin_report"),
        "bounds": (last_plugin_state(memory) or {}).get("bounds"),
        "last_health": (last_plugin_state(memory) or {}).get("health"),
        "last_epoch_id": (last_plugin_state(memory) or {}).get("epoch_id"),
        "last_record_count": (last_plugin_state(memory) or {}).get("record_count"),
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

    liveness = assess_chase_worker_liveness(
        state=state,
        probed_at_ms=probed_at_ms,
        step="memory",
        vehicle_id=vehicle_id,
    )
    if not liveness["live"]:
        return {
            "schema": "vehicle_memory_live_v0",
            "vehicle_id": vehicle_id,
            "provider": "chase-sim",
            "status": liveness["status"],
            "error": liveness["error"],
            "probed_at_ms": probed_at_ms,
            "worker_status": state.get("status"),
            "worker_pid": liveness.get("pid"),
            "worker_updated_at_ms": liveness.get("updated_at_ms"),
            "max_age_ms": CHASE_WORKER_PROBE_MAX_AGE_MS,
        }

    memory = state.get("memory") if isinstance(state.get("memory"), dict) else None
    if memory is None or memory.get("status") == "absent":
        return {
            "schema": "vehicle_memory_live_v0",
            "vehicle_id": vehicle_id,
            "provider": "chase-sim",
            "status": "absent",
            "error": (
                "Automation worker has no live memory step. "
                f"Stage memory then restart automation: "
                f"./cli/automa vehicles update memory --id {vehicle_id}"
            ),
            "probed_at_ms": probed_at_ms,
            "worker_memory": memory,
            "worker_status": state.get("status"),
            "worker_pid": liveness.get("pid"),
        }

    status_block = memory.get("status") if isinstance(memory.get("status"), dict) else memory
    if not isinstance(status_block, dict):
        status_block = {}
    return {
        "schema": "vehicle_memory_live_v0",
        "vehicle_id": vehicle_id,
        "provider": "chase-sim",
        "status": "live",
        "activation": memory.get("activation") or status_block.get("activation"),
        "plugin_ids": status_block.get("plugin_ids", []),
        "selected_plugin_ids": status_block.get("selected_plugin_ids", []),
        "plugins": status_block.get("plugins", []),
        "plugin_report": status_block.get("plugin_report"),
        "bounds": (last_plugin_state(status_block) or {}).get("bounds"),
        "last_health": (last_plugin_state(status_block) or {}).get("health"),
        "last_epoch_id": (last_plugin_state(status_block) or {}).get("epoch_id"),
        "last_record_count": (last_plugin_state(status_block) or {}).get("record_count"),
        "last_duration_ms": status_block.get("last_duration_ms"),
        "last_error": status_block.get("last_error"),
        "update_count": status_block.get("update_count"),
        "reset_count": status_block.get("reset_count"),
        "failure_count": status_block.get("failure_count"),
        "probed_at_ms": probed_at_ms,
        "worker_status": state.get("status"),
        "worker_pid": liveness.get("pid"),
        "run_id": state.get("run_id"),
        "worker_updated_at_ms": liveness.get("updated_at_ms"),
    }


def _format_memory_info(payload: dict[str, Any]) -> str:
    activation = payload["activation"]
    lines = [
        f"Memory: {payload['vehicle_id']} -> {activation.get('preset') or 'unknown'}",
        f"Enabled plugins: {', '.join(activation.get('plugins', [])) or 'none'}",
        f"Available plugins: {', '.join(activation.get('available_plugins', [])) or 'none'}",
        f"Activation: {activation['path']}",
        "Lifecycle: update / reset / status",
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
                f"Applied plugins: {', '.join(live.get('plugin_ids', [])) or 'none'}",
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
