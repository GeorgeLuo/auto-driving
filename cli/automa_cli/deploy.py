from __future__ import annotations

import base64
import copy
import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlparse

from implementations.perception.catalog import DEFAULT_PERCEPTION_ALGORITHM
from implementations.vehicle.picar.defaults import (
    get_default_local_car_base_url,
    get_default_local_car_id,
)

from .bundles import (
    controller_bundle_paths,
    controller_bundle_source_summary,
    release_activation_summary,
    sync_controller_bundle,
)
from .decision import ensure_vehicle_decision_activation
from .donkeycar_vendor import (
    donkeycar_vendor_source_dir,
    ensure_donkeycar_vendor,
    load_donkeycar_vendor_manifest,
)
from .memory import ensure_vehicle_memory_activation
from .paths import display_path, safe_path_part
from .perception import ensure_vehicle_perception_activation
from .vehicles import discover_active_vehicles, find_vehicle_by_id
from implementations.memory import DEFAULT_MEMORY_IMPLEMENTATION


ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = ROOT / "deploy" / "targets" / "donkeycar"
DONKEY_SERVICE_NAME = "automa-donkey.service"
DONKEY_SERVICE_DIR = DEPLOY_DIR / "systemd"
DONKEY_READY_TIMEOUT_S = 30.0
DEFAULT_PI_HOME = "/home/piracer"
DEFAULT_PI_USER = "piracer"
RUNTIME_ROOT = Path(os.environ.get("AUTOMA_RUNTIME_ROOT", ROOT / "runtime" / "vehicles"))

_REMOTE_AUTONOMY_INSTALL_SCRIPT = r"""
import hashlib
import json
import os
import shutil
import sys
import tarfile
import time
from pathlib import Path

archive = Path(sys.argv[1])
release_root = Path(sys.argv[2])
app_root = Path(sys.argv[3])
expected_sha256 = sys.argv[4]
perception_source = Path(sys.argv[5])
decision_source = Path(sys.argv[6])
memory_source = Path(sys.argv[7])
release_id = sys.argv[8]

digest = hashlib.sha256()
with archive.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
actual_sha256 = digest.hexdigest()
if actual_sha256 != expected_sha256:
    raise RuntimeError(
        f"archive SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}"
    )

pending_root = release_root.with_name(f".{release_root.name}.pending")
if pending_root.exists():
    shutil.rmtree(pending_root)
pending_root.mkdir(parents=True)
pending_resolved = pending_root.resolve()
with tarfile.open(archive, "r:gz") as bundle:
    for member in bundle.getmembers():
        if member.issym() or member.islnk():
            raise RuntimeError(f"controller archive contains a link: {member.name}")
        target = (pending_root / member.name).resolve()
        if target != pending_resolved and pending_resolved not in target.parents:
            raise RuntimeError(f"controller archive member escapes release: {member.name}")
    bundle.extractall(pending_root)

for required in ("autonomy", "implementations", "bundle-manifest.json"):
    if not (pending_root / required).exists():
        raise RuntimeError(f"controller archive is missing {required}")

release_root.parent.mkdir(parents=True, exist_ok=True)
if release_root.exists():
    shutil.rmtree(release_root)
pending_root.replace(release_root)

backup_root = app_root / "runtime" / "controller-backups" / str(int(time.time() * 1000))
for package_name in ("autonomy", "implementations"):
    package_target = release_root / package_name
    package_link = app_root / package_name
    next_link = app_root / f".{package_name}.{release_id}.next"
    if next_link.exists() or next_link.is_symlink():
        next_link.unlink()
    next_link.symlink_to(os.path.relpath(package_target, app_root), target_is_directory=True)
    if package_link.exists() and not package_link.is_symlink():
        backup_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(package_link), str(backup_root / package_name))
    os.replace(next_link, package_link)

activation_targets = (
    (perception_source, app_root / "runtime" / "perception" / "active.json"),
    (decision_source, app_root / "runtime" / "decision" / "active.json"),
    (memory_source, app_root / "runtime" / "memory" / "active.json"),
)
for source, target in activation_targets:
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload.get("schema"), str):
        raise RuntimeError(f"activation has no schema: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = target.with_name(f".{target.name}.{release_id}.pending")
    shutil.copy2(source, pending)
    os.replace(pending, target)

status_path = app_root / "runtime" / "controller-release.json"
status_pending = status_path.with_name(f".{status_path.name}.pending")
status_pending.write_text(
    json.dumps(
        {
            "schema": "automa_installed_controller_release_v0",
            "release_id": release_id,
            "archive_sha256": actual_sha256,
            "release_root": str(release_root),
            "activated_at_ms": int(time.time() * 1000),
        },
        indent=2,
        sort_keys=True,
    ),
    encoding="utf-8",
)
os.replace(status_pending, status_path)
print(status_path.read_text(encoding="utf-8"))
""".strip()


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


@dataclass(frozen=True)
class PhysicalTarget:
    vehicle_id: str
    vehicle: dict[str, object]
    provider: str
    ssh_target: str
    pi_home: str


def update_vehicle_core(
    *,
    vehicle_id: str,
    timeout_s: float = 1.0,
    ssh_target: str | None = None,
    pi_home: str | None = None,
    skip_discovery: bool = False,
    dry_run: bool = False,
    restart: bool = False,
    drive_args: str | None = None,
    json_output: bool = False,
    verbose: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    stream = output
    if drive_args is not None and not restart:
        return CommandResult(2, "--drive-args requires --restart so the new arguments take effect.")
    target, error = _resolve_physical_target(
        vehicle_id=vehicle_id,
        timeout_s=timeout_s,
        ssh_target=ssh_target,
        pi_home=pi_home,
        skip_discovery=skip_discovery,
        output=stream,
        operation="core deploy",
        allow_offline_default=True,
    )
    if error is not None:
        return error
    assert target is not None

    _emit(stream, f"Selected {vehicle_id} ({target.provider}) at {target.ssh_target}.")
    _emit(stream, "Scope: core Donkey/harness bundle only.")
    _emit(stream, f"Remote home: {target.pi_home}")

    vendor_source_dir = donkeycar_vendor_source_dir()
    commands = _core_sync_commands(
        ssh_target=target.ssh_target,
        pi_home=target.pi_home,
        pi_user=_ssh_user_from_target(target.ssh_target),
        donkeycar_source_dir=vendor_source_dir,
    )
    vendor_manifest = load_donkeycar_vendor_manifest()
    if dry_run:
        payload = _core_update_payload(
            target=target,
            dry_run=True,
            vendor_manifest=vendor_manifest,
            vendor_source_dir=vendor_source_dir,
            commands=commands,
            restart=restart,
            drive_args=drive_args,
            runtime_readiness=None,
        )
        if json_output:
            return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
        return CommandResult(0, _format_core_dry_run(payload))

    try:
        _emit(stream, "==> Ensure DonkeyCar vendor source")
        ensure_donkeycar_vendor(output=stream, verbose=verbose)
    except Exception as exc:
        return CommandResult(2, f"Could not prepare DonkeyCar vendor source: {exc}")

    steps = [
        ("Prepare remote directories", commands[0], None),
        ("Sync DonkeyCar vendor source", commands[1], None),
        ("Sync mycar app harness", commands[2], None),
        ("Sync Donkey runtime service", commands[3], None),
        ("Install and enable Donkey runtime service", commands[4], None),
    ]
    for label, command, env in steps:
        code = _run_step(label, command, env=env, verbose=verbose, output=stream)
        if code != 0:
            return CommandResult(code, f"{label} failed with exit code {code}.")

    _emit(stream, "Core deploy bundle synced.")

    if restart:
        code = _restart_drive_service(
            target=target,
            drive_args=drive_args,
            verbose=verbose,
            output=stream,
        )
        if code != 0:
            return CommandResult(code, f"Restart failed with exit code {code}.")
        _emit(stream, "Donkey runtime service restarted.")
        readiness = {
            "ok": True,
            "status_url": f"{_physical_base_url(target)}/autonomy/status",
            "drive_mode": "user",
        }
    else:
        try:
            readiness = _wait_for_donkey_readiness(
                target=target,
                timeout_s=DONKEY_READY_TIMEOUT_S,
            )
        except RuntimeError as exc:
            return CommandResult(
                2,
                "\n".join(
                    [
                        f"{DONKEY_SERVICE_NAME} was installed and enabled, but HTTP readiness failed: {exc}",
                        (
                            f"Inspect it with: ssh {target.ssh_target} "
                            f"sudo systemctl status {DONKEY_SERVICE_NAME}"
                        ),
                    ]
                ),
            )
    _emit(stream, f"Donkey runtime ready: {readiness['status_url']}")

    if json_output:
        payload = _core_update_payload(
            target=target,
            dry_run=False,
            vendor_manifest=vendor_manifest,
            vendor_source_dir=vendor_source_dir,
            commands=commands,
            restart=restart,
            drive_args=drive_args,
            runtime_readiness=readiness,
        )
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(
        0,
        "\n".join(
            [
                f"Core updated: {vehicle_id} -> {target.ssh_target}",
                f"Service: {DONKEY_SERVICE_NAME} (enabled and active)",
                f"Readiness: {readiness['status_url']}",
                f"Runtime restarted: {'yes' if restart else 'only if it was not already active'}",
            ]
        ),
    )


def update_vehicle_autonomy(
    *,
    vehicle_id: str,
    timeout_s: float = 1.0,
    ssh_target: str | None = None,
    pi_home: str | None = None,
    skip_discovery: bool = False,
    dry_run: bool = False,
    restart: bool = False,
    drive_args: str | None = None,
    json_output: bool = False,
    verbose: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    if drive_args is not None and not restart:
        return CommandResult(2, "--drive-args requires --restart so the new arguments take effect.")
    target, error = _resolve_physical_target(
        vehicle_id=vehicle_id,
        timeout_s=timeout_s,
        ssh_target=ssh_target,
        pi_home=pi_home,
        skip_discovery=skip_discovery,
        output=output,
        operation="autonomy deploy",
    )
    if error is not None:
        return error
    assert target is not None

    _emit(output, f"Selected {vehicle_id} ({target.provider}) at {target.ssh_target}.")
    _emit(output, "Scope: versioned autonomy controller bundle and activation metadata only.")
    _emit(output, f"Remote home: {target.pi_home}")

    vehicle_runtime_dir = RUNTIME_ROOT / safe_path_part(vehicle_id)
    bundle = controller_bundle_paths(vehicle_runtime_dir)
    source_summary = controller_bundle_source_summary()

    if dry_run:
        release_id = f"autonomy-{source_summary['tree_sha256'][:12]}-preview"
        preview_dir = vehicle_runtime_dir / "deploy" / "donkeycar" / release_id
        preview_archive = Path(bundle["root_dir"]) / "releases" / f"{release_id}.tar.gz"
        commands = _autonomy_sync_commands(
            target=target,
            release_id=release_id,
            archive_path=preview_archive,
            archive_sha256="<archive-sha256>",
            perception_activation_path=preview_dir / "perception-active.json",
            decision_activation_path=preview_dir / "decision-active.json",
            memory_activation_path=preview_dir / "memory-active.json",
        )
        payload = _autonomy_update_payload(
            target=target,
            dry_run=True,
            source_summary=source_summary,
            release=None,
            release_id=release_id,
            commands=commands,
            restart=restart,
            drive_args=drive_args,
            perception_algorithm=DEFAULT_PERCEPTION_ALGORITHM,
            decision_engine="idle",
            memory_implementation=DEFAULT_MEMORY_IMPLEMENTATION,
            runtime_verification=None,
        )
        if json_output:
            return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
        return CommandResult(0, _format_autonomy_dry_run(payload))

    release = sync_controller_bundle(bundle, output=output)
    perception_activation_path = ensure_vehicle_perception_activation(
        vehicle=dict(target.vehicle),
        algorithm=DEFAULT_PERCEPTION_ALGORITHM,
        bundle=bundle,
        release=release,
    )
    decision_activation_path = ensure_vehicle_decision_activation(
        vehicle_id=vehicle_id,
        bundle=bundle,
        release=release,
    )
    memory_activation_path = ensure_vehicle_memory_activation(
        vehicle_id=vehicle_id,
        bundle=bundle,
        release=release,
    )
    release_id = Path(release["archive"]["path"]).name.removesuffix(".tar.gz")
    deploy_files = _write_remote_activation_files(
        target=target,
        vehicle_runtime_dir=vehicle_runtime_dir,
        release=release,
        release_id=release_id,
        perception_activation_path=perception_activation_path,
        decision_activation_path=decision_activation_path,
        memory_activation_path=memory_activation_path,
    )
    commands = _autonomy_sync_commands(
        target=target,
        release_id=release_id,
        archive_path=Path(release["archive"]["path"]),
        archive_sha256=str(release["archive"]["sha256"]),
        perception_activation_path=deploy_files["perception"],
        decision_activation_path=deploy_files["decision"],
        memory_activation_path=deploy_files["memory"],
    )

    for label, command in commands:
        code = _run_step(label, command, env=None, verbose=verbose, output=output)
        if code != 0:
            return CommandResult(code, f"{label} failed with exit code {code}.")

    runtime_verification: dict[str, Any] | None = None
    if restart:
        code = _restart_drive_service(
            target=target,
            drive_args=drive_args,
            verbose=verbose,
            output=output,
        )
        if code != 0:
            return CommandResult(code, f"Restart failed with exit code {code}.")

    perception_manifest = json.loads(perception_activation_path.read_text(encoding="utf-8"))
    decision_manifest = json.loads(decision_activation_path.read_text(encoding="utf-8"))
    memory_manifest = json.loads(memory_activation_path.read_text(encoding="utf-8"))
    if restart:
        try:
            runtime_verification = _verify_physical_autonomy_runtime(
                target=target,
                expected_engine_spec=str(decision_manifest["decision"]["engine_spec"]),
                expected_perception_algorithm=str(perception_manifest["perception"]["algorithm"]),
                timeout_s=max(3.0, timeout_s),
            )
        except RuntimeError as exc:
            return CommandResult(
                2,
                "\n".join(
                    [
                        f"Autonomy release {release_id} was installed and restart was attempted,",
                        f"but runtime verification failed: {exc}",
                    ]
                ),
            )
    payload = _autonomy_update_payload(
        target=target,
        dry_run=False,
        source_summary=source_summary,
        release=release,
        release_id=release_id,
        commands=commands,
        restart=restart,
        drive_args=drive_args,
        perception_algorithm=str(perception_manifest["perception"]["algorithm"]),
        decision_engine=str(decision_manifest["decision"]["engine_id"]),
        memory_implementation=str(memory_manifest["memory"]["implementation_id"]),
        runtime_verification=runtime_verification,
    )
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(
        0,
        "\n".join(
            [
                f"Autonomy updated: {vehicle_id} -> {target.ssh_target}",
                f"Release: {release_id}",
                f"Tree SHA-256: {release['tree_sha256']}",
                f"Perception: {payload['activation']['perception_algorithm']}",
                f"Decision: {payload['activation']['decision_engine']}",
                f"Memory: {payload['activation']['memory_implementation']}",
                f"Runtime restarted: {'yes' if restart else 'no'}",
                *(
                    ["Runtime verified: selected engine loaded; drive mode remains manual"]
                    if runtime_verification is not None
                    else []
                ),
            ]
        ),
    )


def _resolve_physical_target(
    *,
    vehicle_id: str,
    timeout_s: float,
    ssh_target: str | None,
    pi_home: str | None,
    skip_discovery: bool,
    output: TextIO | None,
    operation: str,
    allow_offline_default: bool = False,
) -> tuple[PhysicalTarget | None, CommandResult | None]:
    vehicle: dict[str, object] = {
        "vehicle_id": vehicle_id,
        "vehicle_kind": "picar",
        "provider": "picar",
        "connection": {},
    }

    if skip_discovery:
        _emit(output, f"Skipping active vehicle discovery for id {vehicle_id!r}.")
    else:
        _emit(output, f"Discovering active vehicles for id {vehicle_id!r}...")
        payload = discover_active_vehicles(
            timeout_s=timeout_s,
            include_picar=True,
            include_chase_sim=False,
        )
        found_vehicle, error = find_vehicle_by_id(payload, vehicle_id)
        if error:
            if allow_offline_default and vehicle_id == get_default_local_car_id():
                default_base_url = get_default_local_car_base_url()
                vehicle = {
                    "vehicle_id": vehicle_id,
                    "vehicle_kind": "picar",
                    "provider": "picar",
                    "connection": {
                        "base_url": default_base_url,
                        "source": "configured-default",
                    },
                }
                _emit(
                    output,
                    (
                        "Donkey HTTP readiness is unavailable; using the configured physical "
                        f"target {default_base_url}. SSH will determine deploy reachability."
                    ),
                )
            else:
                return None, CommandResult(2, error)
        elif found_vehicle is None:
            return None, CommandResult(2, f"Vehicle {vehicle_id!r} was not found.")
        else:
            vehicle = found_vehicle

    provider = str(vehicle.get("provider"))
    if provider != "picar":
        return None, CommandResult(
            2,
            f"Vehicle {vehicle_id!r} is provider {provider!r}; {operation} is only supported for physical PiCar targets.",
        )

    resolved_home = pi_home or os.environ.get("PI_HOME") or DEFAULT_PI_HOME
    resolved_ssh_target = ssh_target or os.environ.get("PI_HOST") or _ssh_target_from_vehicle(vehicle)
    if not resolved_ssh_target:
        return None, CommandResult(
            2,
            f"Could not derive SSH target for vehicle {vehicle_id!r}; pass --ssh-target or set PI_HOST.",
        )

    return (
        PhysicalTarget(
            vehicle_id=vehicle_id,
            vehicle=vehicle,
            provider=provider,
            ssh_target=resolved_ssh_target,
            pi_home=resolved_home,
        ),
        None,
    )


def _write_remote_activation_files(
    *,
    target: PhysicalTarget,
    vehicle_runtime_dir: Path,
    release: dict[str, Any],
    release_id: str,
    perception_activation_path: Path,
    decision_activation_path: Path,
    memory_activation_path: Path,
) -> dict[str, Path]:
    deploy_dir = vehicle_runtime_dir / "deploy" / "donkeycar" / release_id
    deploy_dir.mkdir(parents=True, exist_ok=True)
    remote_app_root = f"{target.pi_home}/mycar"
    remote_release_root = f"{remote_app_root}/runtime/controller-releases/{release_id}"
    remote_artifact_dir = f"{remote_app_root}/runtime/controller-artifacts/{release_id}"
    remote_release = release_activation_summary(release)
    remote_release["archive"] = f"{remote_artifact_dir}/{Path(release['archive']['path']).name}"
    remote_release["manifest"] = f"{remote_release_root}/bundle-manifest.json"

    remote_bundle = {
        "root_dir": remote_app_root,
        "autonomy_dir": f"{remote_app_root}/autonomy",
        "implementations_dir": f"{remote_app_root}/implementations",
        "perception_dir": f"{remote_app_root}/implementations/perception",
        "decision_dir": f"{remote_app_root}/implementations/decision",
        "runtime_dir": f"{remote_app_root}/runtime",
        "perception_runtime_dir": f"{remote_app_root}/runtime/perception",
        "decision_runtime_dir": f"{remote_app_root}/runtime/decision",
        "memory_runtime_dir": f"{remote_app_root}/runtime/memory",
        "release": remote_release,
    }

    perception = copy.deepcopy(json.loads(perception_activation_path.read_text(encoding="utf-8")))
    perception["provider"] = target.provider
    perception["runtime"] = {
        "kind": "onboard_controller",
        "connection": target.vehicle.get("connection"),
    }
    perception["controller_bundle"] = copy.deepcopy(remote_bundle)
    perception["perception"]["source_dir"] = remote_bundle["perception_dir"]

    decision = copy.deepcopy(json.loads(decision_activation_path.read_text(encoding="utf-8")))
    decision["controller_bundle"] = copy.deepcopy(remote_bundle)

    memory = copy.deepcopy(json.loads(memory_activation_path.read_text(encoding="utf-8")))
    memory["controller_bundle"] = copy.deepcopy(remote_bundle)

    perception_path = deploy_dir / "perception-active.json"
    decision_path = deploy_dir / "decision-active.json"
    memory_path = deploy_dir / "memory-active.json"
    perception_path.write_text(json.dumps(perception, indent=2, sort_keys=True), encoding="utf-8")
    decision_path.write_text(json.dumps(decision, indent=2, sort_keys=True), encoding="utf-8")
    memory_path.write_text(json.dumps(memory, indent=2, sort_keys=True), encoding="utf-8")
    (deploy_dir / "deployment.json").write_text(
        json.dumps(
            {
                "schema": "automa_physical_autonomy_deployment_v0",
                "created_at_ms": int(time.time() * 1000),
                "vehicle_id": target.vehicle_id,
                "ssh_target": target.ssh_target,
                "release_id": release_id,
                "release": remote_release,
                "activation": {
                    "perception": str(perception_path),
                    "decision": str(decision_path),
                    "memory": str(memory_path),
                },
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return {
        "perception": perception_path,
        "decision": decision_path,
        "memory": memory_path,
    }


def _autonomy_sync_commands(
    *,
    target: PhysicalTarget,
    release_id: str,
    archive_path: Path,
    archive_sha256: str,
    perception_activation_path: Path,
    decision_activation_path: Path,
    memory_activation_path: Path,
) -> list[tuple[str, list[str]]]:
    remote_app_root = f"{target.pi_home}/mycar"
    remote_runtime = f"{remote_app_root}/runtime"
    remote_artifact_dir = f"{remote_runtime}/controller-artifacts/{release_id}"
    remote_release_root = f"{remote_runtime}/controller-releases/{release_id}"
    remote_archive = f"{remote_artifact_dir}/{archive_path.name}"
    install_command = shlex.join(
        [
            "python3",
            "-c",
            _REMOTE_AUTONOMY_INSTALL_SCRIPT,
            remote_archive,
            remote_release_root,
            remote_app_root,
            archive_sha256,
            f"{remote_artifact_dir}/{perception_activation_path.name}",
            f"{remote_artifact_dir}/{decision_activation_path.name}",
            f"{remote_artifact_dir}/{memory_activation_path.name}",
            release_id,
        ]
    )
    return [
        (
            "Prepare remote autonomy release directories",
            [
                "ssh",
                target.ssh_target,
                "mkdir",
                "-p",
                remote_artifact_dir,
                f"{remote_runtime}/controller-releases",
                f"{remote_runtime}/perception",
                f"{remote_runtime}/decision",
                f"{remote_runtime}/memory",
            ],
        ),
        (
            "Transfer autonomy release and activation metadata",
            [
                "rsync",
                "-az",
                str(archive_path),
                str(perception_activation_path),
                str(decision_activation_path),
                str(memory_activation_path),
                f"{target.ssh_target}:{remote_artifact_dir}/",
            ],
        ),
        (
            "Verify and activate autonomy release",
            ["ssh", target.ssh_target, install_command],
        ),
    ]


def _autonomy_update_payload(
    *,
    target: PhysicalTarget,
    dry_run: bool,
    source_summary: dict[str, Any],
    release: dict[str, Any] | None,
    release_id: str,
    commands: list[tuple[str, list[str]]],
    restart: bool,
    drive_args: str | None,
    perception_algorithm: str,
    decision_engine: str,
    memory_implementation: str,
    runtime_verification: dict[str, Any] | None,
) -> dict[str, Any]:
    command_status = "planned" if dry_run else "completed"
    command_entries = [
        {
            "step": label,
            "command": _display_deploy_command(label, command),
            "status": command_status,
        }
        for label, command in commands
    ]
    if restart:
        command_entries.append(
            {
                "step": "Restart Donkey runtime service",
                "command": _display_restart_command(target, drive_args),
                "status": command_status,
            }
        )
    return {
        "schema": "vehicle_autonomy_update_v0",
        "vehicle_id": target.vehicle_id,
        "dry_run": dry_run,
        "scope": {
            "id": "autonomy",
            "description": "versioned autonomy controller bundle and activation metadata",
        },
        "target": {
            "provider": target.provider,
            "ssh_target": target.ssh_target,
            "pi_home": target.pi_home,
        },
        "source": source_summary,
        "release_id": release_id,
        "release": release_activation_summary(release) if release is not None else None,
        "activation": {
            "perception_algorithm": perception_algorithm,
            "decision_engine": decision_engine,
            "memory_implementation": memory_implementation,
        },
        "restart_requested": restart,
        "runtime_verification": runtime_verification,
        "result": _deployment_result(dry_run=dry_run),
        "commands": command_entries,
    }


def _core_update_payload(
    *,
    target: PhysicalTarget,
    dry_run: bool,
    vendor_manifest: dict[str, Any],
    vendor_source_dir: Path,
    commands: list[list[str]],
    restart: bool,
    drive_args: str | None,
    runtime_readiness: dict[str, Any] | None,
) -> dict[str, Any]:
    labels = (
        "Prepare remote directories",
        "Sync DonkeyCar vendor source",
        "Sync mycar app harness",
        "Sync Donkey runtime service",
        "Install and enable Donkey runtime service",
    )
    command_status = "planned" if dry_run else "completed"
    command_entries = [
        {
            "step": label,
            "command": shlex.join(command),
            "status": command_status,
        }
        for label, command in zip(labels, commands, strict=True)
    ]
    if restart:
        command_entries.append(
            {
                "step": "Restart Donkey runtime service",
                "command": _display_restart_command(target, drive_args),
                "status": command_status,
            }
        )
    return {
        "schema": "vehicle_core_update_v0",
        "vehicle_id": target.vehicle_id,
        "dry_run": dry_run,
        "scope": {
            "id": "core",
            "description": "DonkeyCar vendor source and mycar core harness",
            "excluded": ["autonomy", "implementations", "runtime"],
        },
        "target": {
            "provider": target.provider,
            "ssh_target": target.ssh_target,
            "pi_home": target.pi_home,
        },
        "source": {
            "vendor": {
                "repo_url": vendor_manifest["source"]["repo_url"],
                "revision": vendor_manifest["source"]["revision"],
                "checkout": display_path(vendor_source_dir),
            },
            "harness": display_path(DEPLOY_DIR / "app"),
            "service": {
                "name": DONKEY_SERVICE_NAME,
                "source": display_path(DONKEY_SERVICE_DIR),
                "boot_enabled": True,
            },
        },
        "restart_requested": restart,
        "runtime_readiness": runtime_readiness,
        "result": _deployment_result(dry_run=dry_run),
        "commands": command_entries,
    }


def _deployment_result(*, dry_run: bool) -> dict[str, Any]:
    if not dry_run:
        return {"status": "completed"}
    return {
        "status": "planned",
        "local_writes_performed": False,
        "remote_connection_attempted": False,
        "remote_writes_performed": False,
    }


def _format_core_dry_run(payload: dict[str, Any]) -> str:
    source = payload["source"]
    vendor = source["vendor"]
    return "\n".join(
        [
            f"Core update dry run for {payload['vehicle_id']}",
            (
                f"target: {payload['target']['provider']} at "
                f"{payload['target']['ssh_target']}; home={payload['target']['pi_home']}"
            ),
            f"scope: {payload['scope']['description']}",
            "outcome: plan only; no files written and no remote connection attempted",
            f"vendor: {vendor['repo_url']} @ {vendor['revision']}",
            f"vendor checkout: {vendor['checkout']}",
            f"harness: {source['harness']}",
            (
                f"service: {source['service']['name']} "
                f"from {source['service']['source']} (enabled at boot)"
            ),
            "planned commands:",
            *[f"- {entry['step']}: {entry['command']}" for entry in payload["commands"]],
            f"restart requested: {'yes' if payload['restart_requested'] else 'no'}",
        ]
    )


def _format_autonomy_dry_run(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"Autonomy update dry run for {payload['vehicle_id']}",
            (
                f"target: {payload['target']['provider']} at "
                f"{payload['target']['ssh_target']}; home={payload['target']['pi_home']}"
            ),
            f"scope: {payload['scope']['description']}",
            "outcome: plan only; no files written and no remote connection attempted",
            f"source tree SHA-256: {payload['source']['tree_sha256']}",
            f"source files: {payload['source']['file_count']}",
            (
                f"activation defaults: perception={payload['activation']['perception_algorithm']} "
                f"decision={payload['activation']['decision_engine']} "
                f"memory={payload['activation']['memory_implementation']}"
            ),
            "planned commands:",
            *[f"- {entry['step']}: {entry['command']}" for entry in payload["commands"]],
            f"restart requested: {'yes' if payload['restart_requested'] else 'no'}",
        ]
    )


def _display_deploy_command(label: str, command: list[str]) -> str:
    if label == "Verify and activate autonomy release":
        return f"ssh {shlex.quote(command[1])} <verify-and-activate-controller-release>"
    return shlex.join(command)


def _display_restart_command(target: PhysicalTarget, drive_args: str | None) -> str:
    remote_command = _donkey_service_control_command(
        pi_home=target.pi_home,
        action="restart",
        drive_args=drive_args,
    )
    return shlex.join(["ssh", target.ssh_target, remote_command])


def _restart_drive_service(
    *,
    target: PhysicalTarget,
    drive_args: str | None,
    verbose: bool,
    output: TextIO | None,
) -> int:
    remote_command = _donkey_service_control_command(
        pi_home=target.pi_home,
        action="restart",
        drive_args=drive_args,
    )
    code = _run_step(
        "Restart Donkey runtime service",
        ["ssh", target.ssh_target, remote_command],
        env=None,
        verbose=verbose,
        output=output,
    )
    if code != 0:
        return code
    try:
        readiness = _wait_for_donkey_readiness(
            target=target,
            timeout_s=DONKEY_READY_TIMEOUT_S,
        )
    except RuntimeError as exc:
        _emit(output, f"HTTP readiness failed: {exc}")
        return 2
    _emit(output, f"Donkey runtime ready: {readiness['status_url']}")
    return 0


def _verify_physical_autonomy_runtime(
    *,
    target: PhysicalTarget,
    expected_engine_spec: str,
    expected_perception_algorithm: str,
    timeout_s: float,
) -> dict[str, Any]:
    verification = inspect_physical_autonomy_runtime(
        base_url=_physical_base_url(target),
        timeout_s=timeout_s,
    )
    status_url = str(verification["status_url"])
    actual_engine = verification["engine"]
    actual_perception = verification["perception_algorithm"]
    drive_mode = verification["drive_mode"]
    if actual_engine != expected_engine_spec:
        raise RuntimeError(
            f"{status_url} reported engine {actual_engine!r}, expected {expected_engine_spec!r}"
        )
    if actual_perception != expected_perception_algorithm:
        raise RuntimeError(
            f"{status_url} reported perception {actual_perception!r}, "
            f"expected {expected_perception_algorithm!r}"
        )
    if drive_mode != "user":
        raise RuntimeError(
            f"{status_url} reported drive mode {drive_mode!r}; expected 'user' for idle smoke verification"
        )
    return verification


def inspect_physical_autonomy_runtime(
    *,
    base_url: str,
    timeout_s: float,
) -> dict[str, Any]:
    """Read the deployed autonomy status without changing vehicle state."""
    normalized_url = str(base_url).strip().rstrip("/")
    if not normalized_url:
        raise RuntimeError("Pi base URL is required for runtime inspection")
    status_url = f"{normalized_url}/autonomy/status"
    try:
        with urllib_request.urlopen(status_url, timeout=max(0.1, float(timeout_s))) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (
        OSError,
        urllib_error.URLError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        raise RuntimeError(f"GET {status_url} failed: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"{status_url} did not return a JSON object")
    if payload.get("ok") is not True:
        raise RuntimeError(f"{status_url} did not report an available autonomy manager")

    autonomy = payload.get("autonomy")
    if not isinstance(autonomy, dict):
        raise RuntimeError(f"{status_url} did not report autonomy runtime status")

    actual_engine = autonomy.get("engine")
    if not isinstance(actual_engine, str) or not actual_engine.strip():
        raise RuntimeError(f"{status_url} did not report a loaded decision engine")

    components = autonomy.get("components")
    perception = components.get("perception") if isinstance(components, dict) else None
    actual_perception = perception.get("algorithm") if isinstance(perception, dict) else None
    if not isinstance(actual_perception, str) or not actual_perception.strip():
        raise RuntimeError(f"{status_url} did not report an active perception algorithm")

    drive_mode = payload.get("drive_mode")
    return {
        "status_url": status_url,
        "engine": actual_engine.strip(),
        "perception_algorithm": actual_perception.strip(),
        "drive_mode": drive_mode,
        "ok": True,
    }


def _wait_for_donkey_readiness(
    *,
    target: PhysicalTarget,
    timeout_s: float,
) -> dict[str, Any]:
    base_url = _physical_base_url(target)
    status_url = f"{base_url.rstrip('/')}/autonomy/status"
    deadline = time.monotonic() + max(0.1, float(timeout_s))
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            with urllib_request.urlopen(status_url, timeout=1.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                last_error = "endpoint did not return a JSON object"
            elif payload.get("drive_mode") != "user":
                last_error = f"drive mode is {payload.get('drive_mode')!r}, expected 'user'"
            else:
                return {
                    "ok": True,
                    "status_url": status_url,
                    "drive_mode": "user",
                    "autonomy_available": payload.get("ok") is True,
                }
        except (
            OSError,
            urllib_error.URLError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"GET {status_url} was not ready within {timeout_s:g}s ({last_error})")


def _physical_base_url(target: PhysicalTarget) -> str:
    connection = target.vehicle.get("connection")
    base_url = connection.get("base_url") if isinstance(connection, dict) else None
    if isinstance(base_url, str) and base_url.strip():
        return base_url.strip().rstrip("/")
    host = target.ssh_target.rsplit("@", 1)[-1]
    return f"http://{host}:8887"


def _emit(output: TextIO | None, message: str) -> None:
    if output is None:
        return
    print(message, file=output, flush=True)


def _run_step(
    label: str,
    command: list[str],
    *,
    env: dict[str, str] | None,
    verbose: bool,
    output: TextIO | None,
    command_prefix: str | None = None,
) -> int:
    _emit(output, f"==> {label}")
    if verbose:
        prefix = f"{command_prefix} " if command_prefix else ""
        _emit(output, f"$ {prefix}{shlex.join(command)}")

    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        _emit(output, line.rstrip())
    return process.wait()


def _ssh_target_from_vehicle(vehicle: dict[str, object]) -> str | None:
    connection = vehicle.get("connection")
    if not isinstance(connection, dict):
        return None
    base_url = connection.get("base_url")
    if not isinstance(base_url, str):
        return None
    hostname = urlparse(base_url).hostname
    if not hostname:
        return None
    return f"{DEFAULT_PI_USER}@{hostname}"


def _ssh_user_from_target(ssh_target: str) -> str:
    if "@" not in ssh_target:
        return DEFAULT_PI_USER
    user = ssh_target.split("@", 1)[0].strip()
    return user or DEFAULT_PI_USER


def _drive_args_token(drive_args: str | None) -> str:
    if drive_args is None:
        return "-"
    encoded = base64.b64encode(drive_args.encode("utf-8")).decode("ascii")
    return f"b64:{encoded}"


def _donkey_service_control_command(
    *,
    pi_home: str,
    action: str,
    drive_args: str | None,
) -> str:
    return shlex.join(
        [
            f"{pi_home}/.config/automa/systemd/control.sh",
            pi_home,
            action,
            _drive_args_token(drive_args),
        ]
    )


def _core_sync_commands(
    *,
    ssh_target: str,
    pi_home: str,
    pi_user: str,
    donkeycar_source_dir: Path,
) -> list[list[str]]:
    common_excludes = [
        "--exclude=*.bak.*",
        "--exclude=__pycache__/",
        "--exclude=*.pyc",
        "--exclude=.DS_Store",
    ]
    return [
        [
            "ssh",
            ssh_target,
            "mkdir",
            "-p",
            f"{pi_home}/projects/donkeycar",
            f"{pi_home}/mycar",
            f"{pi_home}/.config/automa/systemd",
        ],
        [
            "rsync",
            "-az",
            "--delete",
            *common_excludes,
            f"{donkeycar_source_dir}/",
            f"{ssh_target}:{pi_home}/projects/donkeycar/",
        ],
        [
            "rsync",
            "-az",
            "--delete",
            *common_excludes,
            "--exclude=data/",
            "--exclude=logs/",
            "--exclude=*.pid",
            "--exclude=autonomy",
            "--exclude=implementations",
            "--exclude=runtime",
            f"{DEPLOY_DIR / 'app'}/",
            f"{ssh_target}:{pi_home}/mycar/",
        ],
        [
            "rsync",
            "-az",
            "--delete",
            *common_excludes,
            f"{DONKEY_SERVICE_DIR}/",
            f"{ssh_target}:{pi_home}/.config/automa/systemd/",
        ],
        [
            "ssh",
            ssh_target,
            shlex.join(
                [
                    f"{pi_home}/.config/automa/systemd/install.sh",
                    pi_home,
                    pi_user,
                    _drive_args_token(None),
                ]
            ),
        ],
    ]
