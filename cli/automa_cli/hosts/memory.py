"""Report memory's staged selection, runner schema and live status, and reset it.

Offline inspect lives in ``step_inspect``. Stream and probe live in
``streaming``, beside perception's.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from autonomy.decision_cycle.memory.interface import EPOCH_ID, HEALTH, RECORD_COUNT
from autonomy.runtime.client import RuntimeClient

from ..shared.command_result import CommandResult
from .memory_report import ledger_is_empty
from .runtime_hosts import runtime_base_url
from .bundles import controller_bundle_paths
from ..shared.paths import ROOT, safe_path_part
from .step_schema import format_staged_step, staged_step_info
from .streaming import _format_live_memory_screen, _live_plugins, _live_step_fields, probe_live_memory

MEMORY_RESET_SCHEMA = "host_memory_reset"
RUNTIME_ROOT = Path(os.environ.get("AUTOMA_RUNTIME_ROOT", ROOT / "runtime" / "hosts"))


def get_host_memory_info(
    *,
    host_name: str,
    json_output: bool = False,
    include_live: bool = True,
    timeout_s: float = 3.0,
) -> CommandResult:
    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(host_name))
    staged, error = staged_step_info(bundle, host_name, "memory")
    if error is not None:
        return CommandResult(2, error)
    payload: dict[str, Any] = {
        "schema": "host_memory_info",
        "host_name": host_name,
        **staged,
        "live": None,
    }
    if include_live:
        payload["live"] = probe_live_memory(
            host_name=host_name,
            timeout_s=timeout_s,
        )
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(0, _format_memory_info(payload))


def _format_memory_info(payload: dict[str, Any]) -> str:
    lines = format_staged_step("memory", payload)
    live = payload.get("live")
    if isinstance(live, dict):
        lines.append("")
        lines.append(_format_live_memory_screen(host_name=payload["host_name"], live=live))
    return "\n".join(lines)


def reset_host_memory(
    *,
    host_name: str,
    timeout_s: float = 3.0,
    json_output: bool = False,
) -> CommandResult:
    """Reset live memory on the host.

    The host answers with the memory step's status under the same lock as
    its cycles, before any later frame refills memory. The reset is confirmed
    when, in that answer, every applied plugin started a new epoch or holds
    no records; ``unconfirmed_plugin_ids`` names any that did neither.
    Operators can check with ``info memory``, ``plugins stream --step memory``,
    or the Memory map.
    """

    before = probe_live_memory(host_name=host_name, timeout_s=timeout_s)
    if before.get("status") != "live":
        return CommandResult(
            2,
            "\n".join(
                [
                    f"Cannot reset memory for {host_name!r}: live memory is {before.get('status')!r}.",
                    str(before.get("error") or "The runtime host reported no memory step."),
                ]
            ),
        )

    try:
        base_url = runtime_base_url(host_name)
        reset_payload = RuntimeClient(base_url, timeout_s=timeout_s).reset_memory()
    except RuntimeError as exc:
        return CommandResult(2, f"Memory reset failed for {host_name}: {exc}")

    payload: dict[str, Any] = {
        "schema": MEMORY_RESET_SCHEMA,
        "host_name": host_name,
        "ok": bool(reset_payload.get("ok")),
        "reset": reset_payload,
        "before": before,
    }
    if not payload["ok"]:
        if json_output:
            return CommandResult(2, json.dumps(payload, indent=2, sort_keys=True))
        return CommandResult(
            2,
            "\n".join(
                [
                    f"Memory reset failed: {host_name}",
                    str(reset_payload.get("error") or reset_payload.get("status") or "unknown error"),
                ]
            ),
        )

    step = reset_payload.get("memory") if isinstance(reset_payload.get("memory"), dict) else {}
    after = {"status": "live", **_live_step_fields("memory", step)}
    payload["after"] = after
    before_plugins = {entry["plugin_id"]: entry for entry in _live_plugins(before)}
    after_plugins = {entry["plugin_id"]: entry for entry in _live_plugins(after)}
    unconfirmed = [
        plugin_id
        for plugin_id, now in after_plugins.items()
        if not ledger_is_empty(now)
        and now.get(EPOCH_ID) == before_plugins.get(plugin_id, {}).get(EPOCH_ID)
    ]
    confirmed = bool(step) and not unconfirmed
    payload["confirmed"] = confirmed
    payload["unconfirmed_plugin_ids"] = unconfirmed
    if json_output:
        return CommandResult(0 if confirmed else 2, json.dumps(payload, indent=2, sort_keys=True))
    lines = [
        f"Reset memory: {host_name}",
        f"Plugins: {', '.join(after.get('plugin_ids') or before.get('plugin_ids') or []) or '—'}",
    ]
    for plugin_id in [*after_plugins, *(key for key in before_plugins if key not in after_plugins)]:
        was = before_plugins.get(plugin_id, {})
        now = after_plugins.get(plugin_id, {})
        lines.append(
            f"  {plugin_id}: "
            f"epoch {was.get(EPOCH_ID) or '—'} -> {now.get(EPOCH_ID) or '—'}, "
            f"records {was.get(RECORD_COUNT)} -> {now.get(RECORD_COUNT)}, "
            f"health {was.get(HEALTH)} -> {now.get(HEALTH)}"
        )
    lines.extend(
        [
            f"Evidence publisher: {before.get('evidence_publisher') or 'none'} -> "
            f"{after.get('evidence_publisher') or 'none'}",
            f"Resets: {before.get('reset_count')} -> {after.get('reset_count')}",
        ]
    )
    if not confirmed:
        detail = (
            f" Same epoch and still holding records: {', '.join(unconfirmed)}."
            if unconfirmed
            else " The host answered without the memory step's status."
        )
        lines.append(f"Warning: the host's answer does not confirm the reset.{detail}")
        return CommandResult(2, "\n".join(lines))
    return CommandResult(0, "\n".join(lines))
