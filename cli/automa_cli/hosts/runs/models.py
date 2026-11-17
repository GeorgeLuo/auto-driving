"""Values that cross a run's process and module boundaries.

``WORKER_MODULE`` is the background entry point. Stop recognizes a worker by
that exact module token, not a prefix.

``RunResult`` describes a run's outcome. ``status_reason`` is the truncated
reason status collection and status text both use.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Literal, NotRequired, TypedDict, cast

from ...shared.timestamps import timestamp_ms

WORKER_MODULE = "cli.automa_cli.hosts.runs.worker"

MAX_STATUS_REASON_CHARS = 240


@dataclass
class _Record:
    # Keep absent fields absent when an old or partial record is rewritten.
    _present: set[str] = field(default_factory=set, repr=False, kw_only=True)
    _extra: dict[str, object] = field(default_factory=dict, repr=False, kw_only=True)

    def __post_init__(self) -> None:
        self._tracking = True

    def __setattr__(self, name: str, value: object) -> None:
        object.__setattr__(self, name, value)
        if not name.startswith("_") and self.__dict__.get("_tracking", False):
            self._present.add(name)

    def _remember(self, payload: dict[str, object]) -> None:
        names = {item.name for item in fields(self) if not item.name.startswith("_")}
        self._present = names.intersection(payload)
        self._extra = {key: value for key, value in payload.items() if key not in names}

    def to_dict(self) -> dict[str, object]:
        result = dict(self._extra)
        for item in fields(self):
            if item.name.startswith("_"):
                continue
            value = getattr(self, item.name)
            default = item.default_factory() if item.default_factory is not MISSING else item.default
            if item.name in self._present or value != default:
                result[item.name] = value
        return result


@dataclass
class RunState(_Record):
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
    def from_dict(cls, payload: object) -> RunState | None:
        if not isinstance(payload, dict):
            return None
        record = cls(
            schema=_string(payload.get("schema")), host_name=_string(payload.get("host_name")),
            run_id=_string(payload.get("run_id")), status=_string(payload.get("status")),
            pipeline=_string(payload.get("pipeline")), control_source=_string(payload.get("control_source")),
            action_policy=_string(payload.get("action_policy")), control_application=_string(payload.get("control_application")),
            run_dir=_string(payload.get("run_dir")), stop_reason=_string(payload.get("stop_reason")),
            error=_string(payload.get("error")), pid=_integer(payload.get("pid")),
            started_at_ms=_integer(payload.get("started_at_ms")), updated_at_ms=_integer(payload.get("updated_at_ms")),
            completed_at_ms=_integer(payload.get("completed_at_ms")), frames_dropped=_integer(payload.get("frames_dropped")),
            num_decisions=_integer(payload.get("num_decisions")), exit_code=_integer(payload.get("exit_code")),
            frames_received=_integer(payload.get("frames_received")) or 0, processed_count=_integer(payload.get("processed_count")) or 0,
            recorded_count=_integer(payload.get("recorded_count")) or 0, session=_document(payload.get("session")),
            execution=_document(payload.get("execution")), steps=_document(payload.get("steps")),
            arming=_document(payload.get("arming")),
            recording=payload.get("recording") if type(payload.get("recording")) is bool else None,
            activation_problems=payload.get("activation_problems") if isinstance(payload.get("activation_problems"), list) else None,
            selections=cast(dict[str, dict[str, object]], _document(payload.get("selections")) or {}),
            decision=_document(payload.get("decision")) or {},
            last_frame=_document(payload.get("last_frame")) or {},
        )
        record._remember(payload)
        return record

    def complete(self, status: str) -> None:
        self.status = status
        self.completed_at_ms = self.updated_at_ms = timestamp_ms()

    def startup_error(self, error: str, exit_code: int | None) -> None:
        self.complete("error")
        self.error, self.exit_code = error, exit_code


@dataclass
class WorkerProcess(_Record):
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
    def from_dict(cls, payload: object) -> WorkerProcess | None:
        if not isinstance(payload, dict):
            return None
        command = payload.get("command")
        record = cls(
            schema=_string(payload.get("schema")), host_name=_string(payload.get("host_name")),
            pid=_integer(payload.get("pid")), started_at_ms=_integer(payload.get("started_at_ms")),
            command=command if isinstance(command, list) and all(isinstance(token, str) for token in command) else None,
            log_to_disk=payload.get("log_to_disk") if type(payload.get("log_to_disk")) is bool else False,
            log_path=_string(payload.get("log_path")), state_path=_string(payload.get("state_path")),
            stream_command=_string(payload.get("stream_command")), startup=_document(payload.get("startup")),
            status=_string(payload.get("status")), stopped_by=_string(payload.get("stopped_by")),
            stopped_at_ms=_integer(payload.get("stopped_at_ms")),
        )
        record._remember(payload)
        return record


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _integer(value: object) -> int | None:
    return value if type(value) is int else None


def _document(value: object) -> dict[str, object] | None:
    return value if isinstance(value, dict) else None


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
