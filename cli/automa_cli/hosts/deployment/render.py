"""Format a deployment receipt as text or JSON.

The receipt is supplied by the caller. Formatting does not package a release,
open a connection, or start a host.
"""

from __future__ import annotations

import json
from typing import Any

from ..host_status import host_block
from ..step_activations import step_lines
from ...providers.catalog import PROVIDERS
from .models import DeploymentResult


def present_deployment(result: DeploymentResult, *, json_output: bool) -> str:
    """The command's printable receipt. A diagnostic has no receipt."""

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
    if body.get("schema") == "host_core_update":
        return _format_core_dry_run(body)
    if body.get("schema") == "host_autonomy_update":
        if body.get("dry_run"):
            return _format_autonomy_dry_run(body)
        return _format_autonomy_update(body)
    if body.get("dry_run") and ("core" in body or "host" in body):
        core = _format_core_dry_run(body["core"]) if body.get("core") else body.get("core_error", "")
        host = _format_autonomy_dry_run(body["host"]) if body.get("host") else body.get("host_error", "")
        return core + "\n" + host
    if "configuration" in body and "result" in body:
        return _format_created_host(body)
    return _format_local_autonomy(body, host_present=result.host_present)


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


def _format_created_host(body: dict[str, Any]) -> str:
    configuration = body["configuration"]
    rendered = _text_receipt(DeploymentResult(0, receipt=body["result"]))
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
