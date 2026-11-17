"""Values that cross a run's process and module boundaries.

``WORKER_MODULE`` is the background entry point. Stop recognizes a worker by
that exact module token, not a prefix.

``RunResult`` describes a run's outcome. ``status_reason`` is the truncated
reason status collection and status text both use.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

from autonomy.fields import FieldError, require_ascii_id, require_choice, require_keys, require_safe_int, require_text

from ...shared.timestamps import timestamp_ms

WORKER_MODULE = "cli.automa_cli.hosts.runs.worker"

MAX_STATUS_REASON_CHARS = 240


@dataclass
class RunState:
    schema: str | None = None
    host_name: str | None = None
    run_id: str | None = None
    status: str | None = None
    pid: int | None = None
    started_at_ms: int | None = None
    updated_at_ms: int | None = None
    completed_at_ms: int | None = None
    frames_received: int = 0
    frames_dropped: int | None = None
    processed_count: int = 0
    num_decisions: int | None = None
    pipeline: str | None = None
    control_source: str | None = None
    action_policy: str | None = None
    control_application: str | None = None
    recording: bool | None = None
    recorded_count: int = 0
    run_dir: str | None = None
    stop_reason: str | None = None
    error: str | None = None
    exit_code: int | None = None
    activation_problems: list[dict[str, str]] | None = None
    session: dict[str, object] | None = None
    execution: dict[str, object] | None = None
    selections: dict[str, dict[str, object]] = field(default_factory=dict)
    decision: dict[str, object] = field(default_factory=dict)
    steps: dict[str, object] | None = None
    arming: dict[str, object] | None = None
    last_frame: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: object) -> RunState:
        document = require_keys(payload, {item.name for item in fields(cls)})
        return cls(
            schema=_schema(document["schema"], "automa_automation_run_state"),
            host_name=_identifier(document["host_name"], "host_name"),
            run_id=_identifier(document["run_id"], "run_id"),
            status=_text(document["status"], "status"),
            pid=_integer(document["pid"], "pid"),
            started_at_ms=_integer(document["started_at_ms"], "started_at_ms"),
            updated_at_ms=_integer(document["updated_at_ms"], "updated_at_ms"),
            completed_at_ms=_integer(document["completed_at_ms"], "completed_at_ms"),
            frames_dropped=_integer(document["frames_dropped"], "frames_dropped"),
            num_decisions=_integer(document["num_decisions"], "num_decisions"),
            exit_code=_integer(document["exit_code"], "exit_code"),
            frames_received=require_safe_int(document["frames_received"], field_name="frames_received"),
            processed_count=require_safe_int(document["processed_count"], field_name="processed_count"),
            recorded_count=require_safe_int(document["recorded_count"], field_name="recorded_count"),
            pipeline=_text(document["pipeline"], "pipeline"),
            control_source=_text(document["control_source"], "control_source"),
            action_policy=_text(document["action_policy"], "action_policy"),
            control_application=_text(document["control_application"], "control_application"),
            run_dir=_text(document["run_dir"], "run_dir"),
            stop_reason=_text(document["stop_reason"], "stop_reason"),
            error=_text(document["error"], "error"),
            recording=_boolean(document["recording"], "recording", nullable=True),
            activation_problems=_activation_problems(document["activation_problems"]),
            session=_document(document["session"], "session", nullable=True),
            execution=_document(document["execution"], "execution", nullable=True),
            steps=_document(document["steps"], "steps", nullable=True),
            arming=_document(document["arming"], "arming", nullable=True),
            selections=_document(document["selections"], "selections"),
            decision=_document(document["decision"], "decision"),
            last_frame=_document(document["last_frame"], "last_frame"),
        )

    def to_dict(self) -> dict[str, object]:
        return {item.name: getattr(self, item.name) for item in fields(self)}

    def complete(self, status: str) -> None:
        self.status = status
        self.completed_at_ms = self.updated_at_ms = timestamp_ms()

    def startup_error(self, error: str, exit_code: int | None) -> None:
        self.complete("error")
        self.error, self.exit_code = error, exit_code


@dataclass
class WorkerProcess:
    schema: str | None = None
    host_name: str | None = None
    pid: int | None = None
    started_at_ms: int | None = None
    command: list[str] | None = None
    log_to_disk: bool = False
    log_path: str | None = None
    state_path: str | None = None
    stream_command: str | None = None
    startup: dict[str, object] | None = None
    status: str | None = None
    stopped_by: str | None = None
    stopped_at_ms: int | None = None

    @classmethod
    def from_dict(cls, payload: object) -> WorkerProcess:
        document = require_keys(payload, {item.name for item in fields(cls)})
        command = document["command"]
        if command is not None:
            if not isinstance(command, list):
                raise FieldError("command", "must be an array or null")
            command = [require_text(token, field_name=f"command.{index}") for index, token in enumerate(command)]
        return cls(
            schema=_schema(document["schema"], "automa_automation_process"),
            host_name=_identifier(document["host_name"], "host_name"),
            pid=_integer(document["pid"], "pid"),
            started_at_ms=_integer(document["started_at_ms"], "started_at_ms"),
            command=command,
            log_to_disk=_boolean(document["log_to_disk"], "log_to_disk"),
            log_path=_text(document["log_path"], "log_path"),
            state_path=_text(document["state_path"], "state_path"),
            stream_command=_text(document["stream_command"], "stream_command"),
            startup=_document(document["startup"], "startup", nullable=True),
            status=_text(document["status"], "status"),
            stopped_by=_text(document["stopped_by"], "stopped_by"),
            stopped_at_ms=_integer(document["stopped_at_ms"], "stopped_at_ms"),
        )

    def to_dict(self) -> dict[str, object]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


def _schema(value: object, schema: str) -> str | None:
    return None if value is None else require_choice(value, (schema,), field_name="schema")


def _identifier(value: object, field_name: str) -> str | None:
    return None if value is None else require_ascii_id(value, field_name=field_name)


def _text(value: object, field_name: str) -> str | None:
    return None if value is None else require_text(value, field_name=field_name)


def _integer(value: object, field_name: str) -> int | None:
    return None if value is None else require_safe_int(value, field_name=field_name)


def _boolean(value: object, field_name: str, *, nullable: bool = False) -> bool | None:
    if nullable and value is None:
        return None
    if type(value) is not bool:
        raise FieldError(field_name, "must be a bool" + (" or null" if nullable else ""))
    return value


def _document(value: object, field_name: str, *, nullable: bool = False) -> dict | None:
    if nullable and value is None:
        return None
    if not isinstance(value, dict):
        raise FieldError(field_name, "must be an object" + (" or null" if nullable else ""))
    return value


def _activation_problems(value: object) -> list[dict[str, str]] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise FieldError("activation_problems", "must be an array or null")
    for index, problem in enumerate(value):
        if not isinstance(problem, dict):
            raise FieldError(f"activation_problems.{index}", "must be an object")
        for key, text in problem.items():
            require_text(text, field_name=f"activation_problems.{index}.{key}")
    return value


class AppliedStep(TypedDict):
    preset: object
    plugins: list[str]
    plugin_configs: dict[str, object] | None
    run_count: int | None
    failure_count: int | None
    last_error: str | None
    error: object


class ComparedStep(TypedDict):
    activation: str
    source: str
    saved: bool
    preset: str | None
    plugins: list[str]
    plugin_configs: dict[str, object]
    applied: AppliedStep | None


class AppliedSteps(TypedDict):
    available: bool
    reason: str | None


class ComparedSteps(TypedDict):
    steps: dict[str, ComparedStep]
    applied_steps: AppliedSteps


class RunProcess(TypedDict):
    pid: int | None
    running: bool
    pid_state: str
    status: str
    generation_matches: bool
    reason: str | None
    log_to_disk: bool
    log_path: str | None
    command: list[str] | None
    process_record: str


class RunStatusState(TypedDict):
    status: str | None
    run_id: str | None
    frames_received: object
    frames_dropped: object
    processed_count: object
    num_decisions: object
    recording: bool | None
    control_source: str | None
    action_policy: object
    execution: object
    session: object
    error: str | None
    last_frame: dict[str, object]
    state_record: str
    latest_perception_age_ms: int | None
    pipeline: NotRequired[str | None]
    pid: NotRequired[int | None]
    arming: NotRequired[dict[str, object] | None]
    control_application: NotRequired[str | None]
    exit_code: NotRequired[int | None]
    stop_reason: NotRequired[str | None]
    updated_at_ms: NotRequired[int | None]


class RunSection(ComparedSteps):
    process: RunProcess
    state: RunStatusState


class RunDecision(TypedDict):
    activation: str
    generation_id: object


class RunRelease(TypedDict):
    tree_sha256: object
    created_at_ms: object
    archive_sha256: object
    archive: str | None
    manifest: str | None


class HostRun(RunSection):
    host_name: str
    activation_problems: list[dict[str, str]]
    deployed: bool
    release: RunRelease | None
    bundle_root: str
    automation_runtime_exists: bool
    automation_dir: str
    decision: RunDecision
    published_view: Mapping[str, object]



@dataclass(frozen=True)
class RunResult:
    exit_code: int
    message: str = ""
    state: RunState | None = None
    state_path: Path | None = None
    problems: list[dict[str, str]] | None = None


def run_exit_code(state: RunState) -> int:
    if state.status == "error":
        return 2
    return 130 if state.stop_reason == "keyboard_interrupt" else 0


def status_reason(value: object, *, fallback: str) -> str:
    """The first line of a reason, truncated so a status row stays one line."""

    lines = (
        [line.strip() for line in value.splitlines() if line.strip()]
        if isinstance(value, str)
        else []
    )
    reason = lines[0] if lines else fallback
    if len(reason) <= MAX_STATUS_REASON_CHARS:
        return reason
    return f"{reason[:MAX_STATUS_REASON_CHARS]}..."


@dataclass(frozen=True)
class StartResult:
    exit_code: int
    kind: Literal[
        "error",
        "activation_invalid",
        "starting",
        "session_not_running",
        "records_disagree",
        "authority_mismatch",
        "existing",
        "launch_failed",
        "stopped",
        "started",
    ]
    host_name: str = ""
    error: str = ""
    pid: int | None = None
    state_pid: int | None = None
    state_path: Path | None = None
    log_path: Path | None = None
    expected_authority: dict[str, object] | None = None
    actual_authority: dict[str, object] | None = None
    process_record: WorkerProcess | None = None
    startup: dict[str, object] | None = None
    problems: list[dict[str, str]] | None = None


@dataclass(frozen=True)
class StopResult:
    exit_code: int
    kind: Literal[
        "error",
        "remote_failed",
        "no_pid",
        "already_dead",
        "stale_pid",
        "stopped",
        "forced",
        "timeout",
        "release_failed",
    ]
    host_name: str
    pid: int | None = None
    state_path: Path | None = None
    command: str | None = None
    error: str = ""


@dataclass(frozen=True)
class RestartResult:
    """A stop and then a start. ``start_failed`` stopped the run, but the new run did not start."""

    exit_code: int
    kind: Literal["error", "stop_failed", "restart_failed", "start_failed", "started"]
    host_name: str = ""
    stop: StopResult | None = None
    start: StartResult | None = None
    error: str = ""
    problems: list[dict[str, str]] | None = None
