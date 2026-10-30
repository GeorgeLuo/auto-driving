from __future__ import annotations

import json
import os
import shutil
from typing import Any, TextIO

from autonomy.decision_cycle.activation import DECISION_STEPS, STEPS, activation_presets

from ...shared.command_result import CommandResult
from ..runs.service import stop_host_automation
from ..bundles import controller_bundle_source_summary, sync_controller_bundle
from ...shared.paths import RUNTIME_ROOT, display_path, safe_path_part
from ...shared.io.processes import pid_alive
from ..step_activations import (
    decision_generation_id,
    ensure_step_activations,
    host_bundle,
    read_bundle_activation,
    step_lines,
    staging_host,
    startup_activations,
    write_host_configuration,
)
from ..publications.host_publications import host_state
from ..host_status import host_block
from ...releases.errors import RuntimeHostError
from ...releases.store import release_code_dir
from ..discovery import running_base_url
from ..local import ensure_chase_host, host_record, stop_chase_host
from ..staging import automation_dir, staged_host
from ...vehicles.probes import Candidate
from ...vehicles.providers import PROVIDERS
from .picar_deploy import (
    DEFAULT_PI_HOME,
    DONKEY_READY_TIMEOUT_S,
    deploy_picar_release,
    donkey_service_control_command,
    install_picar_core,
    resolve_picar_target,
    run_step,
    ssh_target_from_vehicle,
)


def update_host(
    *, host_name: str, dry_run: bool = False, restart: bool = False,
    drive_args: str | None = None, json_output: bool = False,
    verbose: bool = False, output: TextIO | None = None,
) -> CommandResult:
    configuration, error = staging_host(host_name, runtime_root=RUNTIME_ROOT)
    if error:
        return CommandResult(2, error)
    if drive_args is not None and not restart:
        return CommandResult(2, "--drive-args requires --restart so the new arguments take effect.")
    if PROVIDERS[configuration["provider"]].local_host:
        if drive_args is not None:
            return CommandResult(2, "--drive-args applies only to a PiCar.")
        return _update_local_autonomy(host_name=host_name, dry_run=dry_run, restart=restart,
                                      json_output=json_output, output=output)
    return deploy_picar_release(
        resolve_picar_target(configuration), dry_run=dry_run, restart=restart,
        drive_args=drive_args, json_output=json_output, verbose=verbose, output=output,
    )


def _update_local_autonomy(
    *,
    host_name: str,
    dry_run: bool,
    restart: bool,
    json_output: bool,
    output: TextIO | None,
) -> CommandResult:
    """Package a release for a locally hosted vehicle; ``restart`` moves its host onto it."""

    from autonomy.runtime.client import RuntimeClient

    bundle = host_bundle(host_name, RUNTIME_ROOT)
    source_summary = controller_bundle_source_summary()
    if dry_run:
        payload = {"host_name": host_name, "dry_run": True, "restart": restart, "source": source_summary}
        if json_output:
            return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
        return CommandResult(0, "\n".join([
            f"Dry run: would package {host_name}'s controller release "
            f"(tree {source_summary['tree_sha256'][:12]}, {source_summary['file_count']} files)",
            f"Restart the host onto it: {'yes' if restart else 'no'}",
        ]))
    try:
        startup = startup_activations(bundle, host_name)
    except ValueError as exc:
        return CommandResult(2, str(exc))
    try:
        configuration = staged_host(host_name)
        release = sync_controller_bundle(bundle, output=output)
        ensure_step_activations(host_name=host_name, bundle=bundle, release=release, activations=startup,
                                vehicle=configuration)
        _, summary = release_code_dir(host_name, release["archive"]["sha256"])
    except RuntimeHostError as exc:
        return CommandResult(2, str(exc))
    activations = {
        step: activation
        for step in STEPS
        if (activation := read_bundle_activation(bundle, step)) is not None
    }
    steps = {step: list(activation.plugins) for step, activation in activations.items()}
    record = host_record(host_name)
    restarted = False
    if restart and record is not None:
        try:
            RuntimeClient(record["base_url"], timeout_s=5.0).restart(timeout_s=DONKEY_READY_TIMEOUT_S)
        except (RuntimeError, TimeoutError) as exc:
            return CommandResult(2, f"Release {summary['archive']} was packaged, but the host did not restart: {exc}")
        restarted = True
    payload = {
        "host_name": host_name,
        "dry_run": False,
        "release": summary,
        "presets": activation_presets(activations),
        "steps": steps,
        "generation_id": decision_generation_id(
            {step: activations.get(step) for step in DECISION_STEPS}
        ),
        "restarted": restarted,
    }
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    if restarted:
        host_line = "Runtime restarted: yes"
    elif record is None:
        host_line = "Runtime restarted: no host running; the next run starts on this release"
    else:
        host_line = (
            "Runtime restarted: no; an idle host moves onto this release at the next run"
        )
    return CommandResult(0, "\n".join([
        f"Host updated: {host_name} (local host)",
        f"Release: {summary['archive']}",
        f"Archive SHA-256: {summary['archive_sha256']}",
        *step_lines(steps, payload["presets"]),
        f"Decision generation: {payload['generation_id']}",
        host_line,
    ]))


def create_host(
    *, host_name: str, provider: str, url: str,
    ssh_target: str | None = None, pi_home: str | None = None,
    drive_args: str | None = None, dry_run: bool = False, json_output: bool = False,
    verbose: bool = False, output: TextIO | None = None,
) -> CommandResult:
    if host_name in {".", ".."} or safe_path_part(host_name) != host_name or not host_name:
        return CommandResult(2, "Host names may contain letters, digits, dots, underscores and hyphens, excluding . and ..")
    if provider not in PROVIDERS:
        return CommandResult(2, f"Provider must be one of {', '.join(PROVIDERS)} (got {provider!r}).")
    vehicle = PROVIDERS[provider]
    local = vehicle.local_host
    directory = RUNTIME_ROOT / safe_path_part(host_name)
    if (directory / "host-configuration.json").exists():
        existing, error = staging_host(host_name, runtime_root=RUNTIME_ROOT)
        return CommandResult(2, error or f"Host {host_name!r} already exists: {_vehicle_label(existing)}.")
    if local and any(value is not None for value in (ssh_target, pi_home, drive_args)):
        return CommandResult(2, "--ssh-target, --pi-home and --drive-args apply only to a PiCar.")
    try:
        url = vehicle.normalize_url(url)
    except ValueError as exc:
        return CommandResult(2, str(exc))
    configuration = {"host_name": host_name, "provider": provider, "vehicle_kind": vehicle.vehicle_kind,
                     "connection": {vehicle.url_key: url}}
    if local:
        if not dry_run:
            probe = vehicle.probe(Candidate(provider, url, "cli"), timeout_s=5.0)
            if not vehicle.can_take_host(probe):
                return CommandResult(2, f"No {vehicle.machine} can take a host at {url}: {probe.error}")
    else:
        configuration["connection"].update(
            ssh_target=ssh_target or os.environ.get("PI_HOST") or ssh_target_from_vehicle(configuration),
            pi_home=pi_home or os.environ.get("PI_HOME") or DEFAULT_PI_HOME,
        )
    if dry_run:
        if local:
            return _update_local_autonomy(host_name=host_name, dry_run=True, restart=True,
                                          json_output=json_output, output=output)
        target = resolve_picar_target(configuration)
        core = install_picar_core(target, dry_run=True, json_output=json_output)
        release = deploy_picar_release(target, dry_run=True, restart=True, drive_args=drive_args,
                                       json_output=json_output, verbose=verbose, output=output)
        if json_output:
            return CommandResult(0, json.dumps({"configuration": configuration, "dry_run": True,
                "core": json.loads(core.message), "host": json.loads(release.message)}, indent=2))
        return CommandResult(0, core.message + "\n" + release.message)
    made_directory = not directory.exists()
    write_host_configuration(RUNTIME_ROOT, configuration)
    try:
        if not local:
            core = install_picar_core(resolve_picar_target(configuration), verbose=verbose, output=output)
            if core.exit_code:
                raise RuntimeError(core.message)
        result = update_host(host_name=host_name, restart=not local, drive_args=drive_args,
                             json_output=json_output, verbose=verbose, output=output)
        if result.exit_code:
            raise RuntimeError(result.message)
        state = None
        if local:
            base_url = ensure_chase_host(host_name)
            state = host_state(base_url, host_name=host_name, timeout_s=5.0)
        if json_output:
            return CommandResult(0, json.dumps({"configuration": configuration, "result": json.loads(result.message),
                                                "host": state}, indent=2))
        detail = "\n".join(line for line in result.message.splitlines()
                           if not line.startswith(("Host updated:", "Runtime restarted:")))
        lines = [f"Host {host_name} created for {_vehicle_label(configuration)}", detail]
        if state:
            lines.append(host_block({**state, "host_name": host_name, "attached": True,
                                     "provider": provider, "vehicle_url": vehicle.operator_url(url)}))
        return CommandResult(0, "\n".join(lines))
    except (OSError, RuntimeError, ValueError) as exc:
        if local:
            stop_chase_host(host_name)
        if made_directory:
            shutil.rmtree(directory)
        else:
            (directory / "host-configuration.json").unlink(missing_ok=True)
        suffix = "" if local else " The Pi keeps files already synced."
        return CommandResult(2, f"Could not create host {host_name!r}: {exc}{suffix}")


def _vehicle_label(configuration: dict[str, Any]) -> str:
    vehicle = PROVIDERS[configuration["provider"]]
    return f"the {vehicle.label} at {vehicle.operator_url(configuration['connection'][vehicle.url_key])}"


def delete_host(*, host_name: str, verbose: bool = False, output: TextIO | None = None) -> CommandResult:
    configuration, error = staging_host(host_name, runtime_root=RUNTIME_ROOT)
    if error:
        return CommandResult(2, error)
    directory = RUNTIME_ROOT / safe_path_part(host_name)
    # Stop an active local run before stopping its host. An idle Pi may have no HTTP listener.
    process = automation_dir(host_name) / "process.json"
    base_url = running_base_url(host_name)
    state = host_state(base_url, host_name=host_name, timeout_s=1.0) if base_url else {}
    try:
        process_record = json.loads(process.read_text())
    except (OSError, ValueError):
        process_record = {}
    active_run = (isinstance(process_record, dict) and pid_alive(process_record.get("pid"))) or state.get("state") in {"starting", "running", "stopping"}
    if active_run:
        result = stop_host_automation(host_name=host_name)
        if result.exit_code:
            return CommandResult(result.exit_code, result.message)
    local = PROVIDERS[configuration["provider"]].local_host
    if local:
        stop_chase_host(host_name)
    else:
        target = resolve_picar_target(configuration)
        command = donkey_service_control_command(pi_home=target.pi_home, action="stop", drive_args=None)
        code = run_step("Stop Donkey runtime service", ["ssh", target.ssh_target, command],
                         env=None, verbose=verbose, output=output)
        if code:
            return CommandResult(code, "Could not stop the PiCar host; its local record was kept.")
    shutil.rmtree(directory)
    suffix = "" if local else " Harness files stay in ~/mycar on the PiCar."
    stopped = "Stopped the run. " if active_run else ""
    return CommandResult(0, f"{stopped}Stopped host {host_name}. Removed {display_path(directory)}.{suffix}")
