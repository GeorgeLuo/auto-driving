from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from autonomy.runtime.client import RuntimeClient
from autonomy.runtime.session import RunConfiguration

from ...frontend.perception_view import (
    get_perception_view_status,
    perception_view_ready,
)
from ...providers.catalog import PROVIDERS
from ...providers.models import DEFAULT_READINESS_TIMEOUT_S
from ...releases.errors import RuntimeHostError
from ...releases.paths import bundle_paths
from ...shared.io.json_files import read_json, write_json_atomically
from ...shared.io.processes import pid_alive
from ...shared.open_view import launch_browser
from ...shared.paths import display_path
from ...shared.timestamps import timestamp_ms
from ..discovery import running_base_url
from ..local import ensure_chase_host, stop_chase_host
from ..staging import (
    automation_dir as host_automation_dir,
)
from ..staging import (
    staged_host,
)
from ..step_activations import (
    bundle_activation_problems,
    decision_identity,
    staged_selections,
)
from ..vehicle_access import create_vehicle_access
from .models import RestartResult, RunEvent, RunResult, StartResult, StopResult
from .monitor import monitor_runtime
from .process import (
    launch_worker,
    pid_matches_automation,
    process_command,
    stop_worker,
    wait_for_automation_startup,
    worker_command,
)
from .store import (
    control_application,
    decision_record,
    initialize_automation_startup,
    mark_automation_startup_error,
    mark_process_stopped,
    mark_state_stopped,
)


def _normalize_host_url(value: str) -> str:
    """The host base URL, or why this value cannot name one."""

    text = value.strip()
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("--host-url must be an http or https URL with a host")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("--host-url must be the host base URL, not a route or query")
    return text.rstrip("/")


def _host_name_at_host(host_url: str, *, timeout_s: float) -> str:
    """The host name reported by the runtime."""

    try:
        status = RuntimeClient(host_url, timeout_s=max(0.1, timeout_s)).host_status()
    except RuntimeError as exc:
        raise RuntimeHostError(f"Could not read the host at {host_url}: {exc}") from exc
    host_name = status.get("host_name") if isinstance(status, dict) else None
    if not isinstance(host_name, str) or not host_name.strip():
        raise RuntimeHostError(f"The host at {host_url} did not report a host name.")
    return host_name


def _resolve_run_target(
    host_name: str | None,
    host_url: str | None,
    *,
    timeout_s: float,
) -> tuple[str, str | None]:
    """The host name to record, and the existing host URL when one was named.

    ``--name`` finds or starts the checkout's host later. ``--host-url`` is that
    host, and its status reports the host name. Passing both or neither is refused.
    """

    if (host_name is None) == (host_url is None):
        raise ValueError("Pass either --name or --host-url.")
    if host_url is None:
        assert host_name is not None
        return host_name, None
    normalized = _normalize_host_url(host_url)
    return _host_name_at_host(normalized, timeout_s=timeout_s), normalized


def _run_configuration(
    *, take_control: bool, num_decisions: int | None, unbounded: bool = False
) -> RunConfiguration:
    """Movement runs require a positive bound or an explicit unbounded opt-in."""

    # Refuse what the runtime refuses before --unbounded replaces the count.
    if num_decisions is not None and (
        type(num_decisions) is not int or num_decisions < 1
    ):
        raise ValueError("--num-decisions must be a positive integer")
    if unbounded:
        num_decisions = None

    if take_control and num_decisions is None and not unbounded:
        raise ValueError(
            "A run that applies control needs --num-decisions N (N > 0); "
            "use --unbounded to run until stopped."
        )
    return RunConfiguration(
        mode="autonomy" if take_control else "observe_only",
        num_decisions=num_decisions,
    )


def run_host_automation(
    *,
    host_name: str | None = None,
    host_url: str | None = None,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
    num_decisions: int | None = None,
    unbounded: bool = False,
    take_control: bool = True,
    record: bool = False,
    verbose: bool = False,
    emit: Callable[[RunEvent], None] | None = None,
) -> RunResult:
    try:
        if host_name is not None:
            staged_host(host_name)
        configuration = _run_configuration(
            take_control=take_control,
            num_decisions=num_decisions,
            unbounded=unbounded,
        )
        host_name, host_url = _resolve_run_target(
            host_name, host_url, timeout_s=timeout_s
        )
    except (TypeError, ValueError, RuntimeHostError) as exc:
        return RunResult(2, str(exc))
    # Every vehicle runs its staged steps on its runtime host: a PiCar's Donkey
    # service or a local Chase host, each from its controller release.
    # Each step runs its staged selection, else its built-in, else nothing.
    bundle = bundle_paths(host_name)
    problems = bundle_activation_problems(bundle, host_name)
    if problems:
        return RunResult(2, problems=problems)
    try:
        selections = staged_selections(bundle)
        identity = decision_identity(bundle)
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return RunResult(
            2, f"Could not read staged step activations for {host_name}: {exc}"
        )
    if host_url is None:
        try:
            base_url = (
                ensure_chase_host(host_name)
                if PROVIDERS[staged_host(host_name)["provider"]].local_host
                else running_base_url(host_name)
            )
            if base_url is None:
                raise RuntimeHostError(f"Host {host_name!r} is not running.")
        except RuntimeHostError as exc:
            return RunResult(2, str(exc))
    else:
        base_url = host_url
    return monitor_runtime(
        host_name=host_name,
        base_url=base_url,
        automation_dir=Path(bundle["runtime_dir"]) / "automation",
        selections=selections,
        decision=decision_record(identity),
        configuration=configuration,
        timeout_s=timeout_s,
        record=record,
        verbose=verbose,
        emit=emit,
    )


def start_host_automation_background(
    *,
    host_name: str | None = None,
    host_url: str | None = None,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
    num_decisions: int | None = None,
    unbounded: bool = False,
    take_control: bool = True,
    record: bool = False,
    verbose: bool = False,
    log_to_disk: bool = False,
    open_view: bool = False,
    startup_wait_s: float = 20.0,
) -> StartResult:
    try:
        if host_name is not None:
            staged_host(host_name)
        configuration = _run_configuration(
            take_control=take_control,
            num_decisions=num_decisions,
            unbounded=unbounded,
        )
        host_name, host_url = _resolve_run_target(
            host_name, host_url, timeout_s=timeout_s
        )
    except (TypeError, ValueError, RuntimeHostError) as exc:
        return StartResult(2, "error", error=str(exc))
    automation_dir = host_automation_dir(host_name)
    automation_dir.mkdir(parents=True, exist_ok=True)
    process_path = automation_dir / "process.json"
    log_path = automation_dir / "automation.log"

    bundle = bundle_paths(host_name)
    problems = bundle_activation_problems(bundle, host_name)
    if problems:
        return StartResult(2, "activation_invalid", problems=problems)

    existing = read_json(process_path)
    existing_pid = existing.get("pid") if isinstance(existing, dict) else None
    if isinstance(existing_pid, int) and pid_alive(existing_pid):
        existing_state = read_json(automation_dir / "state.json")
        existing_status = (
            existing_state.get("status")
            if isinstance(existing_state, dict)
            else "unknown"
        )
        if existing_status in {"launching", "starting"}:
            return StartResult(
                2,
                "starting",
                host_name=host_name,
                pid=existing_pid,
                state_path=automation_dir / "state.json",
            )
        existing_run_id = (
            existing_state.get("run_id")
            if isinstance(existing_state, dict)
            and isinstance(existing_state.get("run_id"), str)
            else None
        )
        existing_state_pid = (
            existing_state.get("pid")
            if isinstance(existing_state, dict)
            and isinstance(existing_state.get("pid"), int)
            else None
        )
        if existing_state_pid is not None and existing_state_pid != existing_pid:
            return StartResult(
                2,
                "records_disagree",
                host_name=host_name,
                pid=existing_pid,
                state_pid=existing_state_pid,
            )
        expected_authority = {
            "action_policy": configuration.mode,
            "control_application": control_application(configuration),
        }
        actual_authority = {
            key: existing_state.get(key) if isinstance(existing_state, dict) else None
            for key in expected_authority
        }
        if actual_authority != expected_authority:
            return StartResult(
                2,
                "authority_mismatch",
                host_name=host_name,
                pid=existing_pid,
                expected_authority=expected_authority,
                actual_authority=actual_authority,
            )
        view = get_perception_view_status(
            automation_dir,
            expected_run_id=existing_run_id,
            expected_worker_pid=existing_pid,
        )
        if not perception_view_ready(view):
            return StartResult(
                2,
                "view_unready",
                host_name=host_name,
                pid=existing_pid,
                view=view,
                state_path=automation_dir / "state.json",
            )
        browser = launch_browser(str(view["url"])) if open_view else None
        return StartResult(
            0,
            "existing",
            host_name=host_name,
            pid=existing_pid,
            view=view,
            browser=browser,
            state_path=automation_dir / "state.json",
            process_record=existing,
            log_path=log_path,
        )

    command = worker_command(
        host_name=host_name,
        host_url=host_url,
        timeout_s=timeout_s,
        configuration=configuration,
        take_control=take_control,
        record=record,
        verbose=verbose,
    )

    started_at_ms = timestamp_ms()
    initialize_automation_startup(
        automation_dir=automation_dir,
        host_name=host_name,
        started_at_ms=started_at_ms,
        configuration=configuration,
        record=record,
    )

    try:
        process = launch_worker(command, log_path=log_path if log_to_disk else None)
    except OSError as exc:
        mark_automation_startup_error(
            automation_dir=automation_dir,
            error=f"Could not launch automation worker: {type(exc).__name__}: {exc}",
        )
        return StartResult(
            2,
            "launch_failed",
            host_name=host_name,
            error=f"{type(exc).__name__}: {exc}",
        )

    process_record = {
        "schema": "automa_automation_process",
        "host_name": host_name,
        "pid": process.pid,
        "started_at_ms": started_at_ms,
        "command": command,
        "log_to_disk": bool(log_to_disk),
        "log_path": display_path(log_path) if log_to_disk else None,
        "state_path": display_path(automation_dir / "state.json"),
        "latest_perception_text": display_path(
            automation_dir / "latest_perception.txt"
        ),
        "stream_command": f"./cli/automa plugins stream --step perception --host-name {host_name}",
    }
    write_json_atomically(process_path, process_record)
    startup = wait_for_automation_startup(
        process=process,
        automation_dir=automation_dir,
        timeout_s=startup_wait_s,
    )
    process_record["startup"] = {
        key: value for key, value in startup.items() if key != "state"
    }
    write_json_atomically(process_path, process_record)

    browser = (
        launch_browser(str(startup.get("view_url")))
        if open_view and startup["status"] == "ready"
        else None
    )
    return StartResult(
        0 if startup["status"] in {"ready", "completed"} else 2,
        "started",
        host_name=host_name,
        pid=process.pid,
        startup=startup,
        browser=browser,
        state_path=automation_dir / "state.json",
        process_record=process_record,
        log_path=log_path,
    )


def restart_host_automation(
    *,
    host_name: str,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
    num_decisions: int | None = None,
    unbounded: bool = False,
    take_control: bool = True,
    record: bool = False,
    verbose: bool = False,
    log_to_disk: bool = False,
    open_view: bool = False,
    wait_s: float = 3.0,
) -> RestartResult:
    try:
        _run_configuration(
            take_control=take_control,
            num_decisions=num_decisions,
            unbounded=unbounded,
        )
    except (TypeError, ValueError) as exc:
        return RestartResult(2, kind="error", error=str(exc))
    bundle = bundle_paths(host_name)
    problems = bundle_activation_problems(bundle, host_name)
    if problems:
        return RestartResult(2, kind="error", problems=problems)
    stop_result = stop_host_automation(host_name=host_name, wait_s=wait_s)
    if stop_result.exit_code != 0:
        return RestartResult(stop_result.exit_code, kind="stop_failed", stop=stop_result)

    # A fresh host process loads the staged specs and configs; a Chase host
    # with none running starts on the latest release with the run.
    base_url = running_base_url(host_name)
    if base_url is not None:
        try:
            RuntimeClient(base_url, timeout_s=timeout_s).restart(
                timeout_s=max(30.0, timeout_s)
            )
        except (RuntimeError, TimeoutError) as exc:
            return RestartResult(2, kind="restart_failed", host_name=host_name, error=str(exc))

    start_result = start_host_automation_background(
        host_name=host_name,
        timeout_s=timeout_s,
        num_decisions=num_decisions,
        unbounded=unbounded,
        take_control=take_control,
        record=record,
        verbose=verbose,
        log_to_disk=log_to_disk,
        open_view=open_view,
    )
    return RestartResult(start_result.exit_code, stop=stop_result, start=start_result)


def stop_host_automation(
    *,
    host_name: str,
    wait_s: float = 3.0,
) -> StopResult:
    # The host ends the session and releases control; the monitor then
    # finishes the run record.
    try:
        base_url = running_base_url(host_name)
    except RuntimeHostError as exc:
        return StopResult(2, "error", host_name, error=str(exc))
    if base_url is not None:
        try:
            RuntimeClient(base_url, timeout_s=max(1.0, wait_s)).stop()
        except RuntimeError as exc:
            if not PROVIDERS[staged_host(host_name)["provider"]].local_host:
                return StopResult(2, "remote_failed", host_name, error=str(exc))
            forced = _replace_unanswering_host(host_name, wait_s=wait_s)
            if forced is not None:
                return forced
    automation_dir = host_automation_dir(host_name)
    process_path = automation_dir / "process.json"
    state_path = automation_dir / "state.json"
    process = read_json(process_path)
    pid = process.get("pid") if isinstance(process, dict) else None

    if not isinstance(pid, int):
        mark_state_stopped(state_path, stopped_by="stop_command_no_pid")
        return StopResult(0, "no_pid", host_name, state_path=state_path)

    if not pid_alive(pid):
        mark_process_stopped(
            process_path, process, stopped_by="stop_command_already_dead"
        )
        mark_state_stopped(state_path, stopped_by="stop_command_already_dead")
        return StopResult(0, "already_dead", host_name, pid=pid, state_path=state_path)
    if not pid_matches_automation(pid, host_name):
        mark_process_stopped(process_path, process, stopped_by="stop_command_stale_pid")
        mark_state_stopped(state_path, stopped_by="stop_command_stale_pid")
        return StopResult(
            0, "stale_pid", host_name, pid=pid, command=process_command(pid)
        )

    outcome = stop_worker(pid, wait_s=wait_s)
    if outcome != "timeout":
        stopped_by = "stop_command_forced" if outcome == "forced" else "stop_command"
        mark_process_stopped(process_path, process, stopped_by=stopped_by)
        mark_state_stopped(state_path, stopped_by=stopped_by)
    return StopResult(
        2 if outcome == "timeout" else 0,
        outcome,
        host_name,
        pid=pid,
        state_path=state_path,
    )


def _replace_unanswering_host(host_name: str, *, wait_s: float) -> StopResult | None:
    """End a Chase host that does not answer; a killed one cannot release control.

    ``None`` once the host is gone with control released.
    """

    killed = stop_chase_host(host_name, wait_s=wait_s)
    if not killed:
        return None
    try:
        create_vehicle_access(
            staged_host(host_name), timeout_s=max(1.0, wait_s)
        ).car.stop()
    except Exception as exc:  # noqa: BLE001 - reported with the recovery
        return StopResult(
            2, "release_failed", host_name, error=f"{type(exc).__name__}: {exc}"
        )
    return None
