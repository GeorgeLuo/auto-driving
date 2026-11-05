"""Format deployment results as text or JSON.

Each renderer takes the result its operation returned. Formatting does not
package a release, open a connection, or start a host.
"""

from __future__ import annotations

import json
import shlex
from typing import Any

from ...providers.metadata import PROVIDERS
from ...shared.paths import display_path
from ..host_formatting import host_block, vehicle_label
from ..progress import ProgressEvent
from ..runs.render import render_stop_result
from ..step_formatting import step_lines
from .models import CreateResult, DeleteResult, UpdateResult


def render_update(result: UpdateResult, *, json_output: bool) -> str:
    """The update receipt, or the sentence an error shows."""

    if result.kind == "error":
        return result.message
    if json_output:
        return json.dumps(result.receipt, indent=2, sort_keys=True)
    return _update_text(result)


def render_create(result: CreateResult, *, json_output: bool) -> str:
    """The created host or its plan, or the sentence an error shows.

    A plan prints the create envelope. ``--json`` prints that same envelope
    for a host that was created, and the text block otherwise.
    """

    if result.kind == "error":
        return result.message
    if json_output or result.kind == "plan":
        return _create_envelope(result)
    if result.kind == "created":
        return _format_created_host(result)
    raise ValueError(f"Unknown create outcome: {result.kind}")


def render_delete(result: DeleteResult) -> str:
    if result.kind == "error":
        return result.message
    if result.kind == "stop_failed":
        return render_stop_result(result.stop)
    if result.kind == "delete_failed":
        return "Could not stop the PiCar host; its local record was kept."
    if result.kind == "deleted":
        stopped = "Stopped the run. " if result.stopped_run else ""
        suffix = f" Harness files stay in {result.harness_dir} on the PiCar." if result.harness_dir else ""
        return f"{stopped}Stopped host {result.host_name}. Removed {display_path(result.deleted_path)}.{suffix}"
    raise ValueError(f"Unknown delete outcome: {result.kind}")


def _create_envelope(result: CreateResult) -> str:
    """One receipt for a plan and for ``hosts create --json``."""

    return json.dumps({
        "schema": "automa_host_create",
        "configuration": result.configuration,
        "dry_run": result.kind == "plan",
        "core": None if result.core is None else result.core.receipt,
        "release": None if result.release is None else result.release.receipt,
        "host_state": result.host_state,
    }, indent=2, sort_keys=True)


def _update_text(result: UpdateResult) -> str:
    body = result.receipt or {}
    if body.get("dry_run") or result.kind == "receipt":
        return json.dumps(body, indent=2, sort_keys=True)
    if result.kind == "local":
        return _format_local_autonomy(body, host_present=result.host_present)
    if result.kind == "remote":
        return _format_autonomy_update(body)
    raise ValueError(f"Unknown update outcome: {result.kind}")


def _format_local_autonomy(body: dict[str, Any], *, host_present: bool | None) -> str:
    summary = body["release"]
    if body["restarted"]:
        host_line = "Runtime restarted: yes"
    elif not host_present:
        host_line = "Runtime restarted: no host running; the next run starts on this release"
    else:
        host_line = "Runtime restarted: no; an idle host moves onto this release at the next run"
    return "\n".join([
        f"Host updated: {body['host_name']} (local host)",
        f"Release: {summary['archive']}",
        f"Archive SHA-256: {summary['archive_sha256']}",
        *step_lines(body["steps"], body["presets"]),
        f"Decision generation: {body['generation_id']}",
        host_line,
    ])


def _format_created_host(result: CreateResult) -> str:
    configuration = result.configuration
    detail = "\n".join(
        line for line in _update_text(result.release).splitlines()
        if not line.startswith(("Host updated:", "Runtime restarted:"))
    )
    lines = [f"Host {configuration['host_name']} created for {vehicle_label(configuration)}", detail]
    state = result.host_state
    if state:
        vehicle = PROVIDERS[configuration["provider"]]
        url = configuration["connection"][vehicle.url_key]
        lines.append(host_block({
            **state,
            "host_name": configuration["host_name"],
            "attached": True,
            "provider": configuration["provider"],
            "vehicle_url": vehicle.operator_url(url),
        }))
    return "\n".join(lines)


def _format_autonomy_update(payload: dict[str, Any]) -> str:
    restart = payload["restart_requested"]
    verified_lines: list[str] = []
    if payload["runtime_verification"] is not None:
        verified_lines.append(
            "Runtime verified: every deployed step runs its staged plugins; "
            "drive mode remains manual"
        )
    return "\n".join(
        [
            f"Host updated: {payload['host_name']} -> {payload['target']['ssh_target']}",
            f"Release: {payload['release_id']}",
            f"Tree SHA-256: {payload['release']['tree_sha256']}",
            *step_lines(payload["activation"]["steps"], payload["activation"]["presets"]),
            f"Decision generation: {payload['activation']['generation_id']}",
            f"Runtime restarted: {'yes' if restart else 'no'}",
            *verified_lines,
            *(
                []
                if restart
                else [
                    "A running runtime selects changed staged plugins on its next frame; "
                    "changed plugin specs or configs need --restart."
                ]
            ),
        ]
    )


def render_progress(event: ProgressEvent) -> str:
    data = event.data
    if event.kind == "step":
        return f"==> {data['label']}"
    if event.kind == "command":
        command = shlex.join(data["command"]) if data.get("quoted") else " ".join(data["command"])
        prefix = f"{data['prefix']} " if data.get("prefix") else ""
        return f"$ {prefix}{command}"
    if event.kind == "output":
        return data["line"]
    if event.kind == "selected":
        return f"Selected {data['host_name']} ({data['provider']}) at {data['ssh_target']}."
    if event.kind == "scope":
        scope = ("core Donkey/harness bundle" if data["scope"] == "core"
                 else "versioned autonomy controller bundle and activation metadata")
        return f"Scope: {scope} only."
    if event.kind == "remote_home":
        return f"Remote home: {data['path']}"
    if event.kind == "core_synced":
        return "Core deploy bundle synced."
    if event.kind == "readiness_failed":
        return f"HTTP readiness failed: {data['error']}"
    if event.kind == "ready":
        return f"Donkey runtime ready: {data['url']}"
    if event.kind == "archive":
        return f"Archive: {display_path(data['path'])}"
    if event.kind == "tree_hash":
        return f"Tree SHA-256: {data['sha256']}"
    if event.kind == "archive_hash":
        return f"Archive SHA-256: {data['sha256']}"
    if event.kind == "extracted":
        return f"Extracted release -> {data['path']}"
    vendor_labels = {
        "vendor_ready": "DonkeyCar vendor ready",
        "vendor_registered": "Registered existing DonkeyCar vendor",
        "vendor_patched": "Patched existing DonkeyCar vendor",
        "vendor_replacing": "Replacing stale DonkeyCar vendor checkout",
        "vendor_prepared": "Prepared DonkeyCar vendor",
    }
    if event.kind in vendor_labels:
        return f"{vendor_labels[event.kind]}: {display_path(data['path'])}"
    raise ValueError(f"Unknown deployment progress: {event.kind}")
