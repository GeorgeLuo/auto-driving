"""The vehicles, simulators, hosts and plugins commands the viewer answers, as its read and write routes.

A read serves what the command's ``--json`` prints for the query's
parameters. A write runs the command with the request's JSON object and
answers with its result's fields and ``text``, what the command prints: a host
command's progress lines, then its result. Parameters and fields are named as
the command's flags: ``name`` is hosts' ``--name`` and plugins' ``--name``,
``host_name`` plugins' ``--host-name``.

A plugins stream read is ``plugins stream --once`` for one step, answering the
probe with the screen it prints as ``text``. A host's latest frame reads as
the perception page reads it, with its image at ``/api/hosts/frame``.
A host's learning history is the memory step's beliefs and plugins over the
run, the rows after the page's ``since`` cursor.
"""

from __future__ import annotations

import base64
import binascii
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from autonomy.decision_cycle.activation import STEPS
from autonomy.decision_cycle.steps import CLEARABLE_STEPS

from .frontend.server import Write
from .hosts.deployment.render import render_create, render_delete, render_progress, render_update
from .hosts.deployment.service import create_host, delete_host, update_host
from .hosts.progress import ProgressEvent
from .hosts.runs.render import render_lifecycle_result
from .hosts.runs.service import restart_host_automation, start_host_automation_background, stop_host_automation
from .hosts.runs.status import hosts_status
from .hosts.views.frames import HostFrames
from .hosts.views.learning import HostLearning
from .plugins.catalog.render import render_listing, render_save, render_selection, render_upload
from .plugins.catalog.service import (
    arm_collection, arm_plugins, arm_preset, disarm_collection, disarm_plugins, list_plugins, save_plugins,
    upload_collection, upload_plugin,
)
from .plugins.stream.render import render_stream
from .plugins.stream.service import STREAM_STEPS, stream_host
from .providers.registry import PROVIDERS
from .simulators.render import render_simulator_result
from .simulators.service import create_simulator, delete_simulator
from .simulators.status import simulators_status
from .vehicles.discovery import discover_vehicle, remove_vehicle
from .vehicles.render import render_vehicle_result
from .vehicles.status import vehicles_status


def _text(body: dict[str, object], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _flag(body: dict[str, object], key: str) -> bool:
    value = body.get(key, False)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be true or false")
    return value


def _optional_text(body: dict[str, object], key: str) -> str | None:
    """An optional flag's text; empty or null leaves it unset, as a blank form field means."""

    value = body.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{key} must be a string or null")
    return value or None


def _provider(body: dict[str, object]) -> str:
    provider = body.get("provider")
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError(f"provider must be one of {', '.join(PROVIDERS)}")
    return provider


def _fields(body: dict[str, object], allowed: set[str]) -> None:
    unexpected = sorted(body.keys() - allowed)
    if unexpected:
        raise ValueError(f"unsupported fields: {', '.join(unexpected)}")


def _choice(body: dict[str, object], names: tuple[str, ...]) -> str | None:
    """Which one of a command's mutually exclusive flags the body gives; null or false gives none."""

    chosen = [key for key in names if (value := body.get(key)) is not None and value is not False]
    if len(chosen) > 1:
        raise ValueError(f"choose only one of {', '.join(names)}")
    return chosen[0] if chosen else None


def _accepting(command: Write, *fields: str) -> Write:
    """The write route for ``command``, refusing a body with fields it does not take before it runs."""

    def write(body: dict[str, object]) -> dict[str, object]:
        _fields(body, set(fields))
        return command(body)

    return write


def _answer(result: object, text: str) -> dict[str, object]:
    # A host command's result can carry paths; they answer as the strings the CLI prints.
    fields = json.loads(json.dumps(asdict(result), default=str))
    return {"ok": result.exit_code == 0, **fields, "text": text}


class _Printed:
    """A host command's progress, as the lines it prints before its result."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, event: ProgressEvent) -> None:
        self.lines.append(render_progress(event))

    def text(self, result: str) -> str:
        return "\n".join([*self.lines, result] if result else self.lines)


def _discover(body: dict[str, object]) -> dict[str, object]:
    result = discover_vehicle(provider=_provider(body), url=_text(body, "url"), force=_flag(body, "force"))
    return _answer(result, render_vehicle_result(result))


def _remove(body: dict[str, object]) -> dict[str, object]:
    result = remove_vehicle(url=_text(body, "url"))
    return _answer(result, render_vehicle_result(result))


def _create(_body: dict[str, object]) -> dict[str, object]:
    result = create_simulator()
    return _answer(result, render_simulator_result(result))


def _delete(body: dict[str, object]) -> dict[str, object]:
    result = delete_simulator(url=_text(body, "url"))
    return _answer(result, render_simulator_result(result))


def _create_host(body: dict[str, object]) -> dict[str, object]:
    printed = _Printed()
    dry_run = _flag(body, "dry_run")
    result = create_host(host_name=_text(body, "name"), provider=_provider(body), url=_text(body, "url"),
                         ssh_target=_optional_text(body, "ssh_target"), pi_home=_optional_text(body, "pi_home"),
                         drive_args=_optional_text(body, "drive_args"), dry_run=dry_run, verbose=_flag(body, "verbose"),
                         emit=None if dry_run else printed)
    return _answer(result, printed.text(render_create(result, json_output=False)))


def _update_host(body: dict[str, object]) -> dict[str, object]:
    printed = _Printed()
    dry_run = _flag(body, "dry_run")
    result = update_host(host_name=_text(body, "name"), dry_run=dry_run, restart=_flag(body, "restart"),
                         drive_args=_optional_text(body, "drive_args"), verbose=_flag(body, "verbose"),
                         emit=None if dry_run else printed)
    return _answer(result, printed.text(render_update(result, json_output=False)))


def _delete_host(body: dict[str, object]) -> dict[str, object]:
    printed = _Printed()
    result = delete_host(host_name=_text(body, "name"), emit=printed)
    return _answer(result, printed.text(render_delete(result)))


def _run_flags(body: dict[str, object]) -> dict[str, object]:
    """hosts run's and restart's flags: --observe-only, --num-decisions N or --unbounded, --record."""

    count = body.get("num_decisions")
    whole_number = isinstance(count, int) and not isinstance(count, bool)  # JSON true is a Python int
    if count is not None and not whole_number:
        raise ValueError("num_decisions must be a whole number or null")
    return {"num_decisions": count, "unbounded": _flag(body, "unbounded"),
            "take_control": not _flag(body, "observe_only"), "record": _flag(body, "record")}


def _run_host(body: dict[str, object]) -> dict[str, object]:
    result = start_host_automation_background(host_name=_text(body, "name"), **_run_flags(body))
    return _answer(result, render_lifecycle_result(result))


def _stop_host(body: dict[str, object]) -> dict[str, object]:
    result = stop_host_automation(host_name=_text(body, "name"))
    return _answer(result, render_lifecycle_result(result))


def _restart_host(body: dict[str, object]) -> dict[str, object]:
    result = restart_host_automation(host_name=_text(body, "name"), **_run_flags(body))
    return _answer(result, render_lifecycle_result(result))


def _step_item(value: object, key: str) -> tuple[str, str]:
    """``<step>/<plugin>`` or ``<step>/<preset>``, as plugins' ``--name`` and ``--preset`` take them."""

    step, _, item = value.partition("/") if isinstance(value, str) else ("", "", "")
    if step not in STEPS or not item:
        raise ValueError(f"{key} must name <step>/<id> with a step from {', '.join(STEPS)}")
    return step, item


def _step_items(body: dict[str, object]) -> list[tuple[str, str]]:
    names = body.get("name")
    if not isinstance(names, list) or not names:
        raise ValueError("name must be a non-empty list of <step>/<plugin>")
    return [_step_item(name, "name") for name in names]


def _plugins_list(query: dict[str, str]) -> dict[str, object]:
    """plugins list --json: the packaged catalog, or with a host_name its whole catalog (--long)."""

    host_name = query.get("host_name")
    return json.loads(render_listing(list_plugins(host_name=host_name, long=host_name is not None),
                                     json_output=True))


def _plugins_stream(query: dict[str, str]) -> dict[str, object]:
    """plugins stream --once --step: the probe, and as ``text`` the screen it prints."""

    step = query.get("step")
    if step not in STREAM_STEPS:
        raise ValueError(f"step must be one of {', '.join(STREAM_STEPS)}")
    events = []
    result = stream_host(step, host_name=_text(query, "host_name"), refresh_s=0.0, once=True, timeout_s=3.0,
                         serve_view=False, include_publication=True, emit=events.append)
    if result.preflight or not events:
        return {**(result.live or {}), "text": result.message}
    return {**json.loads(render_stream(events[-1], json_output=True)),
            "text": render_stream(events[-1], json_output=False)}


def _arm(body: dict[str, object]) -> dict[str, object]:
    """plugins arm --name ..., --preset <step>/<preset> or --collection."""

    host_name, chosen = _text(body, "host_name"), _choice(body, ("name", "preset", "collection"))
    if chosen == "collection":
        result = arm_collection(url=None, host_name=host_name, name=_text(body, "collection"))
    elif chosen == "preset":
        step, preset = _step_item(body["preset"], "preset")
        result = arm_preset(url=None, host_name=host_name, step=step, preset=preset)
    else:
        result = arm_plugins(url=None, host_name=host_name, plugins=_step_items(body))
    return _answer(result, render_selection(result, json_output=False))


def _disarm(body: dict[str, object]) -> dict[str, object]:
    """plugins disarm --name ..., --collection, --step or --all."""

    host_name, chosen = _text(body, "host_name"), _choice(body, ("name", "collection", "step", "all"))
    if chosen == "collection":
        result = disarm_collection(url=None, host_name=host_name, name=_text(body, "collection"))
    elif chosen == "name":
        result = disarm_plugins(url=None, host_name=host_name, plugins=_step_items(body))
    elif chosen == "step":
        step = body["step"]
        if step not in CLEARABLE_STEPS:
            raise ValueError(f"step must be one of {', '.join(CLEARABLE_STEPS)}")
        result = disarm_plugins(url=None, host_name=host_name, step=step)
    elif chosen == "all" and _flag(body, "all"):
        result = disarm_plugins(url=None, host_name=host_name)
    else:
        raise ValueError("disarm needs name, collection, step or all")
    return _answer(result, render_selection(result, json_output=False))


def _upload(body: dict[str, object]) -> dict[str, object]:
    """plugins upload --name --entrypoint [--config] [--arm], the file sent as filename and source_base64;
    or --manifest [--arm], the manifest a path on this machine, as the CLI reads it with its source directory."""

    if body.get("manifest") is not None:
        _fields(body, {"host_name", "manifest", "arm"})
        result = upload_collection(url=None, host_name=_text(body, "host_name"),
                                   manifest=Path(_text(body, "manifest")).expanduser(), arm=_flag(body, "arm"))
        return _answer(result, render_upload(result, json_output=False))
    step, plugin_id = _step_item(body.get("name"), "name")
    filename = Path(_text(body, "filename")).name
    config = body.get("config")
    if config is not None and not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    try:
        source = base64.b64decode(_text(body, "source_base64"), validate=True)
    except binascii.Error as exc:
        raise ValueError(f"source_base64 is not base64: {exc}") from None
    with tempfile.TemporaryDirectory() as directory:
        file = Path(directory) / filename
        file.write_bytes(source)
        result = upload_plugin(url=None, host_name=_text(body, "host_name"), file=file, step=step,
                               plugin_id=plugin_id, entrypoint=_text(body, "entrypoint"),
                               config=None if config is None else json.dumps(config), arm=_flag(body, "arm"))
        # What it prints names the file as the page sent it, not the copy it uploaded.
        return _answer(result, render_upload(result, json_output=False).replace(str(file), filename))


def _save(body: dict[str, object]) -> dict[str, object]:
    """plugins save [--step]."""

    step = body.get("step")
    if step is not None and step not in STEPS:
        raise ValueError(f"step must be one of {', '.join(STEPS)}")
    result = save_plugins(host_name=_text(body, "host_name"), step=step)
    return _answer(result, render_save(result, json_output=False))


def _since(query: dict[str, str]) -> int:
    """A learning read's cursor: the last row's seq the page has, 0 for all of them."""

    since = query.get("since", "0")
    if not since.isascii() or not since.isdigit():
        raise ValueError("since must be a non-negative integer")
    return int(since)


HOST_FRAMES = HostFrames(frame_route="/api/hosts/frame")
HOST_LEARNING = HostLearning()

# /api/hosts is the v1 home page's host list, so hosts status reads at /api/hosts/status.
VIEWER_READS = {
    "/api/vehicles": lambda _query: vehicles_status()[1],
    "/api/simulators": lambda _query: simulators_status()[1],
    "/api/hosts/status": lambda _query: hosts_status(include_runs=True)[1],
    "/api/hosts/latest": lambda query: HOST_FRAMES.latest(_text(query, "host_name")),
    "/api/hosts/frame": lambda query: HOST_FRAMES.image(_text(query, "host_name"), _text(query, "v")),
    "/api/hosts/learning": lambda query: HOST_LEARNING.history(_text(query, "host_name"), _since(query)),
    "/api/plugins/list": _plugins_list,
    "/api/plugins/stream": _plugins_stream,
}
_RUN_FIELDS = ("name", "observe_only", "num_decisions", "unbounded", "record")
_UPLOAD_FIELDS = ("host_name", "arm", "manifest", "name", "entrypoint", "filename", "source_base64", "config")
VIEWER_WRITES = {
    route: _accepting(command, *fields)
    for route, command, fields in [
        ("/api/vehicles/discover", _discover, ("provider", "url", "force")),
        ("/api/vehicles/remove", _remove, ("url",)),
        ("/api/simulators/create", _create, ()),
        ("/api/simulators/delete", _delete, ("url",)),
        ("/api/hosts/create", _create_host,
         ("name", "provider", "url", "ssh_target", "pi_home", "drive_args", "dry_run", "verbose")),
        ("/api/hosts/update", _update_host, ("name", "dry_run", "restart", "drive_args", "verbose")),
        ("/api/hosts/delete", _delete_host, ("name",)),
        ("/api/hosts/run", _run_host, _RUN_FIELDS),
        ("/api/hosts/stop", _stop_host, ("name",)),
        ("/api/hosts/restart", _restart_host, _RUN_FIELDS),
        ("/api/plugins/arm", _arm, ("host_name", "name", "preset", "collection")),
        ("/api/plugins/disarm", _disarm, ("host_name", "name", "collection", "step", "all")),
        ("/api/plugins/upload", _upload, _UPLOAD_FIELDS),
        ("/api/plugins/save", _save, ("host_name", "step")),
    ]
}
