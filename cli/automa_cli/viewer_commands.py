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

from .hosts.deployment.render import render_create, render_delete, render_progress, render_update
from .hosts.deployment.service import create_host, delete_host, update_host
from .hosts.progress import ProgressEvent
from .hosts.runs.render import render_lifecycle_result
from .hosts.runs.service import restart_host_automation, start_host_automation_background, stop_host_automation
from .hosts.runs.status import hosts_status
from .hosts.views.frames import HostFrames
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
    provider, force = body.get("provider"), body.get("force", False)
    if provider not in PROVIDERS:
        raise ValueError(f"provider must be one of {', '.join(PROVIDERS)}")
    if not isinstance(force, bool):
        raise ValueError("force must be true or false")
    result = discover_vehicle(provider=provider, url=_text(body, "url"), force=force)
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
    result = create_host(host_name=_text(body, "name"), provider=body.get("provider"), url=_text(body, "url"), emit=printed)
    return _answer(result, printed.text(render_create(result, json_output=False)))


def _update_host(body: dict[str, object]) -> dict[str, object]:
    printed = _Printed()
    result = update_host(host_name=_text(body, "name"), emit=printed)
    return _answer(result, printed.text(render_update(result, json_output=False)))


def _delete_host(body: dict[str, object]) -> dict[str, object]:
    printed = _Printed()
    result = delete_host(host_name=_text(body, "name"), emit=printed)
    return _answer(result, printed.text(render_delete(result)))


def _run_flags(body: dict[str, object]) -> dict[str, object]:
    """hosts run's and restart's flags: --observe-only, --num-decisions N or --unbounded, --record."""

    count = body.get("num_decisions")
    if count is not None and (isinstance(count, bool) or not isinstance(count, int)):
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

    host_name = _text(body, "host_name")
    if "collection" in body:
        result = arm_collection(url=None, host_name=host_name, name=_text(body, "collection"))
    elif "preset" in body:
        step, preset = _step_item(body["preset"], "preset")
        result = arm_preset(url=None, host_name=host_name, step=step, preset=preset)
    else:
        result = arm_plugins(url=None, host_name=host_name, plugins=_step_items(body))
    return _answer(result, render_selection(result, json_output=False))


def _disarm(body: dict[str, object]) -> dict[str, object]:
    """plugins disarm --name ..., --collection, --step or --all."""

    host_name, step = _text(body, "host_name"), body.get("step")
    if "collection" in body:
        result = disarm_collection(url=None, host_name=host_name, name=_text(body, "collection"))
    elif "name" in body:
        result = disarm_plugins(url=None, host_name=host_name, plugins=_step_items(body))
    elif step is not None:
        if step not in CLEARABLE_STEPS:
            raise ValueError(f"step must be one of {', '.join(CLEARABLE_STEPS)}")
        result = disarm_plugins(url=None, host_name=host_name, step=step)
    elif _flag(body, "all"):
        result = disarm_plugins(url=None, host_name=host_name)
    else:
        raise ValueError("disarm needs name, collection, step or all")
    return _answer(result, render_selection(result, json_output=False))


def _upload(body: dict[str, object]) -> dict[str, object]:
    """plugins upload --name --entrypoint [--config] [--arm], the file sent as filename and source_base64;
    or --manifest [--arm], the manifest a path on this machine, as the CLI reads it with its source directory."""

    if "manifest" in body:
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


HOST_FRAMES = HostFrames(frame_route="/api/hosts/frame")

# /api/hosts is the v1 home page's host list, so hosts status reads at /api/hosts/status.
VIEWER_READS = {
    "/api/vehicles": lambda _query: vehicles_status()[1],
    "/api/simulators": lambda _query: simulators_status()[1],
    "/api/hosts/status": lambda _query: hosts_status(include_runs=True)[1],
    "/api/hosts/latest": lambda query: HOST_FRAMES.latest(_text(query, "host_name")),
    "/api/hosts/frame": lambda query: HOST_FRAMES.image(_text(query, "host_name"), _text(query, "v")),
    "/api/plugins/list": _plugins_list,
    "/api/plugins/stream": _plugins_stream,
}
VIEWER_WRITES = {
    "/api/vehicles/discover": _discover,
    "/api/vehicles/remove": _remove,
    "/api/simulators/create": _create,
    "/api/simulators/delete": _delete,
    "/api/hosts/create": _create_host,
    "/api/hosts/update": _update_host,
    "/api/hosts/delete": _delete_host,
    "/api/hosts/run": _run_host,
    "/api/hosts/stop": _stop_host,
    "/api/hosts/restart": _restart_host,
    "/api/plugins/arm": _arm,
    "/api/plugins/disarm": _disarm,
    "/api/plugins/upload": _upload,
    "/api/plugins/save": _save,
}
