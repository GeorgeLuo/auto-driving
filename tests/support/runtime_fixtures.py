from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autonomy.decision_cycle.activation import step_activation
from implementations.decision_cycle.catalog import preset_activation


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
AUTOMA_PATH = WORKSPACE_ROOT / "cli" / "automa"


@dataclass(frozen=True)
class RuntimeFixturePaths:
    bundle_root: Path
    perception_activation: Path
    automation_process: Path
    automation_state: Path


def write_runtime_fixture(
    runtime_root: Path,
    vehicle_id: str,
    *,
    pid: int,
    manifest_bundle_root: Path | None = None,
) -> RuntimeFixturePaths:
    """Write one explicit staged-runtime fixture and return its document paths."""
    bundle_root = runtime_root / vehicle_id / "bundle"
    activation_bundle_root = manifest_bundle_root or bundle_root
    perception_activation = bundle_root / "runtime" / "perception" / "active.json"
    automation_process = bundle_root / "runtime" / "automation" / "process.json"
    automation_state = bundle_root / "runtime" / "automation" / "state.json"

    packaged = preset_activation("perception", "sim_debug")
    write_json(
        perception_activation,
        step_activation(
            "perception",
            packaged.plugins,
            packaged.plugin_specs,
            packaged.plugin_configs,
            metadata={
                **packaged.metadata,
                "controller_bundle": {"root_dir": str(activation_bundle_root)},
            },
        ).to_payload(),
    )
    write_json(
        automation_process,
        {
            "schema": "automa_automation_process_v0",
            "vehicle_id": vehicle_id,
            "pid": pid,
            "log_to_disk": False,
            "log_path": None,
            "command": [str(AUTOMA_PATH), "vehicles", "automation", "run", "--id", vehicle_id],
        },
    )
    write_json(
        automation_state,
        {
            "schema": "automa_automation_run_state_v0",
            "vehicle_id": vehicle_id,
            "run_id": "test-run",
            "status": "running",
            "pid": pid,
            "processed_count": 3,
            "max_frames": None,
            "interval_s": 1.0,
            "recording": False,
            "control_source": "external_ws",
            "action_policy": "cycle_idle",
            "last_frame": {
                "frame_id": "frame_000002",
                "things": 2,
                "confidence": 0.75,
                "perception_duration_ms": 12,
                "cycle_duration_ms": 25,
                "perception_completed_at_ms": 1000,
            },
        },
    )
    return RuntimeFixturePaths(
        bundle_root=bundle_root,
        perception_activation=perception_activation,
        automation_process=automation_process,
        automation_state=automation_state,
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
