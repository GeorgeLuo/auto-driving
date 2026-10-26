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
    host_name: str,
    *,
    pid: int,
    manifest_bundle_root: Path | None = None,
) -> RuntimeFixturePaths:
    """Write one explicit staged-runtime fixture and return its document paths."""
    write_host_fixture(runtime_root, host_name)
    bundle_root = runtime_root / host_name / "bundle"
    activation_bundle_root = manifest_bundle_root or bundle_root
    perception_activation = bundle_root / "runtime" / "perception" / "active.json"
    automation_process = bundle_root / "runtime" / "automation" / "process.json"
    automation_state = bundle_root / "runtime" / "automation" / "state.json"

    # A controller release is what makes a staged bundle deployable.
    write_json(
        bundle_root / "releases" / "latest-controller-bundle.json",
        {"schema": "automa_controller_bundle_manifest_v0", "tree_sha256": "fixture"},
    )
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
            "schema": "automa_automation_process",
            "host_name": host_name,
            "pid": pid,
            "log_to_disk": False,
            "log_path": None,
            "command": [str(AUTOMA_PATH), "hosts", "run", "--name", host_name],
        },
    )
    write_json(
        automation_state,
        {
            "schema": "automa_automation_run_state",
            "host_name": host_name,
            "run_id": "test-run",
            "status": "running",
            "pid": pid,
            "processed_count": 3,
            "num_decisions": 0,
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


def write_host_fixture(runtime_root: Path, host_name: str, *, provider: str = "chase-sim",
                       url: str | None = None) -> None:
    from cli.automa_cli.step_activations import write_host_configuration

    if (runtime_root / host_name / "host-configuration.json").exists():
        return
    connection = ({"ws_url": url or "ws://localhost:5050/ws/control"} if provider == "chase-sim" else
                  {"base_url": url or "http://piracer.local:8887", "ssh_target": "piracer@piracer.local",
                   "pi_home": "/home/piracer"})
    write_host_configuration(runtime_root, {"host_name": host_name, "provider": provider,
        "vehicle_kind": "chase-sim-ws" if provider == "chase-sim" else "picar-donkey-http",
        "connection": connection})


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
