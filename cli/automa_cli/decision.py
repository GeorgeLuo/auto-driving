"""Automa decision stage, info, stream, and offline apply surfaces (M006-05)."""

from __future__ import annotations

import hashlib
import html
import json
import os
import secrets
import shutil
import stat as stat_mod
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TextIO

from autonomy.decision import (
    ACTION_PLAN_SCHEMA,
    ACTION_PROPOSAL_SCHEMA,
    DECISION_DATA_SOURCE_SCHEMA,
    SHADOW_AUTHORITY_RESULT_SCHEMA,
    SHADOW_DECISION_CYCLE_RESULT_SCHEMA,
    SELECTOR_ID,
    ShadowProposalsConfig,
    canonical_json_utf8,
)
from autonomy.decision.action_plan import ActionPlan, PlanContribution, select_action_plan
from autonomy.decision.action_proposal import (
    PROPOSED_VEHICLE_COMMAND_SCHEMA,
    ActionProposal,
    ProposedVehicleCommand,
)
from autonomy.decision.decision_data import ComponentEnvelope, DecisionDataSource
from autonomy.decision.memory import (
    MEMORY_SNAPSHOT_SCHEMA,
    MemorySnapshot,
)
from autonomy.decision.observation import OBSERVATION_SCHEMA, Observation
from autonomy.decision.shadow_authority import (
    AUTHORIZED_IDLE_REASON,
    ShadowAuthorityResult,
    ShadowDecisionCycleResult,
    authorized_idle_output,
)
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

STREAM_FRAME_EXACT_KEYS = frozenset(
    {
        "schema",
        "vehicle_id",
        "engine_id",
        "run_id",
        "worker_pid",
        "activation_engine_id",
        "activation_activated_at_ms",
        "published_at_ms",
        "frame_id",
        "frame_index",
        "timestamp_ms",
        "cycle",
        "observation_summary",
        "memory_summary",
        "plan_summary",
        "authority_summary",
        "view",
    }
)
CYCLE_EXACT_KEYS = frozenset(
    {"schema", "frame_id", "status", "reason", "source", "plan", "authority"}
)
AUTHORITY_EXACT_KEYS = frozenset(
    {
        "schema",
        "frame_id",
        "proposed",
        "authorized_output",
        "proposed_applied",
        "host_application",
        "proposed_equals_authorized",
        "cycle_status",
        "cycle_reason",
        "authority_mode",
        "drive_mode_gate",
    }
)
PLAN_EXACT_KEYS = frozenset(
    {
        "schema",
        "plan_id",
        "frame_id",
        "timestamp_ms",
        "status",
        "selected_proposal_id",
        "contributions",
        "candidates",
        "selector_id",
        "metadata",
    }
)
SOURCE_EXACT_KEYS = frozenset(
    {
        "schema",
        "source_id",
        "frame_id",
        "frame_index",
        "timestamp_ms",
        "observation",
        "memory",
        "patterns",
        "projections",
        "capabilities",
        "prior_host_applied_command",
        "metadata",
    }
)
ENVELOPE_EXACT_KEYS = frozenset({"status", "value", "reason", "updated_at_ms"})
PROPOSAL_EXACT_KEYS = frozenset(
    {
        "schema",
        "proposal_id",
        "plugin_id",
        "frame_id",
        "lifecycle",
        "freshness",
        "confidence",
        "reason",
        "command",
        "assumptions",
        "source_refs",
        "available",
        "metadata",
    }
)
CONTRIBUTION_EXACT_KEYS = frozenset({"proposal_id", "plugin_id", "weight", "role"})
SOURCE_REF_EXACT_KEYS = frozenset(
    {"kind", "id", "frame_id", "observation_id", "plugin_id", "note"}
)
COMMAND_EXACT_KEYS = frozenset(
    {"schema", "steering", "throttle", "gear", "normalized"}
)
OBS_SUMMARY_EXACT_KEYS = frozenset({"status", "frame_id", "reason"})
MEM_SUMMARY_EXACT_KEYS = frozenset({"status", "health", "record_count", "records"})
PLAN_SUMMARY_EXACT_KEYS = frozenset(
    {"status", "selected_proposal_id", "candidates", "contributions"}
)
PLAN_SUMMARY_CAND_EXACT_KEYS = frozenset(
    {
        "proposal_id",
        "plugin_id",
        "lifecycle",
        "freshness",
        "confidence",
        "reason",
        "command",
        "source_refs",
    }
)
AUTHORITY_SUMMARY_EXACT_KEYS = frozenset(
    {
        "proposed",
        "authorized_output",
        "proposed_applied",
        "host_application",
        "proposed_equals_authorized",
        "cycle_status",
        "cycle_reason",
    }
)
VIEW_EXACT_KEYS = frozenset({"view_id", "applied_false_emphasized"})

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


def _read_surface_activation(
    activation_path: Path,
    *,
    vehicle_id: str,
) -> dict[str, Any]:
    """Canonical activation read for every operator-facing decision surface."""

    try:
        decoded = read_decision_activation(activation_path)
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "activation_invalid",
            f"Invalid decision activation {display_path(activation_path)}: {exc}",
            vehicle_id=vehicle_id,
        ) from exc

    if decoded.engine_id == ENGINE_ID:
        if decoded.engine_spec != ADAPTER_ENGINE_SPEC:
            raise DecisionSurfaceError(
                "activation_invalid",
                f"shadow-proposals engine_spec must be {ADAPTER_ENGINE_SPEC!r}; "
                f"got {decoded.engine_spec!r}.",
                vehicle_id=vehicle_id,
                details={
                    "expected_engine_spec": ADAPTER_ENGINE_SPEC,
                    "got_engine_spec": decoded.engine_spec,
                },
            )
        try:
            validate_shadow_engine_config(decoded.engine_config)
        except DecisionSurfaceError as exc:
            raise DecisionSurfaceError(
                "activation_invalid",
                exc.message_text,
                vehicle_id=vehicle_id,
                details=exc.details,
            ) from exc
    return decoded.payload


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
        activation = _read_surface_activation(
            activation_path,
            vehicle_id=vehicle_id,
        )
    except DecisionSurfaceError as err:
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
    reconstructed_cycle = _require_stream_frame_envelope(frame)

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
    engine_config = decision.get("engine_config")
    if not isinstance(engine_config, dict):
        raise DecisionSurfaceError(
            "activation_invalid",
            "Decision activation engine_config must be an object.",
        )
    try:
        shadow_config = validate_shadow_engine_config(engine_config)
    except DecisionSurfaceError as exc:
        raise DecisionSurfaceError(
            "activation_invalid",
            exc.message_text,
            details=exc.details,
        ) from exc
    _require_runner_plan_alignment(reconstructed_cycle, shadow_config)

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
    frame_pid = frame.get("worker_pid")
    if type(state_pid) is not int or type(frame_pid) is not int or state_pid != frame_pid:
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
    _require_stream_summaries_match_cycle(frame, cycle)


def _require_non_bool_int(value: object, *, field: str) -> int:
    if type(value) is not int:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"Latest decision frame {field} must be a non-bool int.",
            details={"field": field},
        )
    return value


def _require_exact_keys(
    payload: dict[str, Any],
    exact: frozenset[str],
    *,
    field: str,
) -> None:
    keys = set(payload.keys())
    if keys != set(exact):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} key set is not exact export.",
            details={
                "field": field,
                "expected": sorted(exact),
                "got": sorted(keys),
                "extra": sorted(keys - set(exact)),
                "missing": sorted(set(exact) - keys),
            },
        )


def _require_stream_frame_envelope(
    frame: dict[str, Any],
) -> ShadowDecisionCycleResult:
    """Validate exact vehicle_decision_stream_frame_v0 + nested cycle export."""

    if "applied_control" in frame:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "Latest decision frame must not include applied_control.",
            details={"field": "applied_control"},
        )
    if frame.get("schema") != STREAM_FRAME_SCHEMA:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"Latest decision frame schema must be {STREAM_FRAME_SCHEMA!r}.",
        )
    _require_exact_keys(frame, STREAM_FRAME_EXACT_KEYS, field="stream_frame")

    for key in ("vehicle_id", "engine_id", "run_id", "activation_engine_id"):
        if type(frame.get(key)) is not str or not str(frame.get(key)):
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                f"Latest decision frame {key} must be a non-empty string.",
                details={"field": key},
            )
    try:
        require_ascii_id(frame["frame_id"], field_name="frame_id")
    except ValueError as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"Latest decision frame frame_id is not a valid ASCII id: {exc}",
            details={"field": "frame_id"},
        ) from exc
    _require_non_bool_int(frame.get("worker_pid"), field="worker_pid")
    _require_non_bool_int(
        frame.get("activation_activated_at_ms"),
        field="activation_activated_at_ms",
    )
    _require_non_bool_int(frame.get("published_at_ms"), field="published_at_ms")
    _require_non_bool_int(frame.get("frame_index"), field="frame_index")
    _require_non_bool_int(frame.get("timestamp_ms"), field="timestamp_ms")

    _require_observation_summary(frame["observation_summary"])
    _require_memory_summary(frame["memory_summary"])
    _require_plan_summary_envelope(frame["plan_summary"])
    _require_authority_summary(frame["authority_summary"])
    view = frame["view"]
    if not isinstance(view, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "view must be an object.",
        )
    _require_exact_keys(view, VIEW_EXACT_KEYS, field="view")
    if view.get("view_id") != COMBINED_VIEW_ID or view.get("applied_false_emphasized") is not True:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "view must be decision-combined-v0 with applied_false_emphasized=true.",
        )

    cycle = frame["cycle"]
    if not isinstance(cycle, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "Latest decision frame cycle must be an object.",
        )
    reconstructed = _require_exact_cycle_export(cycle)
    _require_aggregate_cycle_alignment(frame, reconstructed)
    return reconstructed


def _require_observation_summary(payload: object) -> None:
    if not isinstance(payload, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "observation_summary must be an object.",
        )
    _require_exact_keys(payload, OBS_SUMMARY_EXACT_KEYS, field="observation_summary")


def _require_memory_summary(payload: object) -> None:
    if not isinstance(payload, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "memory_summary must be an object.",
        )
    _require_exact_keys(payload, MEM_SUMMARY_EXACT_KEYS, field="memory_summary")
    records = payload.get("records")
    if not isinstance(records, list):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "memory_summary.records must be a list.",
        )


def _require_plan_summary_envelope(payload: object) -> None:
    if not isinstance(payload, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "plan_summary must be an object.",
        )
    _require_exact_keys(payload, PLAN_SUMMARY_EXACT_KEYS, field="plan_summary")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "plan_summary.candidates must be a list.",
        )
    for index, cand in enumerate(candidates):
        if not isinstance(cand, dict):
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                f"plan_summary.candidates[{index}] must be an object.",
            )
        _require_exact_keys(
            cand,
            PLAN_SUMMARY_CAND_EXACT_KEYS,
            field=f"plan_summary.candidates[{index}]",
        )
    contributions = payload.get("contributions")
    if not isinstance(contributions, list):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "plan_summary.contributions must be a list.",
        )


def _require_authority_summary(payload: object) -> None:
    if not isinstance(payload, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "authority_summary must be an object.",
        )
    _require_exact_keys(payload, AUTHORITY_SUMMARY_EXACT_KEYS, field="authority_summary")
    if payload.get("proposed_applied") is not False:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "authority_summary.proposed_applied must be false.",
        )
    if "applied_control" in payload:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "authority_summary must not include applied_control.",
        )


def _require_canonical_export_equal(
    payload: object,
    exported: object,
    *,
    field: str,
) -> None:
    try:
        if canonical_json_utf8(_json_ready(payload)) != canonical_json_utf8(
            _json_ready(exported)
        ):
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                f"{field} is not a complete lossless typed export.",
                details={"field": field},
            )
    except ValueError as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} is not strictly JSON-serializable: {exc}",
            details={"field": field},
        ) from exc


def _strict_decode_command(payload: object, *, field: str) -> ProposedVehicleCommand:
    if not isinstance(payload, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} must be a ProposedVehicleCommand object.",
            details={"field": field},
        )
    _require_exact_keys(payload, COMMAND_EXACT_KEYS, field=field)
    if payload.get("schema") != PROPOSED_VEHICLE_COMMAND_SCHEMA:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field}.schema must be {PROPOSED_VEHICLE_COMMAND_SCHEMA!r}.",
            details={"field": field},
        )
    try:
        command = ProposedVehicleCommand.from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} failed ProposedVehicleCommand construction: {exc}",
            details={"field": field},
        ) from exc
    _require_canonical_export_equal(payload, command.to_dict(), field=field)
    return command


def _strict_decode_envelope(payload: object, *, field: str) -> ComponentEnvelope:
    if not isinstance(payload, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} must be a ComponentEnvelope object.",
            details={"field": field},
        )
    _require_exact_keys(payload, ENVELOPE_EXACT_KEYS, field=field)
    try:
        envelope = ComponentEnvelope(
            status=payload["status"],
            value=payload.get("value"),
            reason=payload.get("reason") or "",
            updated_at_ms=payload.get("updated_at_ms") or 0,
        )
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} failed ComponentEnvelope construction: {exc}",
            details={"field": field},
        ) from exc
    _require_canonical_export_equal(payload, envelope.to_dict(), field=field)
    return envelope


def _strict_decode_proposal(payload: object, *, field: str) -> ActionProposal:
    if not isinstance(payload, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} must be an ActionProposal object.",
            details={"field": field},
        )
    _require_exact_keys(payload, PROPOSAL_EXACT_KEYS, field=field)
    if payload.get("schema") != ACTION_PROPOSAL_SCHEMA:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field}.schema must be {ACTION_PROPOSAL_SCHEMA!r}.",
            details={"field": field},
        )
    command = payload.get("command")
    if command is not None:
        _strict_decode_command(command, field=f"{field}.command")
    refs = payload.get("source_refs")
    if not isinstance(refs, list):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field}.source_refs must be a list.",
            details={"field": field},
        )
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict):
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                f"{field}.source_refs[{index}] must be an object.",
            )
        _require_exact_keys(
            ref,
            SOURCE_REF_EXACT_KEYS,
            field=f"{field}.source_refs[{index}]",
        )
    try:
        proposal = ActionProposal.from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} failed ActionProposal construction: {exc}",
            details={"field": field},
        ) from exc
    _require_canonical_export_equal(payload, proposal.to_dict(), field=field)
    return proposal


def _strict_decode_plan(payload: object, *, field: str) -> ActionPlan:
    if not isinstance(payload, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} must be an ActionPlan object.",
            details={"field": field},
        )
    _require_exact_keys(payload, PLAN_EXACT_KEYS, field=field)
    if payload.get("schema") != ACTION_PLAN_SCHEMA:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field}.schema must be {ACTION_PLAN_SCHEMA!r}.",
            details={"field": field},
        )
    candidates_raw = payload.get("candidates")
    if not isinstance(candidates_raw, list):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field}.candidates must be a list.",
        )
    candidates = tuple(
        _strict_decode_proposal(item, field=f"{field}.candidates[{index}]")
        for index, item in enumerate(candidates_raw)
    )
    contributions_raw = payload.get("contributions")
    if not isinstance(contributions_raw, list):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field}.contributions must be a list.",
        )
    contributions: list[PlanContribution] = []
    for index, item in enumerate(contributions_raw):
        if not isinstance(item, dict):
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                f"{field}.contributions[{index}] must be an object.",
            )
        _require_exact_keys(
            item,
            CONTRIBUTION_EXACT_KEYS,
            field=f"{field}.contributions[{index}]",
        )
        contributions.append(
            PlanContribution(
                proposal_id=str(item["proposal_id"]),
                plugin_id=str(item["plugin_id"]),
                weight=float(item["weight"]),
                role=str(item["role"]),
            )
        )
    try:
        plan = ActionPlan(
            frame_id=str(payload["frame_id"]),
            timestamp_ms=payload["timestamp_ms"],
            status=str(payload["status"]),
            candidates=candidates,
            selected_proposal_id=payload.get("selected_proposal_id"),
            contributions=tuple(contributions),
            selector_id=str(payload.get("selector_id") or SELECTOR_ID),
            metadata=dict(payload.get("metadata") or {}),
            plan_id=str(payload.get("plan_id") or ""),
            schema=str(payload.get("schema") or ACTION_PLAN_SCHEMA),
        )
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} failed ActionPlan construction: {exc}",
            details={"field": field},
        ) from exc
    _require_canonical_export_equal(payload, plan.to_dict(), field=field)
    return plan


def _strict_decode_authority(payload: object, *, field: str) -> ShadowAuthorityResult:
    if not isinstance(payload, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} must be a ShadowAuthorityResult object.",
            details={"field": field},
        )
    _require_exact_keys(payload, AUTHORITY_EXACT_KEYS, field=field)
    if payload.get("schema") != SHADOW_AUTHORITY_RESULT_SCHEMA:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field}.schema must be {SHADOW_AUTHORITY_RESULT_SCHEMA!r}.",
            details={"field": field},
        )
    if payload.get("proposed_applied") is not False:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field}.proposed_applied must be false.",
            details={"field": field},
        )
    if "applied_control" in payload:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} must not include applied_control.",
            details={"field": field},
        )
    # authorized_output must be the exact idle command before reconstruction.
    _require_canonical_export_equal(
        payload.get("authorized_output"),
        authorized_idle_output(),
        field=f"{field}.authorized_output",
    )
    proposed_payload = payload.get("proposed")
    proposed: ProposedVehicleCommand | None
    if proposed_payload is None:
        proposed = None
    else:
        proposed = _strict_decode_command(
            proposed_payload, field=f"{field}.proposed"
        )
    host = _strict_decode_envelope(
        payload.get("host_application"),
        field=f"{field}.host_application",
    )
    try:
        authority = ShadowAuthorityResult(
            frame_id=str(payload["frame_id"]),
            proposed=proposed,
            cycle_status=str(payload["cycle_status"]),
            cycle_reason=str(payload.get("cycle_reason") or ""),
            host_application=host,
            drive_mode_gate=str(payload.get("drive_mode_gate") or "unknown"),
            authority_mode=str(payload.get("authority_mode") or ""),
            proposed_applied=False,
            schema=str(payload.get("schema") or SHADOW_AUTHORITY_RESULT_SCHEMA),
        )
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} failed ShadowAuthorityResult construction: {exc}",
            details={"field": field},
        ) from exc
    _require_canonical_export_equal(payload, authority.to_dict(), field=field)
    return authority


def _strict_decode_source_envelope(
    payload: object,
    *,
    component: str,
    field: str,
) -> ComponentEnvelope:
    """Decode one source envelope, hydrating observation/memory typed values."""

    if not isinstance(payload, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} must be a ComponentEnvelope object.",
            details={"field": field},
        )
    _require_exact_keys(payload, ENVELOPE_EXACT_KEYS, field=field)
    status = payload.get("status")
    value = payload.get("value")
    reason = payload.get("reason") or ""
    updated_at_ms = payload.get("updated_at_ms") or 0
    if status == "ready":
        if component == "observation":
            if not isinstance(value, dict):
                raise DecisionSurfaceError(
                    "latest_frame_invalid",
                    f"{field}.value must be an Observation export object when ready.",
                    details={"field": field},
                )
            try:
                typed = Observation.from_dict(value)
            except (TypeError, ValueError) as exc:
                raise DecisionSurfaceError(
                    "latest_frame_invalid",
                    f"{field}.value failed Observation construction: {exc}",
                    details={"field": field},
                ) from exc
            _require_canonical_export_equal(
                value, typed.to_dict(), field=f"{field}.value"
            )
            value = typed
        elif component == "memory":
            if not isinstance(value, dict):
                raise DecisionSurfaceError(
                    "latest_frame_invalid",
                    f"{field}.value must be a MemorySnapshot export object when ready.",
                    details={"field": field},
                )
            try:
                typed = MemorySnapshot.from_dict(value)
            except (TypeError, ValueError) as exc:
                raise DecisionSurfaceError(
                    "latest_frame_invalid",
                    f"{field}.value failed MemorySnapshot construction: {exc}",
                    details={"field": field},
                ) from exc
            _require_canonical_export_equal(
                value, typed.to_dict(), field=f"{field}.value"
            )
            value = typed
    try:
        envelope = ComponentEnvelope(
            status=status,
            value=value,
            reason=reason,
            updated_at_ms=updated_at_ms,
        )
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} failed ComponentEnvelope construction: {exc}",
            details={"field": field},
        ) from exc
    # Compare against the original export shape (dict values, not typed objects).
    _require_canonical_export_equal(payload, envelope.to_dict(), field=field)
    return envelope


def _strict_decode_source(payload: object, *, field: str) -> DecisionDataSource:
    if not isinstance(payload, dict):
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} must be a DecisionDataSource object.",
            details={"field": field},
        )
    _require_exact_keys(payload, SOURCE_EXACT_KEYS, field=field)
    if payload.get("schema") != DECISION_DATA_SOURCE_SCHEMA:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field}.schema must be {DECISION_DATA_SOURCE_SCHEMA!r}.",
            details={"field": field},
        )
    envelopes: dict[str, ComponentEnvelope] = {}
    for env_key in (
        "observation",
        "memory",
        "patterns",
        "projections",
        "capabilities",
        "prior_host_applied_command",
    ):
        envelopes[env_key] = _strict_decode_source_envelope(
            payload.get(env_key),
            component=env_key,
            field=f"{field}.{env_key}",
        )
    try:
        source = DecisionDataSource(
            frame_id=str(payload["frame_id"]),
            frame_index=payload["frame_index"],
            timestamp_ms=payload["timestamp_ms"],
            observation=envelopes["observation"],
            memory=envelopes["memory"],
            patterns=envelopes["patterns"],
            projections=envelopes["projections"],
            capabilities=envelopes["capabilities"],
            prior_host_applied_command=envelopes["prior_host_applied_command"],
            metadata=dict(payload.get("metadata") or {}),
            schema=str(payload.get("schema") or DECISION_DATA_SOURCE_SCHEMA),
            source_id=str(payload.get("source_id") or ""),
        )
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"{field} failed DecisionDataSource construction: {exc}",
            details={"field": field},
        ) from exc
    _require_canonical_export_equal(payload, source.to_dict(), field=field)
    return source


def _require_exact_cycle_export(cycle: dict[str, Any]) -> ShadowDecisionCycleResult:
    """Strict reconstruction + full canonical export equality for the cycle.

    One owning boundary: reconstruct typed PR #74 objects (authority, plan,
    proposals/commands, source) and require lossless ``to_dict()`` equality.
    Adjacent nested authority/command/mode tampering is rejected as a class.
    Returns the reconstructed cycle for aggregate alignment checks.
    """

    _require_exact_keys(cycle, CYCLE_EXACT_KEYS, field="cycle")
    if cycle.get("schema") != SHADOW_DECISION_CYCLE_RESULT_SCHEMA:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"cycle.schema must be {SHADOW_DECISION_CYCLE_RESULT_SCHEMA!r}.",
            details={"field": "cycle.schema"},
        )
    if cycle.get("status") not in {"ok", "engine_error"}:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "cycle.status must be ok or engine_error.",
        )
    if type(cycle.get("reason")) is not str:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "cycle.reason must be a string.",
        )

    authority = _strict_decode_authority(cycle.get("authority"), field="cycle.authority")
    plan_payload = cycle.get("plan")
    plan: ActionPlan | None
    if plan_payload is None:
        plan = None
    else:
        plan = _strict_decode_plan(plan_payload, field="cycle.plan")
    source_payload = cycle.get("source")
    source: DecisionDataSource | None
    if source_payload is None:
        source = None
    else:
        source = _strict_decode_source(source_payload, field="cycle.source")

    try:
        reconstructed = ShadowDecisionCycleResult(
            frame_id=str(cycle["frame_id"]),
            status=str(cycle["status"]),
            reason=str(cycle.get("reason") or ""),
            source=source,
            plan=plan,
            authority=authority,
            schema=str(cycle.get("schema") or SHADOW_DECISION_CYCLE_RESULT_SCHEMA),
        )
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"cycle failed ShadowDecisionCycleResult construction: {exc}",
            details={"field": "cycle"},
        ) from exc
    _require_canonical_export_equal(cycle, reconstructed.to_dict(), field="cycle")
    return reconstructed


def _require_aggregate_cycle_alignment(
    frame: dict[str, Any],
    cycle: ShadowDecisionCycleResult,
) -> None:
    """Enforce cross-object cycle alignment the nested constructors do not own.

    - cycle / authority / plan / source share one frame_id
    - plan/source timestamps agree when both present
    - stream envelope frame_index/timestamp_ms come from the cycle source
    - authority.proposed is the selected plan command (or null for idle/error)
    """

    frame_id = cycle.frame_id
    if frame.get("frame_id") != frame_id:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "stream frame_id must equal cycle.frame_id.",
            details={"field": "frame_id"},
        )
    if cycle.authority.frame_id != frame_id:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "authority.frame_id must equal cycle.frame_id.",
            details={"field": "cycle.authority.frame_id"},
        )

    plan = cycle.plan
    source = cycle.source
    if plan is not None and plan.frame_id != frame_id:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "plan.frame_id must equal cycle.frame_id.",
            details={"field": "cycle.plan.frame_id"},
        )
    if source is not None and source.frame_id != frame_id:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "source.frame_id must equal cycle.frame_id.",
            details={"field": "cycle.source.frame_id"},
        )
    if plan is not None and source is not None:
        if plan.timestamp_ms != source.timestamp_ms:
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                "plan.timestamp_ms must equal source.timestamp_ms.",
                details={"field": "cycle.plan.timestamp_ms"},
            )

    # Stream envelope timing identity is derived from the cycle source.
    expected_index = source.frame_index if source is not None else 0
    expected_ts = source.timestamp_ms if source is not None else 0
    if frame.get("frame_index") != expected_index:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "stream frame_index must match cycle source frame_index.",
            details={
                "field": "frame_index",
                "expected": expected_index,
                "got": frame.get("frame_index"),
            },
        )
    if frame.get("timestamp_ms") != expected_ts:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "stream timestamp_ms must match cycle source timestamp_ms.",
            details={
                "field": "timestamp_ms",
                "expected": expected_ts,
                "got": frame.get("timestamp_ms"),
            },
        )

    # authority.proposed must be the selected plan command (detached equal value).
    proposed = cycle.authority.proposed
    if cycle.status == "engine_error" or plan is None or plan.status == "idle":
        if proposed is not None:
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                "authority.proposed must be null when plan is idle/absent or cycle is engine_error.",
                details={"field": "cycle.authority.proposed"},
            )
        return

    selected = plan.selected_candidate()
    if selected is None:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "selected plan is missing the selected candidate.",
            details={"field": "cycle.plan.selected_proposal_id"},
        )
    selected_command = selected.command
    if selected_command is None:
        if proposed is not None:
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                "authority.proposed must be null when the selected candidate has no command.",
                details={"field": "cycle.authority.proposed"},
            )
        return
    if proposed is None:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "authority.proposed must equal the selected plan command.",
            details={"field": "cycle.authority.proposed"},
        )
    try:
        if canonical_json_utf8(_json_ready(proposed.to_dict())) != canonical_json_utf8(
            _json_ready(selected_command.to_dict())
        ):
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                "authority.proposed must equal the selected plan command.",
                details={"field": "cycle.authority.proposed"},
            )
    except ValueError as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"authority.proposed/selected command are not strictly JSON-serializable: {exc}",
            details={"field": "cycle.authority.proposed"},
        ) from exc


def _require_runner_plan_alignment(
    cycle: ShadowDecisionCycleResult,
    config: ShadowProposalsConfig,
) -> None:
    """Enforce runner-owned candidate membership and selector output."""

    if cycle.status == "engine_error":
        if cycle.plan is not None:
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                "engine_error cycle must not include an action plan.",
                details={"field": "cycle.plan"},
            )
        return

    plan = cycle.plan
    if plan is None:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "ok cycle must include an action plan.",
            details={"field": "cycle.plan"},
        )
    expected_plugins = tuple(sorted(config.enabled_plugins))
    actual_plugins = tuple(candidate.plugin_id for candidate in plan.candidates)
    if actual_plugins != expected_plugins:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            "cycle.plan candidates must match activation enabled_plugins exactly.",
            details={
                "field": "cycle.plan.candidates",
                "expected_plugins": list(expected_plugins),
                "got_plugins": list(actual_plugins),
            },
        )
    try:
        expected = select_action_plan(
            frame_id=plan.frame_id,
            timestamp_ms=plan.timestamp_ms,
            candidates=plan.candidates,
            metadata=plan.to_dict()["metadata"],
        )
    except (TypeError, ValueError) as exc:
        raise DecisionSurfaceError(
            "latest_frame_invalid",
            f"cycle.plan failed deterministic selector validation: {exc}",
            details={"field": "cycle.plan"},
        ) from exc
    _require_canonical_export_equal(
        plan.to_dict(),
        expected.to_dict(),
        field="cycle.plan.selector_result",
    )


def _require_stream_summaries_match_cycle(
    frame: dict[str, Any],
    cycle: dict[str, Any],
) -> None:
    """Rebuild summaries from cycle and require canonical equality (check #10)."""

    plan = cycle.get("plan") if isinstance(cycle.get("plan"), dict) else None
    authority = cycle.get("authority") if isinstance(cycle.get("authority"), dict) else {}
    source = cycle.get("source") if isinstance(cycle.get("source"), dict) else None
    expected = {
        "observation_summary": _observation_summary(source),
        "memory_summary": _memory_summary(source),
        "plan_summary": _plan_summary(plan),
        "authority_summary": _authority_summary(authority, cycle),
        "view": {
            "view_id": COMBINED_VIEW_ID,
            "applied_false_emphasized": True,
        },
    }
    for key, rebuilt in expected.items():
        actual = frame.get(key)
        try:
            if canonical_json_utf8(_json_ready(actual)) != canonical_json_utf8(
                _json_ready(rebuilt)
            ):
                raise DecisionSurfaceError(
                    "latest_frame_invalid",
                    f"Latest decision frame {key} is not consistent with cycle.",
                    details={"field": key},
                )
        except ValueError as exc:
            raise DecisionSurfaceError(
                "latest_frame_invalid",
                f"Latest decision frame {key} is not strictly JSON-serializable: {exc}",
                details={"field": key},
            ) from exc


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


def load_live_decision_activation(vehicle_runtime_dir: Path | str) -> dict[str, Any] | None:
    """Read the current on-disk decision activation, or None if unusable."""

    try:
        bundle = controller_bundle_paths(Path(vehicle_runtime_dir))
        activation_path = Path(bundle["decision_runtime_dir"]) / "active.json"
        vehicle_id = Path(vehicle_runtime_dir).name
        payload = _read_surface_activation(
            activation_path,
            vehicle_id=vehicle_id,
        )
    except (
        FileNotFoundError,
        OSError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        DecisionSurfaceError,
    ):
        return None
    return payload


def publish_shadow_decision_frame(
    *,
    cycle_result: Any | None,
    context_frame_id: str,
    vehicle_id: str,
    vehicle_runtime_dir: Path | str,
    run_id: str,
    worker_pid: int,
    activation: dict[str, Any] | None = None,
    staged_engine_id: str | None = None,
) -> bool:
    """Publish generation-scoped latest frame. Returns True when written.

    The worker must pass the activation generation under which its engine was
    constructed. Live on-disk activation is re-read and must still match that
    generation (engine_id + activated_at_ms). Restage while running leaves the
    invalidated latest file untouched until the worker reloads/restarts.
    """

    if cycle_result is None:
        return False
    if staged_engine_id is not None and staged_engine_id != ENGINE_ID:
        return False
    if not isinstance(activation, dict):
        return False
    worker_decision = activation.get("decision")
    if not isinstance(worker_decision, dict) or worker_decision.get("engine_id") != ENGINE_ID:
        return False
    worker_activated_at = activation.get("activated_at_ms")
    if type(worker_activated_at) is not int:
        return False

    frame_id = getattr(cycle_result, "frame_id", None)
    if frame_id is None and isinstance(cycle_result, dict):
        frame_id = cycle_result.get("frame_id")
    if frame_id != context_frame_id:
        return False

    live = load_live_decision_activation(vehicle_runtime_dir)
    if live is None:
        return False
    live_decision = live.get("decision")
    if not isinstance(live_decision, dict) or live_decision.get("engine_id") != ENGINE_ID:
        return False
    live_activated_at = live.get("activated_at_ms")
    if type(live_activated_at) is not int:
        return False
    # Refuse to relabel a generation-A worker cycle as generation B.
    if live_activated_at != worker_activated_at:
        return False
    if live_decision.get("engine_id") != worker_decision.get("engine_id"):
        return False

    frame = build_decision_stream_frame(
        cycle_result,
        vehicle_id=vehicle_id,
        run_id=run_id,
        worker_pid=worker_pid,
        activation_engine_id=ENGINE_ID,
        activation_activated_at_ms=worker_activated_at,
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
        return _read_surface_activation(
            activation_path,
            vehicle_id=vehicle_id,
        )

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
                text = json.dumps(frame, sort_keys=True) if json_output else _format_stream_frame(frame)
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
        f"Observation: status={obs.get('status')} frame_id={obs.get('frame_id')} "
        f"reason={obs.get('reason')}",
        "Observation image: unavailable (no image path published in stream frame)",
        f"Memory: status={mem.get('status')} health={mem.get('health')} "
        f"records={mem.get('record_count')}",
        f"Plan: status={plan.get('status')} selected={plan.get('selected_proposal_id')}",
    ]
    records = mem.get("records") if isinstance(mem.get("records"), list) else []
    if records:
        for record in records:
            lines.append(
                "Retained: "
                f"record_id={record.get('record_id')} kind={record.get('kind')} "
                f"confidence={record.get('confidence')} frame_id={record.get('frame_id')} "
                f"observation_id={record.get('observation_id')}"
            )
    else:
        lines.append("Retained: (none)")

    selected_refs: list[Any] = []
    for cand in plan.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        lines.append(
            "Candidate: "
            f"plugin={cand.get('plugin_id')} proposal={cand.get('proposal_id')} "
            f"lifecycle={cand.get('lifecycle')} freshness={cand.get('freshness')} "
            f"confidence={cand.get('confidence')} reason={cand.get('reason')} "
            f"command={json.dumps(cand.get('command'), sort_keys=True)} "
            f"source_refs={json.dumps(cand.get('source_refs') or [], sort_keys=True)}"
        )
        if cand.get("proposal_id") == plan.get("selected_proposal_id"):
            selected_refs = cand.get("source_refs") or []
    if not plan.get("candidates"):
        lines.append("Candidate: (none)")
    contributions = (
        plan.get("contributions")
        if isinstance(plan.get("contributions"), list)
        else []
    )
    contribution_plugins = [
        item.get("plugin_id")
        for item in contributions
        if isinstance(item, dict)
    ]
    lines.append(
        "Selected contribution plugins: "
        + (", ".join(str(item) for item in contribution_plugins) or "(none)")
    )
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
    activation = _read_surface_activation(
        activation_path,
        vehicle_id=vehicle_id,
    )
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
            f"decision apply requires engine_id={ENGINE_ID!r}; got {decision.get('engine_id')!r}.",
            vehicle_id=vehicle_id,
        )
    engine_config = decision.get("engine_config")
    if not isinstance(engine_config, dict):
        raise DecisionSurfaceError(
            "activation_invalid",
            "Decision activation engine_config must be an object.",
            vehicle_id=vehicle_id,
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
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    nonce = secrets.token_hex(3)
    final_dir = output_root / f"{safe_path_part(vehicle_id)}-{stamp}-{nonce}"
    partial_dir = output_root / f".{safe_path_part(vehicle_id)}-{stamp}-{nonce}.partial"
    partial_owned = False
    final_owned = False
    try:
        output_root.mkdir(parents=True, exist_ok=True)
        if final_dir.exists() or partial_dir.exists():
            raise DecisionSurfaceError(
                "record_write_failed",
                f"Record directory already exists: {display_path(final_dir)}",
                vehicle_id=vehicle_id,
            )
        partial_dir.mkdir(parents=True, exist_ok=False)
        partial_owned = True
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
            manifest_image_rel = None
            html_image_rel = None
            src = from_run_dir / "frames" / f"{frame_id}.png"
            source_frames_root = from_run_dir / "frames"
            _require_no_source_path_symlinks(src, vehicle_id=vehicle_id)
            if src.is_file():
                resolved = src.resolve()
                base = source_frames_root.resolve()
                run_root = from_run_dir.resolve()
                if base.parent != run_root:
                    raise DecisionSurfaceError(
                        "run_invalid",
                        f"Source frames directory escapes from-run directory: {source_frames_root}",
                        vehicle_id=vehicle_id,
                    )
                if resolved.parent != base:
                    raise DecisionSurfaceError(
                        "run_invalid",
                        f"Source image path escapes from-run frames dir: {src}",
                        vehicle_id=vehicle_id,
                    )
                dest = source_frames_dir / f"{frame_id}.png"
                shutil.copyfile(resolved, dest)
                manifest_image_rel = f"source_frames/{frame_id}.png"
                html_image_rel = f"../source_frames/{frame_id}.png"
            html_body = render_decision_exact_frame_html(
                vehicle_id=vehicle_id,
                frame_id=frame_id,
                cycle_result=cycle_result,
                source_image_rel=html_image_rel,
            )
            (frames_dir / html_name).write_text(html_body, encoding="utf-8")
            frame_entries.append(
                {
                    "frame_id": frame_id,
                    "html": f"frames/{html_name}",
                    "source_image": manifest_image_rel,
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

        recorded_payload = dict(payload)
        recorded_payload["recorded"] = True
        recorded_payload["record_dir"] = display_path(final_dir)
        (partial_dir / "result.json").write_text(
            json.dumps(recorded_payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        total = _directory_byte_size(partial_dir)
        if total > DECISION_APPLY_MAX_RECORD_BYTES:
            raise DecisionSurfaceError(
                "record_bounds_exceeded",
                f"Record artifacts are {total} bytes; max is {DECISION_APPLY_MAX_RECORD_BYTES}.",
                vehicle_id=vehicle_id,
            )

        # Publication is the final operation: readers never observe a partial tree.
        partial_dir.rename(final_dir)
        partial_owned = False
        final_owned = True
        return final_dir
    except DecisionSurfaceError as exc:
        owned_paths = [
            path
            for path, owned in (
                (partial_dir, partial_owned),
                (final_dir, final_owned),
            )
            if owned
        ]
        cleanup_errors = _cleanup_record_paths(*owned_paths)
        if cleanup_errors:
            raise DecisionSurfaceError(
                exc.error,
                f"{exc.message_text} Cleanup also failed: {'; '.join(cleanup_errors)}",
                vehicle_id=vehicle_id,
                details={
                    **exc.details,
                    "original_error": exc.message_text,
                    "cleanup_errors": cleanup_errors,
                },
            ) from exc
        raise
    except Exception as exc:  # noqa: BLE001
        owned_paths = [
            path
            for path, owned in (
                (partial_dir, partial_owned),
                (final_dir, final_owned),
            )
            if owned
        ]
        cleanup_errors = _cleanup_record_paths(*owned_paths)
        cleanup_text = (
            f" Cleanup also failed: {'; '.join(cleanup_errors)}"
            if cleanup_errors
            else ""
        )
        raise DecisionSurfaceError(
            "record_write_failed",
            f"Could not write decision apply record: {type(exc).__name__}: {exc}."
            f"{cleanup_text}",
            vehicle_id=vehicle_id,
            details={
                "original_error": f"{type(exc).__name__}: {exc}",
                "cleanup_errors": cleanup_errors,
            },
        ) from exc


def _require_no_source_path_symlinks(
    source_path: Path,
    *,
    vehicle_id: str,
) -> None:
    """Reject symlinks in every user-controlled lexical source component.

    macOS exposes root-level namespace aliases such as ``/var -> /private/var``.
    Normalize only that platform-level alias; every deeper component from the
    supplied path remains subject to the no-symlink record contract.
    """

    lexical = source_path if source_path.is_absolute() else Path.cwd() / source_path
    root = Path(lexical.anchor)
    for candidate in reversed((lexical, *lexical.parents[:-1])):
        if candidate.is_symlink() and candidate.parent != root:
            raise DecisionSurfaceError(
                "run_invalid",
                f"Source image path must not use symlink component {candidate}: "
                f"{source_path}",
                vehicle_id=vehicle_id,
            )


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
    contribution_plugins = [
        item.get("plugin_id")
        for item in plan_summary.get("contributions") or []
        if isinstance(item, dict)
    ]
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
    <p>contribution_plugins={esc(
        ", ".join(str(item) for item in contribution_plugins) or "(none)"
    )}</p>
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
    """Measure every record artifact node; fail closed on any lstat error."""

    total = 0
    for artifact in path.rglob("*"):
        try:
            measured = artifact.lstat()
        except OSError as exc:
            raise OSError(
                f"could not measure record artifact {artifact}: {exc}"
            ) from exc
        if stat_mod.S_ISREG(measured.st_mode) or stat_mod.S_ISLNK(measured.st_mode):
            total += measured.st_size
    return total


def _remove_tree_strict(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _cleanup_record_paths(*paths: Path) -> list[str]:
    errors: list[str] = []
    for path in paths:
        try:
            _remove_tree_strict(path)
        except Exception as exc:  # noqa: BLE001 - preserve both failure causes
            errors.append(f"{display_path(path)}: {type(exc).__name__}: {exc}")
    return errors


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
