"""Create, update, and delete a host.

These operations return a result and a receipt. The command formats the receipt
and chooses where progress lines are written. The provider's registered
operations perform the deployment; an unregistered operation fails before it
changes a host.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any

from autonomy.decision_cycle.activation import DECISION_STEPS, STEPS, activation_presets

from ...providers.registry import PROVIDERS
from ...providers.models import Candidate
from ..errors import RuntimeHostError
from ...releases.errors import ReleaseError
from ...releases.paths import bundle_paths, host_dir
from ...releases.store import release_code_dir
from ...shared.io.processes import pid_alive
from ..progress import ProgressSink
from ..bundles import controller_bundle_source_summary, sync_controller_bundle
from ..discovery import running_base_url
from ..host_formatting import vehicle_label
from ..local import ensure_chase_host, host_record, stop_chase_host
from ..publications.host_publications import host_state
from ..runs.service import stop_host_automation
from ..staging import automation_dir, staged_host
from ..step_activations import (
    decision_generation_id,
    ensure_step_activations,
    host_name_error,
    read_bundle_activation,
    staging_host,
    startup_activations,
    write_host_configuration,
)
from .models import CreateResult, DeleteResult, UpdateResult
from .picar_deploy import (
    DEFAULT_PI_HOME,
    DONKEY_READY_TIMEOUT_S,
    deploy_picar_release,
    donkey_service_control_command,
    harness_dir,
    install_picar_core,
    resolve_picar_target,
    run_step,
    ssh_target_from_vehicle,
)
from .registry import (
    DeploymentBackend,
    backend_for,
    register_backend,
    unsupported_operation,
)


def update_host(
    *, host_name: str, dry_run: bool = False, restart: bool = False,
    drive_args: str | None = None, verbose: bool = False, emit: ProgressSink | None = None,
) -> UpdateResult:
    configuration, error = staging_host(host_name)
    if error:
        return UpdateResult(2, "error", error)
    if drive_args is not None and not restart:
        return UpdateResult(2, "error", "--drive-args requires --restart so the new arguments take effect.")
    backend = backend_for(configuration["provider"])
    if backend is None or backend.update is None:
        return UpdateResult(2, "error", unsupported_operation(configuration["provider"], "update"))
    return backend.update(
        configuration, dry_run=dry_run, restart=restart, drive_args=drive_args,
        verbose=verbose, emit=emit,
    )


def _update_chase(
    configuration: dict[str, Any], *, dry_run: bool, restart: bool, drive_args: str | None,
    verbose: bool, emit: ProgressSink | None,
) -> UpdateResult:
    del verbose
    if drive_args is not None:
        return UpdateResult(2, "error", "--drive-args applies only to a PiCar.")
    return _update_local_autonomy(
        host_name=configuration["host_name"], dry_run=dry_run, restart=restart, emit=emit,
    )


def _update_picar(
    configuration: dict[str, Any], *, dry_run: bool, restart: bool, drive_args: str | None,
    verbose: bool, emit: ProgressSink | None,
) -> UpdateResult:
    return deploy_picar_release(
        resolve_picar_target(configuration), dry_run=dry_run, restart=restart,
        drive_args=drive_args, verbose=verbose, emit=emit,
    )


def _update_local_autonomy(
    *,
    host_name: str,
    dry_run: bool,
    restart: bool,
    emit: ProgressSink | None,
) -> UpdateResult:
    """Package a release for a locally hosted vehicle; ``restart`` moves its host onto it."""

    from autonomy.runtime.client import RuntimeClient

    bundle = bundle_paths(host_name)
    source_summary = controller_bundle_source_summary()
    if dry_run:
        return UpdateResult(0, "local", receipt={
            "host_name": host_name, "dry_run": True, "restart": restart, "source": source_summary,
        })
    try:
        startup = startup_activations(bundle, host_name)
    except ValueError as exc:
        return UpdateResult(2, "error", str(exc))
    try:
        configuration = staged_host(host_name)
        release = sync_controller_bundle(bundle, emit=emit)
        ensure_step_activations(
            host_name=host_name, bundle=bundle, release=release, activations=startup,
            vehicle=configuration,
        )
        _, summary = release_code_dir(host_name, release["archive"]["sha256"])
    except (RuntimeHostError, ReleaseError) as exc:
        return UpdateResult(2, "error", str(exc))
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
            return UpdateResult(
                2, "error", f"Release {summary['archive']} was packaged, but the host did not restart: {exc}",
            )
        restarted = True
    return UpdateResult(0, "local", receipt={
        "host_name": host_name,
        "dry_run": False,
        "release": summary,
        "presets": activation_presets(activations),
        "steps": steps,
        "generation_id": decision_generation_id(
            {step: activations.get(step) for step in DECISION_STEPS}
        ),
        "restarted": restarted,
    }, host_present=record is not None)


def create_host(
    *, host_name: str, provider: str, url: str,
    ssh_target: str | None = None, pi_home: str | None = None,
    drive_args: str | None = None, dry_run: bool = False,
    verbose: bool = False, emit: ProgressSink | None = None,
) -> CreateResult:
    if (error := host_name_error(host_name)) is not None:
        return CreateResult(2, "error", error)
    if provider not in PROVIDERS:
        return CreateResult(2, "error", f"Provider must be one of {', '.join(PROVIDERS)} (got {provider!r}).")
    if (host_dir(host_name) / "host-configuration.json").exists():
        existing, error = staging_host(host_name)
        return CreateResult(2, "error", error or f"Host {host_name!r} already exists: {vehicle_label(existing)}.")
    backend = backend_for(provider)
    if backend is None or backend.create is None:
        return CreateResult(2, "error", unsupported_operation(provider, "create"))
    return backend.create(
        host_name=host_name, provider=provider, url=url, vehicle=PROVIDERS[provider],
        ssh_target=ssh_target, pi_home=pi_home, drive_args=drive_args, dry_run=dry_run,
        verbose=verbose, emit=emit,
    )


def _create_chase(
    *, host_name: str, provider: str, url: str, vehicle: Any,
    ssh_target: str | None, pi_home: str | None, drive_args: str | None, dry_run: bool,
    verbose: bool, emit: ProgressSink | None,
) -> CreateResult:
    del verbose
    if any(value is not None for value in (ssh_target, pi_home, drive_args)):
        return CreateResult(2, "error", "--ssh-target, --pi-home and --drive-args apply only to a PiCar.")
    try:
        url = vehicle.normalize_url(url)
    except ValueError as exc:
        return CreateResult(2, "error", str(exc))
    if not dry_run:
        probe = vehicle.probe(Candidate(provider, url, "cli"), timeout_s=5.0)
        if not vehicle.can_take_host(probe):
            return CreateResult(2, "error", f"No {vehicle.machine} can take a host at {url}: {probe.error}")
    configuration = {
        "host_name": host_name, "provider": provider, "vehicle_kind": vehicle.vehicle_kind,
        "connection": {vehicle.url_key: url},
    }
    if dry_run:
        plan = _update_local_autonomy(host_name=host_name, dry_run=True, restart=True, emit=emit)
        return CreateResult(plan.exit_code, "plan", configuration=configuration, release=plan)
    return _install_created_host(
        configuration, local=True, drive_args=drive_args, verbose=False, emit=emit,
    )


def _create_picar(
    *, host_name: str, provider: str, url: str, vehicle: Any,
    ssh_target: str | None, pi_home: str | None, drive_args: str | None, dry_run: bool,
    verbose: bool, emit: ProgressSink | None,
) -> CreateResult:
    try:
        url = vehicle.normalize_url(url)
    except ValueError as exc:
        return CreateResult(2, "error", str(exc))
    configuration = {
        "host_name": host_name, "provider": provider, "vehicle_kind": vehicle.vehicle_kind,
        "connection": {vehicle.url_key: url},
    }
    configuration["connection"].update(
        ssh_target=ssh_target or os.environ.get("PI_HOST") or ssh_target_from_vehicle(configuration),
        pi_home=pi_home or os.environ.get("PI_HOME") or DEFAULT_PI_HOME,
    )
    if dry_run:
        target = resolve_picar_target(configuration)
        core = install_picar_core(target, dry_run=True)
        release = deploy_picar_release(
            target, dry_run=True, restart=True, drive_args=drive_args, verbose=verbose, emit=emit,
        )
        for part in (core, release):
            if part.exit_code:
                return CreateResult(part.exit_code, "error", part.message)
        return CreateResult(0, "plan", configuration=configuration, core=core, release=release)
    return _install_created_host(
        configuration, local=False, drive_args=drive_args, verbose=verbose, emit=emit,
    )


def _install_created_host(
    configuration: dict[str, Any], *, local: bool, drive_args: str | None,
    verbose: bool, emit: ProgressSink | None,
) -> CreateResult:
    host_name = configuration["host_name"]
    directory = host_dir(host_name)
    made_directory = not directory.exists()
    write_host_configuration(configuration)
    core = None
    try:
        if not local:
            core = install_picar_core(resolve_picar_target(configuration), verbose=verbose, emit=emit)
            if core.exit_code:
                raise RuntimeError(core.message)
        result = update_host(
            host_name=host_name, restart=not local, drive_args=drive_args,
            verbose=verbose, emit=emit,
        )
        if result.exit_code:
            raise RuntimeError(result.message)
        state = None
        if local:
            base_url = ensure_chase_host(host_name)
            state = host_state(base_url, host_name=host_name, timeout_s=5.0)
        return CreateResult(
            0, "created", configuration=configuration, core=core, release=result, host_state=state,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        if local:
            stop_chase_host(host_name)
        if made_directory:
            shutil.rmtree(directory)
        else:
            (directory / "host-configuration.json").unlink(missing_ok=True)
        suffix = "" if local else " The Pi keeps files already synced."
        return CreateResult(2, "error", f"Could not create host {host_name!r}: {exc}{suffix}")


def delete_host(*, host_name: str, verbose: bool = False, emit: ProgressSink | None = None) -> DeleteResult:
    configuration, error = staging_host(host_name)
    if error:
        return DeleteResult(2, "error", error)
    backend = backend_for(configuration["provider"])
    if backend is None or backend.delete is None:
        return DeleteResult(2, "error", unsupported_operation(configuration["provider"], "delete"))
    return backend.delete(configuration, verbose=verbose, emit=emit)


def _delete_recorded_host(
    configuration: dict[str, Any], *, verbose: bool, emit: ProgressSink | None, local: bool,
) -> DeleteResult:
    host_name = configuration["host_name"]
    directory = host_dir(host_name)
    stopped, failure = _stop_active_automation(host_name)
    if failure is not None:
        return failure
    remote_harness = None
    if local:
        stop_chase_host(host_name)
    else:
        target = resolve_picar_target(configuration)
        command = donkey_service_control_command(pi_home=target.pi_home, action="stop", drive_args=None)
        code = run_step(
            "Stop Donkey runtime service", ["ssh", target.ssh_target, command],
            env=None, verbose=verbose, emit=emit,
        )
        if code:
            return DeleteResult(code, "delete_failed", host_name=host_name)
        remote_harness = harness_dir(target.pi_home)
    shutil.rmtree(directory)
    return DeleteResult(0, "deleted", host_name=host_name, deleted_path=directory, stopped_run=stopped,
                        harness_dir=remote_harness)


def _stop_active_automation(host_name: str) -> tuple[bool, DeleteResult | None]:
    """Stop a running automation before its host. An idle host has nothing to stop."""

    process = automation_dir(host_name) / "process.json"
    base_url = running_base_url(host_name)
    state = host_state(base_url, host_name=host_name, timeout_s=1.0) if base_url else {}
    try:
        process_record = json.loads(process.read_text())
    except (OSError, ValueError):
        process_record = {}
    active_run = (
        (isinstance(process_record, dict) and pid_alive(process_record.get("pid")))
        or state.get("state") in {"starting", "running", "stopping"}
    )
    if not active_run:
        return False, None
    result = stop_host_automation(host_name=host_name)
    if result.exit_code:
        return True, DeleteResult(result.exit_code, "stop_failed", host_name=host_name, stop=result)
    return True, None


def _delete_chase(
    configuration: dict[str, Any], *, verbose: bool, emit: ProgressSink | None,
) -> DeleteResult:
    return _delete_recorded_host(configuration, verbose=verbose, emit=emit, local=True)


def _delete_picar(
    configuration: dict[str, Any], *, verbose: bool, emit: ProgressSink | None,
) -> DeleteResult:
    return _delete_recorded_host(configuration, verbose=verbose, emit=emit, local=False)


register_backend("chase-sim", DeploymentBackend(create=_create_chase, update=_update_chase, delete=_delete_chase))
register_backend("picar", DeploymentBackend(create=_create_picar, update=_update_picar, delete=_delete_picar))
