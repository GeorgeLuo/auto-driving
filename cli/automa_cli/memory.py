"""Stage, inspect, stream and reset vehicle memory."""

from __future__ import annotations

import hashlib
import html
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionSteps
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.activation import read_step_activation
from autonomy.decision_cycle.memory.publication import OBSERVATION_KEY
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.plugins import DuplicatePluginIdError

from implementations.decision_cycle.catalog import selection_activation
from implementations.decision_cycle.memory.presets import (
    MEMORY_PRESETS,
    available_memory_preset_ids,
)

from .automation import (
    _automation_command_matches_vehicle,
    _automation_dir,
    _pid_alive,
    _process_command,
)
from .bundles import (
    controller_bundle_paths,
    release_activation_summary,
    sync_controller_bundle,
)
from .step_activations import refresh_release, stage_activation
from .memory_report import last_plugin_state, memory_summary
from .paths import ROOT, display_path, safe_path_part
from .runtime_view import RuntimeViewServer
from .physical_observation import (
    fetch_autonomy_status,
    fetch_observation_publication,
    physical_observation_dir,
    picar_base_url,
    post_memory_reset,
)
from .streaming import _publish_physical_view
from .vehicles import discover_active_vehicles, find_vehicle_by_id, format_active_vehicles
from .workbench_frames import default_mapper, run_frame
from .workbench_source import SourceValidationError, normalize_image_directory, normalize_image_file


RUNTIME_ROOT = Path(os.environ.get("AUTOMA_RUNTIME_ROOT", ROOT / "runtime" / "vehicles"))
INSPECT_ROOT = Path(
    os.environ.get("AUTOMA_MEMORY_INSPECT_ROOT", ROOT / "runtime" / "memory-inspections")
)
MEMORY_INSPECT_SCHEMA = "memory_inspect_v0"
# Chase memory probe treats workers older than this as stale.
CHASE_MEMORY_PROBE_MAX_AGE_MS = int(
    os.environ.get("AUTOMA_CHASE_MEMORY_PROBE_MAX_AGE_MS", "30000")
)
# Allow tiny forward clock skew before treating updated_at_ms as invalid.
CHASE_MEMORY_PROBE_CLOCK_SKEW_MS = int(
    os.environ.get("AUTOMA_CHASE_MEMORY_PROBE_CLOCK_SKEW_MS", "2000")
)


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


def update_vehicle_memory(
    *,
    vehicle_id: str,
    preset: str | None = None,
    plugins: list[str] | None = None,
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
    vehicle_runtime_dir = RUNTIME_ROOT / safe_path_part(vehicle_id)
    bundle = controller_bundle_paths(vehicle_runtime_dir)
    activation_path = Path(bundle["memory_runtime_dir"]) / "active.json"
    release: dict[str, Any] | None = None

    if not dry_run:
        release = sync_controller_bundle(bundle, output=stream)
        stage_activation(bundle, activation, vehicle_id=vehicle_id, release=release)

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
                    f"No active memory implementation found for {vehicle_id!r}.",
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

    Reports what each memory plugin retained after every frame, and the
    observation a plugin published in place of the frame's own. It reads the
    source only; live frames come from ``perception inspect --record``.
    """

    activation, error = _selected_memory(preset, plugins)
    if error is not None:
        return error
    path = Path(source).expanduser()
    try:
        image_source = normalize_image_file(path) if path.is_file() else normalize_image_directory(path)
    except SourceValidationError as exc:
        return CommandResult(2, f"Could not read memory inspect source: {exc}")
    try:
        mapper = default_mapper()
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
                mapper=mapper,
                memory_step=memory,
                steps=DecisionSteps(),
                shared_memory=shared_memory,
            )
        except Exception as exc:  # Plugins are third-party code; name the frame that broke.
            return CommandResult(
                2, f"Memory inspect failed at {frame.frame_id}: {type(exc).__name__}: {exc}"
            )
        result = outcome.result
        published = shared_memory.get(OBSERVATION_KEY)
        frames.append(
            {
                "frame_id": frame.frame_id,
                "frame_index": frame.frame_index,
                "timestamp_ms": frame.timestamp_ms,
                "absence_reason": frame.absence_reason,
                "plugins": [
                    {"plugin_id": item.get("plugin_id"), **memory_summary(item.get("state"))}
                    for item in (result.memory or {}).get("plugins") or []
                ],
                "observation": _observation_counts(seen.get("observation")),
                "replacement": _observation_counts(published if result.observation is published else None),
            }
        )

    run_id = _inspect_run_id(image_source.source_id)
    report = {
        "schema": MEMORY_INSPECT_SCHEMA,
        "run_id": run_id,
        "source": {
            "path": str(image_source.source_path),
            "source_id": image_source.source_id,
            "frame_count": len(frames),
        },
        "memory": {"preset": activation.metadata["preset"], "plugins": list(activation.plugins)},
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
            for frame in image_source.frames:
                if frame.image_path is not None:
                    shutil.copyfile(frame.image_path, frames_dir / f"frame_{frame.position:06d}{frame.image_path.suffix.lower()}")
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
        f"Memory: {report['memory']['preset']} ({', '.join(report['memory']['plugins'])})",
        "",
        "Frame  Plugin  Health  Records  Epoch  Observation  Replacement",
    ]
    for frame in report["frames"]:
        observation = frame["observation"]
        replacement = frame["replacement"]
        seen = f"{observation['things']}t/{observation['signals']}s" if observation else "-"
        replaced = f"{replacement['things']}t/{replacement['signals']}s" if replacement else "-"
        for item in frame["plugins"]:
            lines.append(
                f"{frame['frame_index']}  {item['plugin_id']}  {item['health']}  "
                f"{item['record_count']}  {item['epoch_id']}  {seen}  {replaced}"
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


def build_memory_origin_rows(
    *,
    final: dict[str, Any],
    frames: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pair each retained key with its source sequence frame observation."""

    frames_by_id = {str(frame.get("frame_id")): frame for frame in frames}
    records = final.get("records") if isinstance(final.get("records"), list) else []
    rows: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        origin = record.get("origin") if isinstance(record.get("origin"), dict) else {}
        frame_id = str(origin.get("frame_id") or "")
        source_frame = frames_by_id.get(frame_id)
        source_observation = (
            source_frame.get("observation")
            if isinstance(source_frame, dict) and isinstance(source_frame.get("observation"), dict)
            else None
        )
        location = record.get("location") if isinstance(record.get("location"), dict) else {}
        rows.append(
            {
                "key": record.get("record_id"),
                "kind": record.get("kind"),
                "label": record.get("label"),
                "confidence": record.get("confidence"),
                "retained_not_current": True,
                "location": location,
                "origin": {
                    "frame_id": origin.get("frame_id"),
                    "observation_id": origin.get("observation_id"),
                    "observed_id": origin.get("observed_id"),
                    "updated_at_ms": origin.get("updated_at_ms"),
                    "source_plugin_id": origin.get("source_plugin_id"),
                    "coordinate_frame": origin.get("coordinate_frame"),
                },
                "source_frame_present_in_sequence": source_frame is not None,
                "source_observation": source_observation,
                "source_frame_index": (
                    source_frame.get("frame_index") if isinstance(source_frame, dict) else None
                ),
                "source_timestamp_ms": (
                    source_frame.get("timestamp_ms") if isinstance(source_frame, dict) else None
                ),
            }
        )
    rows.sort(key=lambda item: str(item.get("key") or ""))
    return rows


def render_memory_origin_extract_html(
    *,
    vehicle_id: str,
    payload: dict[str, Any],
    frames: list[dict[str, Any]],
    origin_rows: list[dict[str, Any]],
    frame_image_paths: dict[str, str] | None = None,
) -> str:
    """Render a compact HTML extract: retained key → value → source observation."""

    final = payload.get("final") if isinstance(payload.get("final"), dict) else {}
    rows_html: list[str] = []
    for row in origin_rows:
        location = row.get("location") if isinstance(row.get("location"), dict) else {}
        origin = row.get("origin") if isinstance(row.get("origin"), dict) else {}
        source_obs = row.get("source_observation") if isinstance(row.get("source_observation"), dict) else {}
        source_block = (
            f"<pre>{html.escape(json.dumps(source_obs, indent=2, sort_keys=True, default=str))}</pre>"
            if source_obs
            else "<p class='warn'>Source observation not found in recorded sequence "
            f"for frame_id={html.escape(str(origin.get('frame_id')))}.</p>"
        )
        rows_html.append(
            "\n".join(
                [
                    "<section class='record'>",
                    f"<h3>key <code>{html.escape(str(row.get('key')))}</code></h3>",
                    "<p class='badge'>retained evidence — not current camera geometry</p>",
                    "<dl>",
                    f"<dt>kind</dt><dd>{html.escape(str(row.get('kind')))}</dd>",
                    f"<dt>label</dt><dd>{html.escape(str(row.get('label')))}</dd>",
                    f"<dt>confidence</dt><dd>{html.escape(str(row.get('confidence')))}</dd>",
                    f"<dt>location.zone</dt><dd>{html.escape(str(location.get('zone') or '—'))}</dd>",
                    f"<dt>location.frame</dt><dd>{html.escape(str(location.get('frame') or '—'))}</dd>",
                    f"<dt>origin.frame_id</dt><dd><code>{html.escape(str(origin.get('frame_id') or '—'))}</code></dd>",
                    f"<dt>origin.observation_id</dt><dd><code>{html.escape(str(origin.get('observation_id') or '—'))}</code></dd>",
                    f"<dt>source in sequence</dt><dd>{'yes' if row.get('source_frame_present_in_sequence') else 'no'}</dd>",
                    "</dl>",
                    "<h4>Mapped value (retained)</h4>",
                    f"<pre>{html.escape(json.dumps({k: row.get(k) for k in ('key','kind','label','confidence','location','origin')}, indent=2, sort_keys=True, default=str))}</pre>",
                    "<h4>Source observation at origin.frame_id</h4>",
                    source_block,
                    "</section>",
                ]
            )
        )

    timeline = []
    for item in payload.get("per_frame") or []:
        if not isinstance(item, dict):
            continue
        timeline.append(
            "<li>"
            f"<code>{html.escape(str(item.get('frame_id')))}</code> "
            f"keys={html.escape(str(item.get('record_count')))} "
            f"health={html.escape(str(item.get('health')))}"
            "</li>"
        )

    frame_images = frame_image_paths or {}
    frame_figures: list[str] = []
    for frame in frames:
        frame_id = str(frame.get("frame_id") or "").strip()
        image_path = frame_images.get(frame_id)
        if not frame_id or not image_path:
            continue
        frame_figures.append(
            "<figure>"
            f"<img src=\"{html.escape(image_path, quote=True)}\" "
            f"alt=\"Captured source frame {html.escape(frame_id, quote=True)}\">"
            f"<figcaption><code>{html.escape(frame_id)}</code></figcaption>"
            "</figure>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Memory origin extract — {html.escape(vehicle_id)}</title>
  <style>
    :root {{ font-family: ui-sans-serif, system-ui, sans-serif; color: #17191c; }}
    body {{ margin: 24px; max-width: 960px; }}
    h1 {{ font-size: 1.25rem; }}
    .meta, .badge, .warn {{ color: #62676f; }}
    .badge {{
      display: inline-block; border: 1px solid #d9dde2; border-radius: 999px;
      padding: 2px 8px; font-size: 12px; background: #f6f7f8;
    }}
    .warn {{ color: #a35b00; }}
    .record {{
      border: 1px solid #d9dde2; border-radius: 8px; padding: 12px 14px; margin: 14px 0;
      background: #fafbfc;
    }}
    dl {{ display: grid; grid-template-columns: 12rem 1fr; gap: 4px 10px; }}
    dt {{ color: #62676f; }}
    dd {{ margin: 0; word-break: break-word; }}
    pre {{
      background: #0f1418; color: #e7eef5; padding: 10px; border-radius: 6px;
      overflow: auto; font-size: 12px; line-height: 1.4;
    }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .note {{
      border-left: 3px solid #176b87; padding: 8px 12px; background: #e7f3f8; margin: 16px 0;
    }}
    .frame-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }}
    figure {{ margin: 0; }}
    figure img {{ display: block; width: 100%; height: auto; border: 1px solid #d9dde2; }}
    figcaption {{ margin-top: 5px; color: #62676f; }}
  </style>
</head>
<body>
  <h1>Memory origin extract</h1>
  <p class="meta">
    vehicle=<strong>{html.escape(vehicle_id)}</strong>
    · implementation=<code>{html.escape(str(payload.get('plugin_id')))}</code>
    · digest=<code>{html.escape(str(payload.get('digest')))}</code>
    · frames={html.escape(str(payload.get('frame_count')))}
    · final keys={html.escape(str(final.get('record_count')))}
    · health={html.escape(str(final.get('health')))}
  </p>
  <div class="note">
    Retained evidence is attributed to <code>origin.frame_id</code> / observation identity.
    Image-space locations below are <strong>not</strong> current camera geometry; they are
    frozen claims from the source observation that produced each key.
  </div>
  <h2>Replay timeline</h2>
  <ol>
    {''.join(timeline) or '<li>no per-frame rows</li>'}
  </ol>
  <h2>Captured source frames</h2>
  <div class="frame-grid">
    {''.join(frame_figures) if frame_figures else '<p class="meta">No captured frame images in this extract.</p>'}
  </div>
  <h2>Retained keys → mapped values → source observations</h2>
  {''.join(rows_html) if rows_html else '<p class="meta">No retained records in final state.</p>'}
  <h2>Sequence frames present</h2>
  <p class="meta">{html.escape(', '.join(str(frame.get('frame_id')) for frame in frames))}</p>
</body>
</html>
"""


def memory_state_digest(state: dict[str, Any]) -> str:
    """Stable digest of the final plugin's memory state (records + health, not process identity)."""

    records = state.get("records") if isinstance(state.get("records"), list) else []
    normalized_records = []
    for record in records:
        if not isinstance(record, dict):
            continue
        # Canonicalize nested structures with sorted JSON.
        normalized_records.append(json.loads(json.dumps(record, sort_keys=True, default=str)))
    normalized_records.sort(key=lambda item: str(item.get("record_id") or ""))
    body = {
        "plugin_id": state.get("plugin_id"),
        "health": state.get("health"),
        "record_count": state.get("record_count", len(normalized_records)),
        "bounds": state.get("bounds"),
        "records": normalized_records,
        "summary": state.get("summary"),
        "metadata": state.get("metadata"),
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
                    "Discovery:",
                    format_active_vehicles(discovery, include_inactive=True),
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
                return _once_memory_stream_result(
                    live=live,
                    line=line,
                    stream=stream,
                )
            time.sleep(max(0.1, float(refresh_s)))
    except KeyboardInterrupt:
        return CommandResult(130, "")


def _once_memory_stream_result(
    *,
    live: dict[str, Any],
    line: str,
    stream: TextIO | None,
) -> CommandResult:
    """One-shot stream succeeds only when live memory is confirmed.

    Non-live statuses (stopped, stale, absent, error, unavailable, …) keep their
    structured diagnostic payload/line but return nonzero so automation cannot
    treat retained or stopped state as success.
    """

    status = str(live.get("status") or "unknown")
    if status == "live":
        # Avoid double-print when the handler also emits result.message.
        return CommandResult(0, "" if stream is not None else line)
    diagnostic = str(
        live.get("error")
        or f"memory stream is not live (status={status})"
    )
    return CommandResult(2, "" if stream is not None else (line or diagnostic))


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
                return _once_memory_stream_result(
                    live=live,
                    line="",
                    stream=stream,
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

    liveness = assess_chase_memory_worker_liveness(
        state=state,
        probed_at_ms=probed_at_ms,
        max_age_ms=CHASE_MEMORY_PROBE_MAX_AGE_MS,
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
            "max_age_ms": CHASE_MEMORY_PROBE_MAX_AGE_MS,
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


def assess_chase_memory_worker_liveness(
    *,
    state: dict[str, Any],
    probed_at_ms: int,
    max_age_ms: int = CHASE_MEMORY_PROBE_MAX_AGE_MS,
    vehicle_id: str | None = None,
    clock_skew_ms: int = CHASE_MEMORY_PROBE_CLOCK_SKEW_MS,
) -> dict[str, Any]:
    """Require a running automation process and a fresh state publication.

    Stopped or stale workers must not be reported as live memory merely because
    a previous state.json still contains a memory status block. A live PID must
    also match the automation run identity for this vehicle; unavailable process
    identity fails closed (unlike stop-path permissive matching).

    ``updated_at_ms`` is the automation-wide state heartbeat refreshed by the
    capture loop. It proves worker publication freshness, not that the memory
    step itself just completed an update.
    """

    run_status = str(state.get("status") or "")
    pid = state.get("pid")
    if not isinstance(pid, int):
        pid = None
    pid_alive = _pid_alive(pid) if isinstance(pid, int) else False
    updated_at_ms = state.get("updated_at_ms")
    try:
        updated_at = int(updated_at_ms) if updated_at_ms is not None else None
    except (TypeError, ValueError):
        updated_at = None
    age_ms = (probed_at_ms - updated_at) if updated_at is not None else None

    if run_status not in {"running", "starting"}:
        return {
            "live": False,
            "status": "stopped",
            "error": (
                f"Automation worker is not running (status={run_status or 'unknown'}). "
                "Start observe-only automation before probing live memory."
            ),
            "pid": pid,
            "updated_at_ms": updated_at,
            "age_ms": age_ms,
        }
    if not pid_alive:
        return {
            "live": False,
            "status": "stale",
            "error": (
                f"Automation worker PID {pid} is not running "
                f"(state status={run_status}). Restart automation before probing live memory."
            ),
            "pid": pid,
            "updated_at_ms": updated_at,
            "age_ms": age_ms,
        }
    # Live memory requires verified automation identity — fail closed if unknown.
    if vehicle_id is None or not str(vehicle_id).strip():
        return {
            "live": False,
            "status": "stale",
            "error": (
                "vehicle_id is required to verify the automation worker identity "
                "before trusting live memory."
            ),
            "pid": pid,
            "updated_at_ms": updated_at,
            "age_ms": age_ms,
        }
    vehicle_key = str(vehicle_id).strip()
    if isinstance(pid, int):
        command = _process_command(pid)
        if command is None:
            return {
                "live": False,
                "status": "stale",
                "error": (
                    f"Could not read process command for PID {pid}; "
                    "cannot verify automation worker identity. "
                    "Treating live memory as unavailable."
                ),
                "pid": pid,
                "updated_at_ms": updated_at,
                "age_ms": age_ms,
            }
        if not _automation_command_matches_vehicle(command, vehicle_key):
            return {
                "live": False,
                "status": "stale",
                "error": (
                    f"PID {pid} is alive but is not the automation worker for "
                    f"{vehicle_key!r} (possible PID reuse). Restart automation."
                ),
                "pid": pid,
                "updated_at_ms": updated_at,
                "age_ms": age_ms,
            }
    if updated_at is None:
        return {
            "live": False,
            "status": "stale",
            "error": (
                "Automation state is missing updated_at_ms; cannot confirm a live publication."
            ),
            "pid": pid,
            "updated_at_ms": None,
            "age_ms": None,
        }
    # Future timestamps (beyond small clock skew) are not trustworthy freshness.
    if age_ms is not None and age_ms < -int(clock_skew_ms):
        return {
            "live": False,
            "status": "stale",
            "error": (
                f"Automation state updated_at_ms is in the future "
                f"(age_ms={age_ms}, skew_tolerance_ms={int(clock_skew_ms)}). "
                "Rejecting as invalid publication time."
            ),
            "pid": pid,
            "updated_at_ms": updated_at,
            "age_ms": age_ms,
        }
    if age_ms is not None and age_ms > int(max_age_ms):
        return {
            "live": False,
            "status": "stale",
            "error": (
                f"Automation state is stale (age_ms={age_ms}, max_age_ms={int(max_age_ms)}). "
                "Worker may be hung; restart automation for a fresh memory publication."
            ),
            "pid": pid,
            "updated_at_ms": updated_at,
            "age_ms": age_ms,
        }
    return {
        "live": True,
        "status": "live",
        "error": None,
        "pid": pid,
        "updated_at_ms": updated_at,
        "age_ms": max(0, age_ms) if age_ms is not None else None,
    }



def _format_memory_info(payload: dict[str, Any]) -> str:
    activation = payload["activation"]
    bounds = activation.get("bounds") if isinstance(activation.get("bounds"), dict) else {}
    plugins = ", ".join(activation.get("plugins", [])) or "none"
    lines = [f"Memory: {payload['vehicle_id']} -> {plugins}"]
    lines.extend(
        [
            f"Activation: {activation['path']}",
            (
                f"Bounds: max_records={bounds.get('max_records')} "
                f"max_age_ms={bounds.get('max_age_ms')} "
                f"eviction={bounds.get('eviction_policy')}"
            ),
            f"Enabled plugins: {', '.join(activation.get('plugins', [])) or 'none'}",
            f"Available plugins: {', '.join(activation.get('available_plugins', [])) or 'none'}",
            "Lifecycle: update / reset / status",
        ]
    )
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
