"""Format a deployment receipt as text or JSON.

The receipt is supplied by the caller. Formatting does not package a release,
open a connection, or start a host.
"""

from __future__ import annotations

import json
import shlex
from typing import Any

from ...providers.metadata import PROVIDERS
from ...shared.paths import display_path
from ...shared.progress import ProgressEvent
from ..host_formatting import host_block
from ..runs.render import render_stop_result
from ..step_formatting import step_lines
from .models import DeploymentResult


def present_deployment(result: DeploymentResult, *, json_output: bool) -> str:
    """The command's printable receipt. A diagnostic has no receipt."""

    if result.render_kind == "stop_failed":
        return render_stop_result(result.stop)
    if result.render_kind == "delete_failed":
        return "Could not stop the PiCar host; its local record was kept."
    if result.render_kind == "deleted":
        stopped = "Stopped the run. " if result.stopped_run else ""
        suffix = "" if result.local else " Harness files stay in ~/mycar on the PiCar."
        return f"{stopped}Stopped host {result.host_name}. Removed {display_path(result.deleted_path)}.{suffix}"
    if result.exit_code or result.receipt is None:
        return result.message
    if json_output:
        return _json_receipt(result.receipt)
    return _text_receipt(result)


def _json_receipt(receipt: dict[str, Any]) -> str:
    """Create combines child receipts. Those children were previously dumped
    with sorted keys and then embedded; the outer document keeps its key order.
    """

    if "configuration" in receipt and any(key in receipt for key in ("core", "host", "result")):
        body = dict(receipt)
        for key in ("core", "host", "result"):
            if isinstance(body.get(key), dict):
                body[key] = json.loads(json.dumps(body[key], sort_keys=True))
        return json.dumps(body, indent=2)
    return json.dumps(receipt, indent=2, sort_keys=True)


def _text_receipt(result: DeploymentResult) -> str:
    body = result.receipt or {}
    if result.render_kind == "core_update":
        return _format_core_dry_run(body)
    if result.render_kind == "remote_update":
        if body.get("dry_run"):
            return _format_autonomy_dry_run(body)
        return _format_autonomy_update(body)
    if result.render_kind == "create_plan":
        core = _format_core_dry_run(body["core"]) if body.get("core") else body.get("core_error", "")
        host = _format_autonomy_dry_run(body["host"]) if body.get("host") else body.get("host_error", "")
        return core + "\n" + host
    if result.render_kind == "create":
        return _format_created_host(body, child_kind=result.child_render_kind)
    if result.render_kind == "local_update":
        return _format_local_autonomy(body, host_present=result.host_present)
    return json.dumps(body, indent=2, sort_keys=True)


def _format_local_autonomy(body: dict[str, Any], *, host_present: bool | None) -> str:
    if body.get("dry_run") and "source" in body and "release" not in body:
        source = body["source"]
        return "\n".join([
            f"Dry run: would package {body['host_name']}'s controller release "
            f"(tree {source['tree_sha256'][:12]}, {source['file_count']} files)",
            f"Restart the host onto it: {'yes' if body['restart'] else 'no'}",
        ])
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


def _format_created_host(body: dict[str, Any], *, child_kind: str) -> str:
    configuration = body["configuration"]
    rendered = _text_receipt(DeploymentResult(0, receipt=body["result"], render_kind=child_kind))
    detail = "\n".join(
        line for line in rendered.splitlines()
        if not line.startswith(("Host updated:", "Runtime restarted:"))
    )
    lines = [f"Host {configuration['host_name']} created for {_vehicle_label(configuration)}", detail]
    state = body.get("host")
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


def _vehicle_label(configuration: dict[str, Any]) -> str:
    vehicle = PROVIDERS[configuration["provider"]]
    return f"the {vehicle.label} at {vehicle.operator_url(configuration['connection'][vehicle.url_key])}"


def _format_core_dry_run(payload: dict[str, Any]) -> str:
    source = payload["source"]
    vendor = source["vendor"]
    return "\n".join(
        [
            f"Core update dry run for {payload['host_name']}",
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
            f"Autonomy update dry run for {payload['host_name']}",
            (
                f"target: {payload['target']['provider']} at "
                f"{payload['target']['ssh_target']}; home={payload['target']['pi_home']}"
            ),
            f"scope: {payload['scope']['description']}",
            "outcome: plan only; no files written and no remote connection attempted",
            f"source tree SHA-256: {payload['source']['tree_sha256']}",
            f"source files: {payload['source']['file_count']}",
            "activation: " + " ".join(
                f"{step}={payload['activation']['presets'].get(step) or ','.join(plugins) or '-'}"
                for step, plugins in payload["activation"]["steps"].items()
            ),
            "planned commands:",
            *[f"- {entry['step']}: {entry['command']}" for entry in payload["commands"]],
            f"restart requested: {'yes' if payload['restart_requested'] else 'no'}",
        ]
    )


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
