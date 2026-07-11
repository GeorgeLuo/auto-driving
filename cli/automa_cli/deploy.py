from __future__ import annotations

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
from .paths import display_path, safe_path_part
from .perception import ensure_vehicle_perception_activation
from .vehicles import discover_active_vehicles, find_vehicle_by_id


ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = ROOT / "deploy" / "targets" / "donkeycar"
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
release_id = sys.argv[7]

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
    verbose: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    stream = output
    target, error = _resolve_physical_target(
        vehicle_id=vehicle_id,
        timeout_s=timeout_s,
        ssh_target=ssh_target,
        pi_home=pi_home,
        skip_discovery=skip_discovery,
        output=stream,
        operation="core deploy",
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
        donkeycar_source_dir=vendor_source_dir,
    )
    if dry_run:
        vendor_manifest = load_donkeycar_vendor_manifest()
        restart_prefix = f"DRIVE_ARGS={shlex.quote(drive_args)} " if restart and drive_args is not None else ""
        return CommandResult(
            0,
            "\n".join(
                [
                    f"Core update dry run for {vehicle_id} -> {target.ssh_target}",
                    f"would ensure DonkeyCar vendor source: {vendor_manifest['source']['repo_url']} @ {vendor_manifest['source']['revision']}",
                    f"vendor checkout: {vendor_source_dir}",
                    *[f"$ {shlex.join(command)}" for command in commands],
                    *(
                        [
                            f"$ PI_HOST={shlex.quote(target.ssh_target)} "
                            f"{restart_prefix}scripts/deploy/donkeycar/restart_drive.sh"
                        ]
                        if restart
                        else []
                    ),
                ],
            ),
        )

    try:
        _emit(stream, "==> Ensure DonkeyCar vendor source")
        ensure_donkeycar_vendor(output=stream, verbose=verbose)
    except Exception as exc:
        return CommandResult(2, f"Could not prepare DonkeyCar vendor source: {exc}")

    steps = [
        ("Prepare remote directories", commands[0], None),
        ("Sync DonkeyCar vendor source", commands[1], None),
        ("Sync mycar app harness", commands[2], None),
    ]
    for label, command, env in steps:
        code = _run_step(label, command, env=env, verbose=verbose, output=stream)
        if code != 0:
            return CommandResult(code, f"{label} failed with exit code {code}.")

    _emit(stream, "Core deploy bundle synced.")

    if restart:
        code = _restart_drive_server(
            target=target,
            drive_args=drive_args,
            verbose=verbose,
            output=stream,
        )
        if code != 0:
            return CommandResult(code, f"Restart failed with exit code {code}.")
        _emit(stream, "Drive server restarted.")

    return CommandResult(0, "")


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
        )
        payload = _autonomy_update_payload(
            target=target,
            dry_run=True,
            source_summary=source_summary,
            release=None,
            release_id=release_id,
            commands=commands,
            restart=restart,
            perception_algorithm="current",
            decision_engine="idle",
            runtime_verification=None,
        )
        if json_output:
            return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
        return CommandResult(0, _format_autonomy_dry_run(payload))

    release = sync_controller_bundle(bundle, output=output)
    perception_activation_path = ensure_vehicle_perception_activation(
        vehicle=dict(target.vehicle),
        algorithm="current",
        bundle=bundle,
        release=release,
    )
    decision_activation_path = ensure_vehicle_decision_activation(
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
    )
    commands = _autonomy_sync_commands(
        target=target,
        release_id=release_id,
        archive_path=Path(release["archive"]["path"]),
        archive_sha256=str(release["archive"]["sha256"]),
        perception_activation_path=deploy_files["perception"],
        decision_activation_path=deploy_files["decision"],
    )

    for label, command in commands:
        code = _run_step(label, command, env=None, verbose=verbose, output=output)
        if code != 0:
            return CommandResult(code, f"{label} failed with exit code {code}.")

    runtime_verification: dict[str, Any] | None = None
    if restart:
        code = _restart_drive_server(
            target=target,
            drive_args=drive_args,
            verbose=verbose,
            output=output,
        )
        if code != 0:
            return CommandResult(code, f"Restart failed with exit code {code}.")

    perception_manifest = json.loads(perception_activation_path.read_text(encoding="utf-8"))
    decision_manifest = json.loads(decision_activation_path.read_text(encoding="utf-8"))
    if restart:
        try:
            runtime_verification = _verify_physical_autonomy_runtime(
                target=target,
                expected_engine_spec=str(decision_manifest["decision"]["engine_spec"]),
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
        perception_algorithm=str(perception_manifest["perception"]["algorithm"]),
        decision_engine=str(decision_manifest["decision"]["engine_id"]),
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
            include_chase_sim=True,
        )
        found_vehicle, error = find_vehicle_by_id(payload, vehicle_id)
        if error:
            return None, CommandResult(2, error)
        if found_vehicle is None:
            return None, CommandResult(2, f"Vehicle {vehicle_id!r} was not found.")
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

    perception_path = deploy_dir / "perception-active.json"
    decision_path = deploy_dir / "decision-active.json"
    perception_path.write_text(json.dumps(perception, indent=2, sort_keys=True), encoding="utf-8")
    decision_path.write_text(json.dumps(decision, indent=2, sort_keys=True), encoding="utf-8")
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
                },
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return {"perception": perception_path, "decision": decision_path}


def _autonomy_sync_commands(
    *,
    target: PhysicalTarget,
    release_id: str,
    archive_path: Path,
    archive_sha256: str,
    perception_activation_path: Path,
    decision_activation_path: Path,
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
    perception_algorithm: str,
    decision_engine: str,
    runtime_verification: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema": "vehicle_autonomy_update_v0",
        "vehicle_id": target.vehicle_id,
        "dry_run": dry_run,
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
        },
        "restart_requested": restart,
        "runtime_verification": runtime_verification,
        "commands": [
            {"step": label, "command": _display_deploy_command(label, command)}
            for label, command in commands
        ],
    }


def _format_autonomy_dry_run(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"Autonomy update dry run for {payload['vehicle_id']} -> {payload['target']['ssh_target']}",
            f"source tree SHA-256: {payload['source']['tree_sha256']}",
            f"source files: {payload['source']['file_count']}",
            "would activate perception=current decision=idle when no prior activation exists",
            *[f"$ {entry['command']}" for entry in payload["commands"]],
            f"would restart drive runtime: {'yes' if payload['restart_requested'] else 'no'}",
        ]
    )


def _display_deploy_command(label: str, command: list[str]) -> str:
    if label == "Verify and activate autonomy release":
        return f"ssh {shlex.quote(command[1])} <verify-and-activate-controller-release>"
    return shlex.join(command)


def _restart_drive_server(
    *,
    target: PhysicalTarget,
    drive_args: str | None,
    verbose: bool,
    output: TextIO | None,
) -> int:
    restart_env = {
        **os.environ,
        "PI_HOST": target.ssh_target,
        "PI_HOME": target.pi_home,
    }
    if drive_args is not None:
        restart_env["DRIVE_ARGS"] = drive_args
    restart_command = [str(ROOT / "scripts" / "deploy" / "donkeycar" / "restart_drive.sh")]
    command_prefix = f"PI_HOST={shlex.quote(target.ssh_target)} PI_HOME={shlex.quote(target.pi_home)}"
    if drive_args is not None:
        command_prefix += f" DRIVE_ARGS={shlex.quote(drive_args)}"
    return _run_step(
        "Restart Donkey drive server",
        restart_command,
        env=restart_env,
        command_prefix=command_prefix,
        verbose=verbose,
        output=output,
    )


def _verify_physical_autonomy_runtime(
    *,
    target: PhysicalTarget,
    expected_engine_spec: str,
    timeout_s: float,
) -> dict[str, Any]:
    connection = target.vehicle.get("connection")
    base_url = connection.get("base_url") if isinstance(connection, dict) else None
    if not isinstance(base_url, str) or not base_url:
        host = target.ssh_target.rsplit("@", 1)[-1]
        base_url = f"http://{host}:8887"
    status_url = f"{base_url.rstrip('/')}/autonomy/status"
    try:
        with urllib_request.urlopen(status_url, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib_error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"GET {status_url} failed: {exc}") from exc

    autonomy = payload.get("autonomy")
    actual_engine = autonomy.get("engine") if isinstance(autonomy, dict) else None
    drive_mode = payload.get("drive_mode")
    if payload.get("ok") is not True:
        raise RuntimeError(f"{status_url} did not report an available autonomy manager")
    if actual_engine != expected_engine_spec:
        raise RuntimeError(
            f"{status_url} reported engine {actual_engine!r}, expected {expected_engine_spec!r}"
        )
    if drive_mode != "user":
        raise RuntimeError(
            f"{status_url} reported drive mode {drive_mode!r}; expected 'user' for idle smoke verification"
        )
    return {
        "status_url": status_url,
        "engine": actual_engine,
        "drive_mode": drive_mode,
        "ok": True,
    }


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


def _core_sync_commands(*, ssh_target: str, pi_home: str, donkeycar_source_dir: Path) -> list[list[str]]:
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
    ]
