"""Automa decision stage, info, stream, and offline apply surfaces (M006-05)."""

from __future__ import annotations

import hashlib
import html
import json
import os
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TextIO

from autonomy.decision import (
    ACTION_PLAN_SCHEMA,
    ACTION_PROPOSAL_SCHEMA,
    SHADOW_AUTHORITY_RESULT_SCHEMA,
    SHADOW_DECISION_CYCLE_RESULT_SCHEMA,
    SELECTOR_ID,
    ShadowProposalsConfig,
    canonical_json_utf8,
)
from autonomy.decision.memory import (
    MEMORY_SNAPSHOT_SCHEMA,
    MemorySnapshot,
)
from autonomy.decision.observation import OBSERVATION_SCHEMA, Observation
from autonomy.decision.shadow_authority import AUTHORIZED_IDLE_REASON, authorized_idle_output
from autonomy.decision.shadow_ids import require_ascii_id, require_safe_int
from autonomy.decision.shadow_runner import (
    DEFAULT_ACCEPTED_KINDS,
    DEFAULT_ENABLED_PLUGINS,
    DEFAULT_RETAINED_MAX_AGE_MS,
    DEFAULT_STEER_MAGNITUDE,
    ENGINE_ID,
)
from autonomy.runtime import AutonomyManager, read_decision_activation
from implementations.decision.catalog import (
    KNOWN_PROPOSAL_PLUGIN_IDS,
    create_shadow_proposals_engine,
)
from implementations.decision.shadow_adapter import ADAPTER_ENGINE_SPEC

from .bundles import (
    controller_bundle_paths,
    release_activation_summary,
    sync_controller_bundle,
)
from .paths import ROOT, display_path, safe_path_part


RUNTIME_ROOT = Path(os.environ.get("AUTOMA_RUNTIME_ROOT", ROOT / "runtime" / "vehicles"))

DECISION_STREAM_MAX_AGE_MS = int(
    os.environ.get("AUTOMA_DECISION_STREAM_MAX_AGE_MS", "30000")
)
DECISION_APPLY_MAX_FRAMES = int(os.environ.get("AUTOMA_DECISION_APPLY_MAX_FRAMES", "256"))
DECISION_APPLY_MAX_SEQUENCE_FILE_BYTES = int(
    os.environ.get("AUTOMA_DECISION_APPLY_MAX_SEQUENCE_FILE_BYTES", str(32 * 1024 * 1024))
)
DECISION_APPLY_MAX_RECORD_BYTES = int(
    os.environ.get("AUTOMA_DECISION_APPLY_MAX_RECORD_BYTES", str(8 * 1024 * 1024))
)

APPLY_SEQUENCE_SCHEMA = "automa_decision_apply_sequence_v0"
STREAM_FRAME_SCHEMA = "vehicle_decision_stream_frame_v0"
APPLY_RESULT_SCHEMA = "vehicle_decision_apply_result_v0"
APPLY_DIGEST_SCHEMA = "vehicle_decision_apply_digest_v0"
ERROR_SCHEMA = "vehicle_decision_error_v0"
EXACT_FRAME_REVIEW_SCHEMA = "decision_exact_frame_review_v0"
COMBINED_VIEW_ID = "decision-combined-v0"
LATEST_DECISION_FILENAME = "latest_decision.json"

OBSERVATION_REQUIRED_KEYS = frozenset(
    {
        "schema",
        "observation_id",
        "created_at_ms",
        "sensor_snapshot",
        "perception_schema",
        "perception_plugin_id",
        "summary",
        "things",
        "signals",
        "artifacts",
        "metadata",
    }
)
MEMORY_REQUIRED_KEYS = frozenset(
    {
        "schema",
        "memory_id",
        "epoch_id",
        "health",
        "bounds",
        "created_at_ms",
        "record_count",
        "records",
        "summary",
        "implementation_id",
        "error",
        "metadata",
    }
)
BOUNDS_REQUIRED_KEYS = frozenset(
    {
        "max_records",
        "max_age_ms",
        "eviction_policy",
        "max_property_bytes",
        "max_serialized_bytes",
    }
)
RECORD_REQUIRED_KEYS = frozenset(
    {
        "record_id",
        "kind",
        "label",
        "confidence",
        "provenance",
        "location",
        "properties",
    }
)
PROVENANCE_REQUIRED_KEYS = frozenset(
    {
        "observation_id",
        "evidence_id",
        "coordinate_frame",
        "observed_at_ms",
        "updated_at_ms",
        "source_plugin_id",
        "frame_id",
    }
)
LOCATION_REQUIRED_KEYS = frozenset(
    {"frame", "zone", "bbox_xyxy_norm", "polygon_xy_norm"}
)

SHADOW_DECISION_INPUTS = (
    "observation",
    "memory",
    "patterns",
    "projections",
    "capabilities",
    "prior_host_applied_command",
)

DEFAULT_SHADOW_ENGINE_CONFIG: dict[str, Any] = {
    "enabled_plugins": list(DEFAULT_ENABLED_PLUGINS),
    "accepted_kinds": list(DEFAULT_ACCEPTED_KINDS),
    "retained_max_age_ms": DEFAULT_RETAINED_MAX_AGE_MS,
    "steer_magnitude": DEFAULT_STEER_MAGNITUDE,
}

DECISION_ENGINES: dict[str, dict[str, Any]] = {
    "idle": {
        "description": "Safe default engine that always holds position.",
        "engine_spec": "autonomy.runtime.engine:IdleAutonomyEngine",
        "engine_config": {},
    },
    ENGINE_ID: {
        "description": (
            "Shadow-only proposals engine (PR #74). Authorized output is idle; "
            f"proposed_applied=false; reason={AUTHORIZED_IDLE_REASON}."
        ),
        "engine_spec": ADAPTER_ENGINE_SPEC,
        "engine_config": dict(DEFAULT_SHADOW_ENGINE_CONFIG),
    },
}


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


class DecisionSurfaceError(Exception):
    """Operator/config/input error mapped to exit 2 and stable error codes."""

    def __init__(
        self,
        error: str,
        message: str,
        *,
        vehicle_id: str | None = None,
        details: dict[str, Any] | None = None,
        exit_code: int = 2,
    ) -> None:
        super().__init__(message)
        self.error = error
        self.message_text = message
        self.vehicle_id = vehicle_id
        self.details = details or {}
        self.exit_code = exit_code


def available_decision_engine_ids() -> tuple[str, ...]:
    return tuple(sorted(DECISION_ENGINES))


def decision_apply_output_root() -> Path:
    return Path(
        os.environ.get(
            "AUTOMA_DECISION_APPLY_OUTPUT_ROOT",
            str(ROOT / "lab" / "runs" / "decision-apply"),
        )
    )


def latest_decision_path(vehicle_runtime_dir: Path | str) -> Path:
    """Path of generation-scoped latest decision frame beside automation state."""

    bundle = controller_bundle_paths(Path(vehicle_runtime_dir))
    return Path(bundle["runtime_dir"]) / "automation" / LATEST_DECISION_FILENAME


def decision_error_payload(
    *,
    error: str,
    message: str,
    exit_code: int = 2,
    vehicle_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": ERROR_SCHEMA,
        "exit_code": exit_code,
        "error": error,
        "message": message,
        "vehicle_id": vehicle_id,
        "details": details or {},
    }


def _error_result(
    exc: DecisionSurfaceError,
    *,
    json_output: bool,
) -> CommandResult:
    if json_output:
        return CommandResult(
            exc.exit_code,
            json.dumps(
                decision_error_payload(
                    error=exc.error,
                    message=exc.message_text,
                    exit_code=exc.exit_code,
                    vehicle_id=exc.vehicle_id,
                    details=exc.details,
                ),
                indent=2,
                sort_keys=True,
            ),
        )
    return CommandResult(exc.exit_code, exc.message_text)


def validate_shadow_engine_config(engine_config: dict[str, Any]) -> ShadowProposalsConfig:
    """Fail closed before activation write when shadow config is invalid."""

    if not isinstance(engine_config, dict):
        raise DecisionSurfaceError(
            "invalid_engine_config",
            "shadow-proposals engine_config must be a JSON object.",
        )
    allowed = {
        "enabled_plugins",
        "accepted_kinds",
        "retained_max_age_ms",
        "steer_magnitude",
    }
    unknown = set(engine_config) - allowed
    if unknown:
        raise DecisionSurfaceError(
            "invalid_engine_config",
            f"shadow-proposals engine_config has unknown keys: {sorted(unknown)}.",
        )
    try:
        cfg = ShadowProposalsConfig(**engine_config) if engine_config else ShadowProposalsConfig()
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "invalid_engine_config",
            f"Invalid shadow-proposals engine_config: {exc}",
        ) from exc
    for plugin_id in cfg.enabled_plugins:
        if plugin_id not in KNOWN_PROPOSAL_PLUGIN_IDS:
            raise DecisionSurfaceError(
                "invalid_engine_config",
                f"Unknown proposal plugin {plugin_id!r}. "
                f"Known: {', '.join(sorted(KNOWN_PROPOSAL_PLUGIN_IDS))}.",
            )
    return cfg


def update_vehicle_decision(
    *,
    vehicle_id: str,
    engine_id: str = "idle",
    dry_run: bool = False,
    json_output: bool = False,
    verbose: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    if engine_id not in DECISION_ENGINES:
        available = ", ".join(available_decision_engine_ids())
        exc = DecisionSurfaceError(
            "unknown_engine",
            f"Unknown decision engine {engine_id!r}. Available engines: {available}.",
            vehicle_id=vehicle_id,
            details={"available_engines": list(available_decision_engine_ids())},
        )
        return _error_result(exc, json_output=json_output)

    engine_entry = DECISION_ENGINES[engine_id]
    engine_config = dict(engine_entry["engine_config"])
    if engine_id == ENGINE_ID:
        try:
            validate_shadow_engine_config(engine_config)
        except DecisionSurfaceError as exc:
            return _error_result(
                DecisionSurfaceError(
                    exc.error,
                    exc.message_text,
                    vehicle_id=vehicle_id,
                    details=exc.details,
                ),
                json_output=json_output,
            )

    stream = output if verbose else None
    vehicle_runtime_dir = RUNTIME_ROOT / safe_path_part(vehicle_id)
    bundle = controller_bundle_paths(vehicle_runtime_dir)
    activation_path = Path(bundle["decision_runtime_dir"]) / "active.json"
    manager = AutonomyManager(
        default_engine_spec=engine_entry["engine_spec"],
        default_engine_config=dict(engine_config),
    )
    release: dict[str, Any] | None = None

    if not dry_run:
        release = sync_controller_bundle(bundle, output=stream)

    activation = _decision_activation(
        vehicle_id=vehicle_id,
        engine_id=engine_id,
        bundle=bundle,
        release=release,
        manager=manager,
        engine_config=engine_config,
        engine_description=engine_entry["description"],
        engine_spec=engine_entry["engine_spec"],
    )

    if not dry_run:
        activation_path.parent.mkdir(parents=True, exist_ok=True)
        activation_path.write_text(
            json.dumps(activation, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        # Restage invalidates any prior latest decision frame.
        invalidate_latest_decision_frame(vehicle_runtime_dir)

    payload = {
        "schema": "vehicle_decision_update_v0",
        "vehicle_id": vehicle_id,
        "engine_id": engine_id,
        "dry_run": dry_run,
        "activation": display_path(activation_path),
        "manifest": activation,
        "release": release_activation_summary(release) if release is not None else None,
    }
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    verb = "Would activate" if dry_run else "Updated decision"
    return CommandResult(
        0,
        "\n".join(
            [
                f"{verb}: {vehicle_id} -> {engine_id}",
                f"Engine: {engine_entry['engine_spec']}",
                f"Activation: {display_path(activation_path)}",
            ]
        ),
    )


def ensure_vehicle_decision_activation(
    *,
    vehicle_id: str,
    bundle: dict[str, str],
    release: dict[str, Any],
) -> Path:
    activation_path = Path(bundle["decision_runtime_dir"]) / "active.json"
    if activation_path.exists():
        activation = read_decision_activation(activation_path).payload
        controller_bundle = activation.get("controller_bundle")
        if not isinstance(controller_bundle, dict):
            raise ValueError(f"decision activation has no controller_bundle: {activation_path}")
        controller_bundle["release"] = release_activation_summary(release)
        activation_path.write_text(json.dumps(activation, indent=2, sort_keys=True), encoding="utf-8")
        return activation_path

    engine_id = "idle"
    engine_entry = DECISION_ENGINES[engine_id]
    manager = AutonomyManager(
        default_engine_spec=engine_entry["engine_spec"],
        default_engine_config=dict(engine_entry["engine_config"]),
    )
    activation = _decision_activation(
        vehicle_id=vehicle_id,
        engine_id=engine_id,
        bundle=bundle,
        release=release,
        manager=manager,
        engine_config=dict(engine_entry["engine_config"]),
        engine_description=engine_entry["description"],
        engine_spec=engine_entry["engine_spec"],
    )
    activation_path.parent.mkdir(parents=True, exist_ok=True)
    activation_path.write_text(json.dumps(activation, indent=2, sort_keys=True), encoding="utf-8")
    return activation_path


def get_vehicle_decision_info(*, vehicle_id: str, json_output: bool = False) -> CommandResult:
    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))
    activation_path = Path(bundle["decision_runtime_dir"]) / "active.json"
    if not activation_path.exists():
        exc = DecisionSurfaceError(
            "activation_missing",
            "\n".join(
                [
                    f"No active decision engine found for {vehicle_id!r}.",
                    f"Expected activation: {display_path(activation_path)}",
                    "Run: ./cli/automa vehicles update decision --id <vehicle_id>",
                ]
            ),
            vehicle_id=vehicle_id,
        )
        return _error_result(exc, json_output=json_output)

    try:
        activation = json.loads(activation_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        err = DecisionSurfaceError(
            "activation_invalid",
            f"Could not parse decision activation {display_path(activation_path)}: {exc}",
            vehicle_id=vehicle_id,
        )
        return _error_result(err, json_output=json_output)

    decision = activation.get("decision")
    if not isinstance(decision, dict):
        err = DecisionSurfaceError(
            "activation_invalid",
            f"Activation {display_path(activation_path)} has no decision section.",
            vehicle_id=vehicle_id,
        )
        return _error_result(err, json_output=json_output)

    engine_id = decision.get("engine_id")
    engine_config = decision.get("engine_config") if isinstance(decision.get("engine_config"), dict) else {}
    shadow: dict[str, Any] | None = None
    if engine_id == ENGINE_ID:
        plugins = engine_config.get("enabled_plugins")
        if not isinstance(plugins, list):
            plugins = list(DEFAULT_ENABLED_PLUGINS)
        shadow = {
            "decision_inputs": list(SHADOW_DECISION_INPUTS),
            "enabled_plugins": list(plugins),
            "selector_id": SELECTOR_ID,
            "output_schemas": {
                "action_proposal": ACTION_PROPOSAL_SCHEMA,
                "action_plan": ACTION_PLAN_SCHEMA,
                "shadow_authority": SHADOW_AUTHORITY_RESULT_SCHEMA,
                "cycle_result": SHADOW_DECISION_CYCLE_RESULT_SCHEMA,
            },
            "authority": {
                "proposed_applied": False,
                "authorized_idle_reason": AUTHORIZED_IDLE_REASON,
                "authority_mode": "shadow_only",
            },
        }

    combined_view = {
        "view_id": COMBINED_VIEW_ID,
        "url": None,
        "path_template": f"cli/automa_cli/decision_view.html#{COMBINED_VIEW_ID}",
    }

    payload = {
        "schema": "vehicle_decision_info_v0",
        "vehicle_id": vehicle_id,
        "activation": {
            "path": display_path(activation_path),
            "engine_id": engine_id,
            "engine_spec": decision.get("engine_spec"),
            "engine_config": decision.get("engine_config"),
        },
        "shadow": shadow,
        "engine_schema_source": {
            "kind": "engine_method",
            "method": "describe_schema",
            "engine_spec": decision.get("engine_spec"),
        },
        "engine_schema": decision.get("engine_schema"),
        "controller_bundle": activation.get("controller_bundle"),
        "combined_view": combined_view,
    }
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(0, _format_decision_info(payload))


def load_decision_activation(bundle: dict[str, str]) -> dict[str, Any]:
    activation_path = Path(bundle["decision_runtime_dir"]) / "active.json"
    try:
        return read_decision_activation(activation_path).payload
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"{exc}; run `automa vehicles update decision --id <vehicle_id>`"
        ) from exc


# ---------------------------------------------------------------------------
# Stream frame build / accept / publish
# ---------------------------------------------------------------------------


def build_decision_stream_frame(
    cycle_result: Any,
    *,
    vehicle_id: str,
    run_id: str,
    worker_pid: int,
    activation_engine_id: str,
    activation_activated_at_ms: int,
    published_at_ms: int | None = None,
    engine_id: str = ENGINE_ID,
) -> dict[str, Any]:
    """Build one ``vehicle_decision_stream_frame_v0`` from a cycle result."""

    if hasattr(cycle_result, "to_dict"):
        cycle_dict = cycle_result.to_dict()
    elif isinstance(cycle_result, dict):
        cycle_dict = cycle_result
    else:
        raise TypeError("cycle_result must provide to_dict() or be a dict")

    frame_id = cycle_dict.get("frame_id")
    plan = cycle_dict.get("plan") if isinstance(cycle_dict.get("plan"), dict) else None
    authority = (
        cycle_dict.get("authority") if isinstance(cycle_dict.get("authority"), dict) else {}
    )
    source = cycle_dict.get("source") if isinstance(cycle_dict.get("source"), dict) else None

    return {
        "schema": STREAM_FRAME_SCHEMA,
        "vehicle_id": vehicle_id,
        "engine_id": engine_id,
        "run_id": run_id,
        "worker_pid": int(worker_pid),
        "activation_engine_id": activation_engine_id,
        "activation_activated_at_ms": int(activation_activated_at_ms),
        "published_at_ms": int(
            published_at_ms if published_at_ms is not None else int(time.time() * 1000)
        ),
        "frame_id": frame_id,
        "frame_index": _cycle_frame_index(source, cycle_dict),
        "timestamp_ms": _cycle_timestamp_ms(source, cycle_dict),
        "cycle": cycle_dict,
        "observation_summary": _observation_summary(source),
        "memory_summary": _memory_summary(source),
        "plan_summary": _plan_summary(plan),
        "authority_summary": _authority_summary(authority, cycle_dict),
        "view": {
            "view_id": COMBINED_VIEW_ID,
            "applied_false_emphasized": True,
        },
    }


def _cycle_frame_index(source: dict[str, Any] | None, cycle: dict[str, Any]) -> int:
    if source is not None and type(source.get("frame_index")) is int:
        return int(source["frame_index"])
    if type(cycle.get("frame_index")) is int:
        return int(cycle["frame_index"])
    return 0


def _cycle_timestamp_ms(source: dict[str, Any] | None, cycle: dict[str, Any]) -> int:
    if source is not None and type(source.get("timestamp_ms")) is int:
        return int(source["timestamp_ms"])
    if type(cycle.get("timestamp_ms")) is int:
        return int(cycle["timestamp_ms"])
    return 0


def _observation_summary(source: dict[str, Any] | None) -> dict[str, Any]:
    if source is None:
        return {"status": "absent", "frame_id": None, "reason": "no_decision_data_source"}
    obs = source.get("observation")
    if not isinstance(obs, dict):
        return {"status": "absent", "frame_id": None, "reason": "observation_envelope_missing"}
    status = obs.get("status")
    if status == "ready":
        value = obs.get("value") if isinstance(obs.get("value"), dict) else {}
        return {
            "status": "ready",
            "frame_id": value.get("observation_id") or source.get("frame_id"),
            "reason": "",
        }
    if status in {"unavailable", "error", "absent"}:
        return {
            "status": str(status),
            "frame_id": None,
            "reason": str(obs.get("reason") or status),
        }
    return {
        "status": str(status or "absent"),
        "frame_id": None,
        "reason": str(obs.get("reason") or "unknown"),
    }


def _memory_summary(source: dict[str, Any] | None) -> dict[str, Any]:
    if source is None:
        return {
            "status": "absent",
            "health": None,
            "record_count": None,
            "records": [],
        }
    mem_env = source.get("memory")
    if not isinstance(mem_env, dict):
        return {
            "status": "absent",
            "health": None,
            "record_count": None,
            "records": [],
        }
    status = mem_env.get("status")
    value = mem_env.get("value") if isinstance(mem_env.get("value"), dict) else None
    if status == "ready" and value is not None:
        records_out: list[dict[str, Any]] = []
        raw_records = value.get("records") if isinstance(value.get("records"), list) else []
        for item in raw_records[:12]:
            if not isinstance(item, dict):
                continue
            prov = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
            records_out.append(
                {
                    "record_id": item.get("record_id"),
                    "kind": item.get("kind"),
                    "confidence": item.get("confidence"),
                    "frame_id": prov.get("frame_id"),
                    "observation_id": prov.get("observation_id"),
                }
            )
        return {
            "status": "ready",
            "health": value.get("health"),
            "record_count": value.get("record_count"),
            "records": records_out,
        }
    return {
        "status": str(status or "absent"),
        "health": value.get("health") if isinstance(value, dict) else None,
        "record_count": value.get("record_count") if isinstance(value, dict) else None,
        "records": [],
    }


def _plan_summary(plan: dict[str, Any] | None) -> dict[str, Any]:
    if plan is None:
        return {
            "status": None,
            "selected_proposal_id": None,
            "candidates": [],
            "contributions": [],
        }
    candidates_out: list[dict[str, Any]] = []
    raw_candidates = plan.get("candidates") if isinstance(plan.get("candidates"), list) else []
    for cand in raw_candidates:
        if not isinstance(cand, dict):
            continue
        command = cand.get("command")
        if isinstance(command, dict):
            command_out = {
                "steering": command.get("steering"),
                "throttle": command.get("throttle"),
            }
        else:
            command_out = None
        source_refs = cand.get("source_refs")
        if not isinstance(source_refs, list):
            source_refs = []
        candidates_out.append(
            {
                "proposal_id": cand.get("proposal_id"),
                "plugin_id": cand.get("plugin_id"),
                "lifecycle": cand.get("lifecycle"),
                "freshness": cand.get("freshness"),
                "confidence": cand.get("confidence"),
                "reason": cand.get("reason"),
                "command": command_out,
                "source_refs": source_refs,
            }
        )
    contributions = plan.get("contributions")
    if not isinstance(contributions, list):
        contributions = []
    return {
        "status": plan.get("status"),
        "selected_proposal_id": plan.get("selected_proposal_id"),
        "candidates": candidates_out,
        "contributions": contributions,
    }


def _authority_summary(
    authority: dict[str, Any],
    cycle: dict[str, Any],
) -> dict[str, Any]:
    proposed = authority.get("proposed")
    if isinstance(proposed, dict):
        proposed_out: dict[str, Any] | None = dict(proposed)
    else:
        proposed_out = None
    authorized = authority.get("authorized_output")
    if not isinstance(authorized, dict):
        authorized = authorized_idle_output()
    host_application = authority.get("host_application")
    if not isinstance(host_application, dict):
        host_application = {
            "status": "unavailable",
            "reason": "host_did_not_report_application",
            "value": None,
            "updated_at_ms": None,
        }
    return {
        "proposed": proposed_out,
        "authorized_output": authorized,
        "proposed_applied": False,
        "host_application": host_application,
        "proposed_equals_authorized": bool(authority.get("proposed_equals_authorized")),
        "cycle_status": authority.get("cycle_status") or cycle.get("status") or "ok",
        "cycle_reason": authority.get("cycle_reason")
        if authority.get("cycle_reason") is not None
        else (cycle.get("reason") or ""),
    }


def accept_decision_stream_frame(
    frame: object,
    *,
    activation: dict[str, Any] | None,
    automation_state: dict[str, Any] | None,
    now_ms: int,
    is_pid_alive: Callable[[int], bool],
    max_age_ms: int | None = None,
) -> None:
    """Production stream acceptance predicate. Raises DecisionSurfaceError."""

    ceiling = DECISION_STREAM_MAX_AGE_MS if max_age_ms is None else int(max_age_ms)
    if type(ceiling) is not int or ceiling <= 0:
        raise DecisionSurfaceError(
            "latest_frame_stale",
            "Decision stream max age is not a positive int.",
        )

    if not isinstance(frame, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "Latest decision frame is not a JSON object.",
        )
    if frame.get("schema") != STREAM_FRAME_SCHEMA:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"Latest decision frame schema must be {STREAM_FRAME_SCHEMA!r}.",
        )

    if not isinstance(activation, dict):
        raise DecisionSurfaceError(
            "activation_missing",
            "No active decision engine found for stream decision.",
        )
    decision = activation.get("decision")
    if not isinstance(decision, dict):
        raise DecisionSurfaceError(
            "activation_invalid",
            "Decision activation has no decision section.",
        )
    engine_id = decision.get("engine_id")
    if engine_id != ENGINE_ID:
        raise DecisionSurfaceError(
            "wrong_engine",
            f"stream decision requires engine_id={ENGINE_ID!r}; got {engine_id!r}. "
            "Run: ./cli/automa vehicles update decision --id <vehicle> --engine shadow-proposals",
        )

    activated_at = activation.get("activated_at_ms")
    if type(activated_at) is not int:
        raise DecisionSurfaceError(
            "activation_invalid",
            "Decision activation activated_at_ms must be a non-bool int.",
        )
    if (
        frame.get("activation_activated_at_ms") != activated_at
        or frame.get("activation_engine_id") != ENGINE_ID
        or frame.get("engine_id") != ENGINE_ID
    ):
        raise DecisionSurfaceError(
            "latest_frame_stale",
            "Latest decision frame does not match the current shadow-proposals activation generation.",
        )

    if not isinstance(automation_state, dict):
        raise DecisionSurfaceError(
            "latest_frame_stale",
            "Automation state is missing; cannot accept a decision stream frame.",
        )
    if automation_state.get("run_id") != frame.get("run_id"):
        raise DecisionSurfaceError(
            "latest_frame_stale",
            "Latest decision frame run_id does not match the live automation worker.",
        )
    if automation_state.get("status") != "running":
        raise DecisionSurfaceError(
            "latest_frame_stale",
            f"Automation worker status is {automation_state.get('status')!r}; "
            "stream decision requires status='running'.",
        )
    state_pid = automation_state.get("pid")
    if type(state_pid) is not int or state_pid != frame.get("worker_pid"):
        raise DecisionSurfaceError(
            "latest_frame_stale",
            "Latest decision frame worker_pid does not match automation state pid.",
        )
    if not is_pid_alive(int(state_pid)):
        raise DecisionSurfaceError(
            "latest_frame_stale",
            f"Automation worker pid {state_pid} is not alive.",
        )

    published_at = frame.get("published_at_ms")
    if type(now_ms) is not int or type(published_at) is not int:
        raise DecisionSurfaceError(
            "latest_frame_stale",
            "published_at_ms and now_ms must be non-bool ints for freshness.",
        )
    age = now_ms - published_at
    if not (0 <= age <= ceiling):
        raise DecisionSurfaceError(
            "latest_frame_stale",
            f"Latest decision frame age {age} ms is outside 0..{ceiling} ms.",
            details={"age_ms": age, "max_age_ms": ceiling},
        )

    cycle = frame.get("cycle")
    if not isinstance(cycle, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "Latest decision frame is missing cycle object.",
        )
    if frame.get("frame_id") != cycle.get("frame_id"):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "frame_id must equal cycle.frame_id.",
        )


def is_pid_alive(pid: int) -> bool:
    """Production process liveness check (os.kill(pid, 0))."""

    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we cannot signal it.
        return True
    except OSError:
        return False
    return True


def write_latest_decision_frame(path: Path, frame: dict[str, Any]) -> None:
    """Atomic write of latest_decision.json (temp + fsync + replace)."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    payload = json.dumps(frame, indent=2, sort_keys=True)
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def invalidate_latest_decision_frame(vehicle_runtime_dir: Path | str) -> None:
    """Remove or replace latest_decision.json so it cannot satisfy stream."""

    path = latest_decision_path(vehicle_runtime_dir)
    try:
        if path.exists():
            path.unlink()
    except OSError:
        # Best-effort: write a non-schema placeholder that fails acceptance.
        try:
            write_latest_decision_frame(
                path,
                {"schema": "invalid_stale_decision_placeholder_v0", "stale": True},
            )
        except OSError:
            pass


def publish_shadow_decision_frame(
    *,
    cycle_result: Any | None,
    context_frame_id: str,
    vehicle_id: str,
    vehicle_runtime_dir: Path | str,
    run_id: str,
    worker_pid: int,
    activation: dict[str, Any],
    staged_engine_id: str,
) -> bool:
    """Publish generation-scoped latest frame. Returns True when written."""

    if staged_engine_id != ENGINE_ID:
        return False
    if cycle_result is None:
        return False
    frame_id = getattr(cycle_result, "frame_id", None)
    if frame_id is None and isinstance(cycle_result, dict):
        frame_id = cycle_result.get("frame_id")
    if frame_id != context_frame_id:
        return False
    decision = activation.get("decision") if isinstance(activation, dict) else None
    if not isinstance(decision, dict) or decision.get("engine_id") != ENGINE_ID:
        return False
    activated_at = activation.get("activated_at_ms")
    if type(activated_at) is not int:
        return False
    frame = build_decision_stream_frame(
        cycle_result,
        vehicle_id=vehicle_id,
        run_id=run_id,
        worker_pid=worker_pid,
        activation_engine_id=ENGINE_ID,
        activation_activated_at_ms=activated_at,
    )
    write_latest_decision_frame(latest_decision_path(vehicle_runtime_dir), frame)
    return True


def stream_vehicle_decision(
    *,
    vehicle_id: str,
    refresh_s: float = 0.5,
    once: bool = False,
    no_clear: bool = False,
    json_output: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    """Read and accept latest_decision.json under the production predicate."""

    vehicle_runtime_dir = RUNTIME_ROOT / safe_path_part(vehicle_id)
    bundle = controller_bundle_paths(vehicle_runtime_dir)
    activation_path = Path(bundle["decision_runtime_dir"]) / "active.json"
    state_path = Path(bundle["runtime_dir"]) / "automation" / "state.json"
    frame_path = latest_decision_path(vehicle_runtime_dir)

    def _load_activation() -> dict[str, Any]:
        if not activation_path.exists():
            raise DecisionSurfaceError(
                "activation_missing",
                "\n".join(
                    [
                        f"No active decision engine found for {vehicle_id!r}.",
                        f"Expected activation: {display_path(activation_path)}",
                        "Run: ./cli/automa vehicles update decision --id <vehicle_id> "
                        "--engine shadow-proposals",
                    ]
                ),
                vehicle_id=vehicle_id,
            )
        try:
            payload = json.loads(activation_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DecisionSurfaceError(
                "activation_invalid",
                f"Could not parse decision activation: {exc}",
                vehicle_id=vehicle_id,
            ) from exc
        if not isinstance(payload, dict):
            raise DecisionSurfaceError(
                "activation_invalid",
                "Decision activation is not a JSON object.",
                vehicle_id=vehicle_id,
            )
        return payload

    def _load_state() -> dict[str, Any] | None:
        if not state_path.exists():
            return None
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _load_frame() -> dict[str, Any]:
        if not frame_path.exists():
            raise DecisionSurfaceError(
                "latest_frame_missing",
                f"No latest decision frame at {display_path(frame_path)}.",
                vehicle_id=vehicle_id,
            )
        try:
            payload = json.loads(frame_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                f"Could not parse latest decision frame: {exc}",
                vehicle_id=vehicle_id,
            ) from exc
        if not isinstance(payload, dict):
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                "Latest decision frame is not a JSON object.",
                vehicle_id=vehicle_id,
            )
        return payload

    def _accept_once() -> dict[str, Any]:
        # Activation/engine gates before frame IO so wrong_engine surfaces cleanly.
        activation = _load_activation()
        decision = activation.get("decision")
        if not isinstance(decision, dict):
            raise DecisionSurfaceError(
                "activation_invalid",
                "Decision activation has no decision section.",
                vehicle_id=vehicle_id,
            )
        if decision.get("engine_id") != ENGINE_ID:
            raise DecisionSurfaceError(
                "wrong_engine",
                f"stream decision requires engine_id={ENGINE_ID!r}; "
                f"got {decision.get('engine_id')!r}. "
                "Run: ./cli/automa vehicles update decision --id <vehicle> "
                "--engine shadow-proposals",
                vehicle_id=vehicle_id,
            )
        frame = _load_frame()
        state = _load_state()
        accept_decision_stream_frame(
            frame,
            activation=activation,
            automation_state=state,
            now_ms=int(time.time() * 1000),
            is_pid_alive=is_pid_alive,
        )
        return frame

    if once:
        try:
            frame = _accept_once()
        except DecisionSurfaceError as exc:
            return _error_result(exc, json_output=json_output)
        if json_output:
            return CommandResult(0, json.dumps(frame, indent=2, sort_keys=True))
        return CommandResult(0, _format_stream_frame(frame))

    stream = output
    last_error: str | None = None
    try:
        while True:
            try:
                frame = _accept_once()
                last_error = None
                text = (
                    json.dumps(frame, indent=2, sort_keys=True)
                    if json_output
                    else _format_stream_frame(frame)
                )
                if stream is not None:
                    if not no_clear and not json_output:
                        stream.write("\033[2J\033[H")
                    stream.write(text + "\n")
                    stream.flush()
            except DecisionSurfaceError as exc:
                last_error = exc.message_text
                if stream is not None and not json_output:
                    if not no_clear:
                        stream.write("\033[2J\033[H")
                    stream.write(last_error + "\n")
                    stream.flush()
                elif stream is not None and json_output:
                    stream.write(
                        json.dumps(
                            decision_error_payload(
                                error=exc.error,
                                message=exc.message_text,
                                vehicle_id=vehicle_id,
                                details=exc.details,
                            ),
                            indent=2,
                            sort_keys=True,
                        )
                        + "\n"
                    )
                    stream.flush()
            time.sleep(max(0.05, float(refresh_s)))
    except KeyboardInterrupt:
        return CommandResult(130, last_error or "")


def _format_stream_frame(frame: dict[str, Any]) -> str:
    plan = frame.get("plan_summary") if isinstance(frame.get("plan_summary"), dict) else {}
    authority = (
        frame.get("authority_summary")
        if isinstance(frame.get("authority_summary"), dict)
        else {}
    )
    obs = (
        frame.get("observation_summary")
        if isinstance(frame.get("observation_summary"), dict)
        else {}
    )
    mem = frame.get("memory_summary") if isinstance(frame.get("memory_summary"), dict) else {}
    proposed = authority.get("proposed")
    lines = [
        f"Decision stream: {frame.get('vehicle_id')} frame={frame.get('frame_id')}",
        f"Engine: {frame.get('engine_id')}  run={frame.get('run_id')}  pid={frame.get('worker_pid')}",
        f"Observation: {obs.get('status')}  memory: {mem.get('status')} "
        f"records={mem.get('record_count')}",
        f"Plan: status={plan.get('status')} selected={plan.get('selected_proposal_id')}",
    ]
    selected_refs: list[Any] = []
    for cand in plan.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        if cand.get("proposal_id") == plan.get("selected_proposal_id"):
            selected_refs = cand.get("source_refs") or []
            break
    lines.append(f"Selected source_refs: {json.dumps(selected_refs, sort_keys=True)}")
    lines.append(
        f"Authority: proposed={proposed} authorized={authority.get('authorized_output')} "
        f"proposed_applied=false"
    )
    lines.append(
        "Non-claims: no object identity; shadow-only; not navigation certification."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Apply / replay
# ---------------------------------------------------------------------------


def strict_decode_apply_observation(payload: object) -> Observation:
    """Only legal way apply turns JSON into Observation (complete export equality)."""

    if not isinstance(payload, dict):
        raise DecisionSurfaceError(
            "run_invalid",
            "Apply observation must be a JSON object (full to_dict export).",
        )
    if set(payload.keys()) != OBSERVATION_REQUIRED_KEYS:
        raise DecisionSurfaceError(
            "run_invalid",
            "Apply observation key set must exactly match Observation.to_dict() export.",
            details={
                "expected": sorted(OBSERVATION_REQUIRED_KEYS),
                "got": sorted(payload.keys()),
            },
        )
    things = payload.get("things")
    if not isinstance(things, (list, tuple)):
        raise DecisionSurfaceError("run_invalid", "observation.things must be a list.")
    for item in things:
        if not isinstance(item, dict):
            raise DecisionSurfaceError(
                "run_invalid",
                "observation.things entries must be objects (lossless decode).",
            )
    signals = payload.get("signals")
    if not isinstance(signals, (list, tuple)):
        raise DecisionSurfaceError("run_invalid", "observation.signals must be a list.")
    for item in signals:
        if not isinstance(item, dict):
            raise DecisionSurfaceError(
                "run_invalid",
                "observation.signals entries must be objects.",
            )
    summary = payload.get("summary")
    if not isinstance(summary, (list, tuple, str)):
        raise DecisionSurfaceError("run_invalid", "observation.summary must be a list or string.")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        raise DecisionSurfaceError("run_invalid", "observation.artifacts must be an object.")
    for key, value in artifacts.items():
        if type(key) is not str or type(value) is not str:
            raise DecisionSurfaceError(
                "run_invalid",
                "observation.artifacts values must be strings.",
            )
    try:
        constructed = Observation.from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "run_invalid",
            f"observation failed construction: {exc}",
        ) from exc
    _require_full_export_equality(payload, constructed.to_dict(), label="observation")
    return constructed


def strict_decode_apply_memory(payload: object) -> MemorySnapshot:
    """Only legal way apply turns JSON into MemorySnapshot (complete export equality)."""

    if not isinstance(payload, dict):
        raise DecisionSurfaceError(
            "run_invalid",
            "Apply memory must be a JSON object (full to_dict export).",
        )
    if set(payload.keys()) != MEMORY_REQUIRED_KEYS:
        raise DecisionSurfaceError(
            "run_invalid",
            "Apply memory key set must exactly match MemorySnapshot.to_dict() export.",
            details={
                "expected": sorted(MEMORY_REQUIRED_KEYS),
                "got": sorted(payload.keys()),
            },
        )
    bounds = payload.get("bounds")
    if not isinstance(bounds, dict) or set(bounds.keys()) != BOUNDS_REQUIRED_KEYS:
        raise DecisionSurfaceError(
            "run_invalid",
            "memory.bounds must be a complete MemoryBounds.to_dict() export.",
        )
    records = payload.get("records")
    if not isinstance(records, (list, tuple)):
        raise DecisionSurfaceError("run_invalid", "memory.records must be a list.")
    if payload.get("record_count") != len(records):
        raise DecisionSurfaceError(
            "run_invalid",
            "memory.record_count must equal len(records).",
        )
    summary = payload.get("summary")
    if not isinstance(summary, (list, tuple)):
        raise DecisionSurfaceError("run_invalid", "memory.summary must be a list.")
    for item in records:
        if not isinstance(item, dict):
            raise DecisionSurfaceError(
                "run_invalid",
                "memory.records entries must be objects (lossless decode).",
            )
        if set(item.keys()) != RECORD_REQUIRED_KEYS:
            raise DecisionSurfaceError(
                "run_invalid",
                "memory record key set must match RetainedEvidence.to_dict().",
            )
        provenance = item.get("provenance")
        if not isinstance(provenance, dict) or set(provenance.keys()) != PROVENANCE_REQUIRED_KEYS:
            raise DecisionSurfaceError(
                "run_invalid",
                "memory record provenance must be a complete MemoryProvenance export.",
            )
        location = item.get("location")
        if location is not None:
            if not isinstance(location, dict) or set(location.keys()) != LOCATION_REQUIRED_KEYS:
                raise DecisionSurfaceError(
                    "run_invalid",
                    "memory record location must be null or a complete ViewLocation export.",
                )
    try:
        constructed = MemorySnapshot.from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "run_invalid",
            f"memory failed construction: {exc}",
        ) from exc
    _require_full_export_equality(payload, constructed.to_dict(), label="memory")
    return constructed


def _json_ready(value: Any) -> Any:
    """Recursively convert tuples to lists so canonical JSON encoding is possible."""

    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    return value


def _require_full_export_equality(
    payload: dict[str, Any],
    exported: dict[str, Any],
    *,
    label: str,
) -> None:
    left = _json_ready(payload)
    right = _json_ready(exported)
    try:
        if canonical_json_utf8(left) != canonical_json_utf8(right):
            raise DecisionSurfaceError(
                "run_invalid",
                f"Apply {label} is not a complete lossless to_dict() export "
                "(canonical export equality failed).",
            )
    except ValueError as exc:
        raise DecisionSurfaceError(
            "run_invalid",
            f"Apply {label} is not strictly JSON-serializable: {exc}",
        ) from exc


def apply_vehicle_decision(
    *,
    vehicle_id: str | None,
    from_run: str | Path,
    json_output: bool = False,
    record: bool = False,
    output_root: Path | None = None,
    verify_twice: bool = True,
) -> CommandResult:
    """Offline deterministic apply of a recorded decision sequence."""

    if vehicle_id is None or str(vehicle_id).strip() == "":
        exc = DecisionSurfaceError(
            "missing_vehicle_id",
            "decision apply requires --id <vehicle>. "
            "Example: ./cli/automa vehicles decision apply --id <vehicle> --from-run <dir>",
        )
        return _error_result(exc, json_output=json_output)

    try:
        return _apply_vehicle_decision_body(
            vehicle_id=str(vehicle_id),
            from_run=from_run,
            json_output=json_output,
            record=record,
            output_root=output_root,
            verify_twice=verify_twice,
        )
    except DecisionSurfaceError as exc:
        if exc.vehicle_id is None:
            exc = DecisionSurfaceError(
                exc.error,
                exc.message_text,
                vehicle_id=str(vehicle_id),
                details=exc.details,
                exit_code=exc.exit_code,
            )
        return _error_result(exc, json_output=json_output)


def _apply_vehicle_decision_body(
    *,
    vehicle_id: str,
    from_run: str | Path,
    json_output: bool,
    record: bool,
    output_root: Path | None,
    verify_twice: bool,
) -> CommandResult:
    run_dir = Path(from_run).expanduser()
    sequence_path = run_dir / "sequence.json" if run_dir.is_dir() else run_dir
    if not sequence_path.exists():
        raise DecisionSurfaceError(
            "run_missing",
            f"Decision apply run is missing sequence.json at {display_path(sequence_path)}.",
            vehicle_id=vehicle_id,
        )

    try:
        size = sequence_path.stat().st_size
    except OSError as exc:
        raise DecisionSurfaceError(
            "run_missing",
            f"Could not stat sequence file: {exc}",
            vehicle_id=vehicle_id,
        ) from exc
    if size > DECISION_APPLY_MAX_SEQUENCE_FILE_BYTES:
        raise DecisionSurfaceError(
            "run_bounds_exceeded",
            f"sequence.json is {size} bytes; max is {DECISION_APPLY_MAX_SEQUENCE_FILE_BYTES}.",
            vehicle_id=vehicle_id,
        )

    try:
        sequence = json.loads(sequence_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DecisionSurfaceError(
            "run_invalid",
            f"Could not parse sequence.json: {exc}",
            vehicle_id=vehicle_id,
        ) from exc
    if not isinstance(sequence, dict):
        raise DecisionSurfaceError(
            "run_invalid",
            "sequence.json must be a JSON object.",
            vehicle_id=vehicle_id,
        )
    if sequence.get("schema") != APPLY_SEQUENCE_SCHEMA:
        raise DecisionSurfaceError(
            "run_invalid",
            f"sequence schema must be {APPLY_SEQUENCE_SCHEMA!r}.",
            vehicle_id=vehicle_id,
        )
    if "vehicle_id" in sequence and sequence.get("vehicle_id") != vehicle_id:
        raise DecisionSurfaceError(
            "run_invalid",
            f"sequence vehicle_id {sequence.get('vehicle_id')!r} does not match --id {vehicle_id!r}.",
            vehicle_id=vehicle_id,
        )
    frames = sequence.get("frames")
    if not isinstance(frames, list) or not frames:
        raise DecisionSurfaceError(
            "run_invalid",
            "sequence.frames must be a non-empty array.",
            vehicle_id=vehicle_id,
        )
    if len(frames) > DECISION_APPLY_MAX_FRAMES:
        raise DecisionSurfaceError(
            "run_bounds_exceeded",
            f"sequence has {len(frames)} frames; max is {DECISION_APPLY_MAX_FRAMES}.",
            vehicle_id=vehicle_id,
        )

    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))
    activation_path = Path(bundle["decision_runtime_dir"]) / "active.json"
    if not activation_path.exists():
        raise DecisionSurfaceError(
            "activation_missing",
            "\n".join(
                [
                    f"No active decision engine found for {vehicle_id!r}.",
                    f"Expected activation: {display_path(activation_path)}",
                    "Run: ./cli/automa vehicles update decision --id <vehicle_id> "
                    "--engine shadow-proposals",
                ]
            ),
            vehicle_id=vehicle_id,
        )
    try:
        activation = json.loads(activation_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DecisionSurfaceError(
            "activation_invalid",
            f"Could not parse decision activation: {exc}",
            vehicle_id=vehicle_id,
        ) from exc
    decision = activation.get("decision") if isinstance(activation, dict) else None
    if not isinstance(decision, dict):
        raise DecisionSurfaceError(
            "activation_invalid",
            "Decision activation has no decision section.",
            vehicle_id=vehicle_id,
        )
    if decision.get("engine_id") != ENGINE_ID:
        raise DecisionSurfaceError(
            "wrong_engine",
            f"decision apply requires engine_id={ENGINE_ID!r}; got {decision.get('engine_id')!r}.",
            vehicle_id=vehicle_id,
        )
    engine_config = (
        decision.get("engine_config")
        if isinstance(decision.get("engine_config"), dict)
        else {}
    )
    try:
        cfg = validate_shadow_engine_config(dict(engine_config))
    except DecisionSurfaceError as exc:
        raise DecisionSurfaceError(
            "activation_invalid",
            exc.message_text,
            vehicle_id=vehicle_id,
            details=exc.details,
        ) from exc

    normalized_frames = _normalize_apply_frames(frames, vehicle_id=vehicle_id)

    digest_a = _run_apply_pass(cfg, normalized_frames)
    bytes_a = canonical_json_utf8(digest_a)
    digest_sha256 = hashlib.sha256(bytes_a).hexdigest()
    second_pass_sha: str | None = None
    deterministic = True
    if verify_twice:
        digest_b = _run_apply_pass(cfg, normalized_frames)
        bytes_b = canonical_json_utf8(digest_b)
        second_pass_sha = hashlib.sha256(bytes_b).hexdigest()
        deterministic = bytes_a == bytes_b
        if not deterministic:
            raise DecisionSurfaceError(
                "apply_non_deterministic",
                "Decision apply digests differed across two independent passes.",
                vehicle_id=vehicle_id,
                details={
                    "digest_sha256": digest_sha256,
                    "second_pass_digest_sha256": second_pass_sha,
                },
            )

    payload: dict[str, Any] = {
        "schema": APPLY_RESULT_SCHEMA,
        "vehicle_id": vehicle_id,
        "from_run": display_path(run_dir if run_dir.is_dir() else sequence_path.parent),
        "frame_count": len(normalized_frames),
        "engine_id": ENGINE_ID,
        "activation": display_path(activation_path),
        "digest": digest_a,
        "digest_sha256": digest_sha256,
        "deterministic": deterministic,
        "second_pass_digest_sha256": second_pass_sha,
        "recorded": False,
        "record_dir": None,
    }

    if record:
        record_dir = _write_apply_record(
            vehicle_id=vehicle_id,
            from_run_dir=run_dir if run_dir.is_dir() else sequence_path.parent,
            frames=normalized_frames,
            payload=payload,
            engine_config=cfg,
            output_root=output_root or decision_apply_output_root(),
        )
        payload["recorded"] = True
        payload["record_dir"] = display_path(record_dir)

    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    lines = [
        f"Decision apply: {vehicle_id}",
        f"From-run: {payload['from_run']} ({payload['frame_count']} frames)",
        f"Engine: {ENGINE_ID}",
        f"Activation: {payload['activation']}",
        f"Digest sha256: {digest_sha256}",
        "Deterministic: yes (canonical_json_utf8 byte equality)"
        if deterministic
        else "Deterministic: no",
        f"Recorded: {payload['record_dir']}" if record else "Record: disabled (pass --record)",
    ]
    return CommandResult(0, "\n".join(lines))


def _normalize_apply_frames(
    frames: list[Any],
    *,
    vehicle_id: str,
) -> list[dict[str, Any]]:
    seen_ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(frames):
        if not isinstance(raw, dict):
            raise DecisionSurfaceError(
                "run_invalid",
                f"frames[{index}] must be an object.",
                vehicle_id=vehicle_id,
            )
        required = {"frame_id", "frame_index", "timestamp_ms", "observation"}
        if not required.issubset(raw.keys()):
            raise DecisionSurfaceError(
                "run_invalid",
                f"frames[{index}] missing required keys {sorted(required - set(raw.keys()))}.",
                vehicle_id=vehicle_id,
            )
        try:
            frame_id = require_ascii_id(raw["frame_id"], field_name="frame_id")
            frame_index = require_safe_int(raw["frame_index"], field_name="frame_index")
            timestamp_ms = require_safe_int(raw["timestamp_ms"], field_name="timestamp_ms")
        except ValueError as exc:
            raise DecisionSurfaceError(
                "run_invalid",
                f"frames[{index}] identity invalid: {exc}",
                vehicle_id=vehicle_id,
            ) from exc
        if frame_index < 0:
            raise DecisionSurfaceError(
                "run_invalid",
                f"frames[{index}].frame_index must be non-negative.",
                vehicle_id=vehicle_id,
            )
        if frame_id in seen_ids:
            raise DecisionSurfaceError(
                "run_invalid",
                f"Duplicate frame_id {frame_id!r} in sequence.",
                vehicle_id=vehicle_id,
            )
        seen_ids.add(frame_id)

        observation_payload = raw.get("observation")
        observation: Observation | None
        if observation_payload is None:
            observation = None
        else:
            observation = strict_decode_apply_observation(observation_payload)

        observation_error = raw.get("observation_error")
        if observation_error is not None and (
            type(observation_error) is not str or not observation_error
        ):
            raise DecisionSurfaceError(
                "run_invalid",
                f"frames[{index}].observation_error must be a non-empty string when set.",
                vehicle_id=vehicle_id,
            )

        memory_payload = raw.get("memory")
        memory: MemorySnapshot | None
        if memory_payload is None or "memory" not in raw:
            memory = None
        else:
            memory = strict_decode_apply_memory(memory_payload)

        normalized.append(
            {
                "frame_id": frame_id,
                "frame_index": frame_index,
                "timestamp_ms": timestamp_ms,
                "observation": observation,
                "observation_error": observation_error,
                "memory": memory,
                "raw": raw,
            }
        )
    return normalized


def _run_apply_pass(
    cfg: ShadowProposalsConfig,
    frames: list[dict[str, Any]],
) -> dict[str, Any]:
    engine = create_shadow_proposals_engine(cfg)
    digest_frames: list[dict[str, Any]] = []
    for frame in frames:
        cycle_result, _control = engine.run_cycle(
            frame_id=frame["frame_id"],
            frame_index=frame["frame_index"],
            timestamp_ms=frame["timestamp_ms"],
            observation=frame["observation"],
            observation_error=frame["observation_error"],
            memory=frame["memory"],
            host_application=None,
        )
        plan = cycle_result.plan
        authority = cycle_result.authority
        candidates: list[dict[str, Any]] = []
        if plan is not None:
            for cand in plan.candidates:
                candidates.append(
                    {
                        "plugin_id": cand.plugin_id,
                        "lifecycle": cand.lifecycle,
                        "reason": cand.reason,
                        "confidence": cand.confidence,
                    }
                )
        proposed = None
        if authority.proposed is not None:
            proposed = {
                "steering": authority.proposed.steering,
                "throttle": authority.proposed.throttle,
            }
        digest_frames.append(
            {
                "frame_id": cycle_result.frame_id,
                "cycle_status": cycle_result.status,
                "cycle_reason": cycle_result.reason,
                "plan_status": plan.status if plan is not None else None,
                "selected_proposal_id": (
                    plan.selected_proposal_id if plan is not None else None
                ),
                "candidates": candidates,
                "proposed": proposed,
                "proposed_applied": False,
                "authorized_output": authorized_idle_output(),
            }
        )
    return {
        "schema": APPLY_DIGEST_SCHEMA,
        "frame_count": len(digest_frames),
        "frames": digest_frames,
    }


def _write_apply_record(
    *,
    vehicle_id: str,
    from_run_dir: Path,
    frames: list[dict[str, Any]],
    payload: dict[str, Any],
    engine_config: ShadowProposalsConfig,
    output_root: Path,
) -> Path:
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    nonce = secrets.token_hex(3)
    final_dir = output_root / f"{safe_path_part(vehicle_id)}-{stamp}-{nonce}"
    partial_dir = output_root / f".{safe_path_part(vehicle_id)}-{stamp}-{nonce}.partial"
    if final_dir.exists() or partial_dir.exists():
        raise DecisionSurfaceError(
            "record_write_failed",
            f"Record directory already exists: {display_path(final_dir)}",
            vehicle_id=vehicle_id,
        )
    try:
        partial_dir.mkdir(parents=True, exist_ok=False)
        frames_dir = partial_dir / "frames"
        source_frames_dir = partial_dir / "source_frames"
        frames_dir.mkdir()
        source_frames_dir.mkdir()

        engine = create_shadow_proposals_engine(engine_config)
        cycle_results: list[Any] = []
        for frame in frames:
            cycle_result, _ = engine.run_cycle(
                frame_id=frame["frame_id"],
                frame_index=frame["frame_index"],
                timestamp_ms=frame["timestamp_ms"],
                observation=frame["observation"],
                observation_error=frame["observation_error"],
                memory=frame["memory"],
                host_application=None,
            )
            cycle_results.append(cycle_result)

        frame_entries: list[dict[str, Any]] = []
        for frame, cycle_result in zip(frames, cycle_results):
            frame_id = frame["frame_id"]
            html_name = f"{frame_id}.html"
            source_image_rel = None
            src = from_run_dir / "frames" / f"{frame_id}.png"
            if src.is_file():
                resolved = src.resolve()
                base = (from_run_dir / "frames").resolve()
                if resolved.parent != base:
                    raise DecisionSurfaceError(
                        "run_invalid",
                        f"Source image path escapes from-run frames dir: {src}",
                        vehicle_id=vehicle_id,
                    )
                dest = source_frames_dir / f"{frame_id}.png"
                shutil.copyfile(resolved, dest)
                source_image_rel = f"source_frames/{frame_id}.png"
            html_body = render_decision_exact_frame_html(
                vehicle_id=vehicle_id,
                frame_id=frame_id,
                cycle_result=cycle_result,
                source_image_rel=source_image_rel,
            )
            (frames_dir / html_name).write_text(html_body, encoding="utf-8")
            frame_entries.append(
                {
                    "frame_id": frame_id,
                    "html": f"frames/{html_name}",
                    "source_image": source_image_rel,
                }
            )

        (partial_dir / "digest.json").write_text(
            json.dumps(payload["digest"], indent=2, sort_keys=True),
            encoding="utf-8",
        )
        manifest = {
            "schema": EXACT_FRAME_REVIEW_SCHEMA,
            "view_id": COMBINED_VIEW_ID,
            "vehicle_id": vehicle_id,
            "frame_count": len(frame_entries),
            "frames": frame_entries,
            "proposed_applied": False,
            "bounds": {
                "max_frames": DECISION_APPLY_MAX_FRAMES,
                "max_record_bytes": DECISION_APPLY_MAX_RECORD_BYTES,
            },
            "note": "proposed_applied=false; authorized output is shadow-only idle",
        }
        (partial_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        total = _directory_byte_size(partial_dir)
        if total > DECISION_APPLY_MAX_RECORD_BYTES:
            raise DecisionSurfaceError(
                "record_bounds_exceeded",
                f"Record artifacts are {total} bytes; max is {DECISION_APPLY_MAX_RECORD_BYTES}.",
                vehicle_id=vehicle_id,
            )

        partial_dir.rename(final_dir)
        recorded_payload = dict(payload)
        recorded_payload["recorded"] = True
        recorded_payload["record_dir"] = display_path(final_dir)
        (final_dir / "result.json").write_text(
            json.dumps(recorded_payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        total = _directory_byte_size(final_dir)
        if total > DECISION_APPLY_MAX_RECORD_BYTES:
            _remove_tree_strict(final_dir)
            raise DecisionSurfaceError(
                "record_bounds_exceeded",
                f"Record artifacts are {total} bytes; max is {DECISION_APPLY_MAX_RECORD_BYTES}.",
                vehicle_id=vehicle_id,
            )
        return final_dir
    except DecisionSurfaceError:
        _remove_tree_strict(partial_dir)
        if final_dir.exists():
            _remove_tree_strict(final_dir)
        raise
    except Exception as exc:  # noqa: BLE001
        _remove_tree_strict(partial_dir)
        if final_dir.exists():
            _remove_tree_strict(final_dir)
        raise DecisionSurfaceError(
            "record_write_failed",
            f"Could not write decision apply record: {type(exc).__name__}: {exc}",
            vehicle_id=vehicle_id,
        ) from exc


def render_decision_exact_frame_html(
    *,
    vehicle_id: str,
    frame_id: str,
    cycle_result: Any,
    source_image_rel: str | None,
) -> str:
    cycle = cycle_result.to_dict() if hasattr(cycle_result, "to_dict") else dict(cycle_result)
    plan = cycle.get("plan") if isinstance(cycle.get("plan"), dict) else None
    authority = cycle.get("authority") if isinstance(cycle.get("authority"), dict) else {}
    source = cycle.get("source") if isinstance(cycle.get("source"), dict) else None
    plan_summary = _plan_summary(plan)
    selected_id = plan_summary.get("selected_proposal_id")
    selected_refs: list[Any] = []
    for cand in plan_summary.get("candidates") or []:
        if isinstance(cand, dict) and cand.get("proposal_id") == selected_id:
            selected_refs = cand.get("source_refs") or []
            break

    def esc(value: Any) -> str:
        return html.escape(str(value), quote=True)

    image_block = (
        f'<img src="{esc(source_image_rel)}" alt="frame {esc(frame_id)}" />'
        if source_image_rel
        else "<p>Observation image unavailable</p>"
    )
    candidates_html = []
    for cand in plan_summary.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        candidates_html.append(
            "<li>"
            f"plugin={esc(cand.get('plugin_id'))} lifecycle={esc(cand.get('lifecycle'))} "
            f"freshness={esc(cand.get('freshness'))} conf={esc(cand.get('confidence'))} "
            f"reason={esc(cand.get('reason'))} command={esc(cand.get('command'))} "
            f"source_refs={esc(json.dumps(cand.get('source_refs') or [], sort_keys=True))}"
            "</li>"
        )
    mem = _memory_summary(source)
    records_html = "".join(
        f"<li>{esc(item)}</li>" for item in (mem.get("records") or [])
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>decision-combined-v0 {esc(frame_id)}</title>
  <style>
    body {{ font-family: ui-monospace, monospace; margin: 1.5rem; }}
    section {{ margin-bottom: 1.25rem; }}
    h1, h2 {{ margin: 0 0 0.5rem 0; }}
    .emph {{ color: #a40; font-weight: bold; }}
    img {{ max-width: 480px; border: 1px solid #444; }}
  </style>
</head>
<body>
  <h1>decision-combined-v0</h1>
  <p>vehicle={esc(vehicle_id)} frame_id={esc(frame_id)}</p>
  <section id="observation">
    <h2>Observation / camera</h2>
    {image_block}
  </section>
  <section id="memory">
    <h2>Retained evidence</h2>
    <p>status={esc(mem.get('status'))} health={esc(mem.get('health'))}
       records={esc(mem.get('record_count'))}</p>
    <ul>{records_html or "<li>(none)</li>"}</ul>
  </section>
  <section id="proposals">
    <h2>Proposals</h2>
    <ul>{"".join(candidates_html) or "<li>(none)</li>"}</ul>
  </section>
  <section id="selection">
    <h2>Selection</h2>
    <p>status={esc(plan_summary.get('status'))}
       selected_proposal_id={esc(selected_id)}</p>
  </section>
  <section id="source_refs">
    <h2>source_refs</h2>
    <pre>{esc(json.dumps(selected_refs, indent=2, sort_keys=True))}</pre>
  </section>
  <section id="authority">
    <h2>Authority</h2>
    <p>proposed={esc(authority.get('proposed'))}</p>
    <p>authorized_output={esc(authority.get('authorized_output'))}</p>
    <p class="emph">proposed_applied=false</p>
    <p>host_application={esc(authority.get('host_application'))}</p>
  </section>
  <section id="non-claims">
    <h2>Non-claims</h2>
    <p>no object identity; shadow-only; not navigation certification.</p>
  </section>
</body>
</html>
"""


def _directory_byte_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def _remove_tree_strict(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


# ---------------------------------------------------------------------------
# Shared formatting / activation helpers
# ---------------------------------------------------------------------------


def _decision_activation(
    *,
    vehicle_id: str,
    engine_id: str,
    bundle: dict[str, str],
    release: dict[str, Any] | None,
    manager: AutonomyManager,
    engine_config: dict[str, Any],
    engine_description: str,
    engine_spec: str,
) -> dict[str, Any]:
    return {
        "schema": "automa_decision_activation_v0",
        "vehicle_id": vehicle_id,
        "activated_at_ms": int(time.time() * 1000),
        "controller_bundle": {
            "root_dir": bundle["root_dir"],
            "autonomy_dir": bundle["autonomy_dir"],
            "implementations_dir": bundle["implementations_dir"],
            "decision_dir": bundle["decision_dir"],
            "decision_runtime_dir": bundle["decision_runtime_dir"],
            "release": release_activation_summary(release) if release is not None else None,
        },
        "decision": {
            "engine_id": engine_id,
            "description": engine_description,
            "engine_spec": engine_spec,
            "engine_config": dict(engine_config),
            "engine_schema": manager.status()["engine_schema"],
        },
    }


def _format_decision_info(payload: dict[str, Any]) -> str:
    activation = payload["activation"]
    schema = payload.get("engine_schema") if isinstance(payload.get("engine_schema"), dict) else {}
    stages = schema.get("stages") if isinstance(schema.get("stages"), dict) else {}
    lines = [
        f"Decision: {payload['vehicle_id']} -> {activation.get('engine_id', 'unknown')}",
        f"Engine: {activation.get('engine_spec', 'unknown')}",
        f"Activation: {activation['path']}",
        f"Schema source: {(payload.get('engine_schema_source') or {}).get('engine_spec', 'unknown')}.describe_schema()",
        "",
        "Stages:",
        *(
            [f"- {name}: {value if value is not None else 'disabled'}" for name, value in stages.items()]
            or ["- none declared"]
        ),
        "",
        f"Output: {(schema.get('output') or {}).get('type', 'unknown') if isinstance(schema.get('output'), dict) else 'unknown'}",
    ]
    shadow = payload.get("shadow")
    if isinstance(shadow, dict):
        lines.extend(
            [
                "",
                "Shadow decision:",
                f"- inputs: {', '.join(shadow.get('decision_inputs') or [])}",
                f"- enabled_plugins: {', '.join(shadow.get('enabled_plugins') or [])}",
                f"- selector: {shadow.get('selector_id')}",
                f"- output_schemas: {json.dumps(shadow.get('output_schemas') or {}, sort_keys=True)}",
                (
                    f"- authority: proposed_applied={shadow.get('authority', {}).get('proposed_applied')} "
                    f"idle_reason={shadow.get('authority', {}).get('authorized_idle_reason')} "
                    f"mode={shadow.get('authority', {}).get('authority_mode')}"
                ),
            ]
        )
    combined = payload.get("combined_view") if isinstance(payload.get("combined_view"), dict) else {}
    lines.extend(
        [
            "",
            f"Combined view: id={combined.get('view_id')} "
            f"url={combined.get('url')} path_template={combined.get('path_template')}",
        ]
    )
    return "\n".join(lines)
