"""Report memory's staged selection, runner schema and live status.

Offline inspect lives in ``step_inspect``. Stream and probe live in
``streaming``, beside perception's.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..shared.command_result import CommandResult
from .bundles import controller_bundle_paths
from ..shared.paths import ROOT, safe_path_part
from .step_schema import format_staged_step, staged_step_info
from .streaming import _format_live_memory_screen, probe_live_memory

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
