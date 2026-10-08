"""Proposal step info over its staged contract and live runner.

Like ``perception`` and ``memory``, this module owns the step's info reader and
formatter and uses ``step_schema`` for the shared staged-step contract. The
proposal view and plan/action context come from the combined decision surface.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Any

from autonomy.decision_cycle.action.hold import HOLD_IDLE_REASON, HoldAction
from implementations.decision_cycle.action.mode.plugin import LIVE_MODES, ModeAction

from .bundles import controller_bundle_paths
from .decision import COMBINED_VIEW_ID, CommandResult, DecisionSurfaceError, load_decision_identity
from .paths import ROOT, safe_path_part
from .step_schema import format_staged_step, staged_step_info

RUNTIME_ROOT = Path(os.environ.get("AUTOMA_RUNTIME_ROOT", ROOT / "runtime" / "vehicles"))


def get_vehicle_proposal_info(
    *,
    vehicle_id: str,
    json_output: bool = False,
    include_live: bool = True,
    timeout_s: float = 3.0,
) -> CommandResult:
    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))
    staged, error = staged_step_info(bundle, vehicle_id, "proposal")
    if error is not None:
        return CommandResult(2, error)
    try:
        identity = load_decision_identity(bundle)
    except DecisionSurfaceError as err:
        return CommandResult(err.exit_code, err.message_text)
    steps = identity["steps"]
    plan_plugins = (steps.get("plan") or {}).get("plugins") or []
    action_plugins = (steps.get("action") or {}).get("plugins") or []

    from .decision_view import get_decision_view_status
    from .view_discovery import discover_runtime_view

    view = discover_runtime_view(
        vehicle_id,
        lambda directory: get_decision_view_status(
            automation_dir=directory, vehicle_id=vehicle_id, activation=identity,
        ),
        runtime_root=RUNTIME_ROOT,
    )
    payload: dict[str, Any] = {
        "schema": "vehicle_proposal_info_v1",
        "vehicle_id": vehicle_id,
        **staged,
        "decision": {
            "generation_id": identity["generation_id"],
            "selector_id": plan_plugins[0] if plan_plugins else None,
            "authority": _action_authority_description(
                action_plugins[0] if action_plugins else None
            ),
        },
        "published_view": {
            "view_id": COMBINED_VIEW_ID,
            **view,
            "launch_command": (
                "./cli/automa vehicles decision inspect --id "
                + shlex.quote(vehicle_id) + " --from-run <sequence.json> --open"
            ),
        },
        "live": None,
    }
    if include_live:
        from .streaming import probe_live_step

        payload["live"] = probe_live_step("proposal", vehicle_id=vehicle_id, timeout_s=timeout_s)
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(0, _format_proposal_info(payload))


def _action_authority_description(plugin_id: str | None) -> dict[str, Any]:
    """How the staged action plugin authorizes control, for operator-facing summaries."""

    if plugin_id == HoldAction.plugin_id:
        return {
            "gate_id": HoldAction.plugin_id,
            "proposed_applied": False,
            "authorized_idle_reason": HOLD_IDLE_REASON,
        }
    if plugin_id == ModeAction.plugin_id:
        return {
            "gate_id": ModeAction.plugin_id,
            "proposed_applied": f"in {'/'.join(sorted(LIVE_MODES))} drive modes",
            "authorized_idle_reason": None,
        }
    return {"gate_id": plugin_id, "proposed_applied": None, "authorized_idle_reason": None}


def _format_proposal_info(payload: dict[str, Any]) -> str:
    view = payload["published_view"]
    if view.get("available"):
        view_line = f"Decision view: {view['url']}"
    else:
        view_line = (
            f"Decision view: unavailable ({view.get('reason')}); run automation, "
            f"or open saved input with `{view['launch_command']}`"
        )
    decision = payload["decision"]
    authority = decision["authority"]
    lines = [
        *format_staged_step("proposal", payload, view=view_line),
        "",
        f"Decision: generation={decision['generation_id']}",
        f"- plan: {decision['selector_id']}",
        (
            f"- action: {authority.get('gate_id')} "
            f"proposed_applied={authority.get('proposed_applied')} "
            f"idle_reason={authority.get('authorized_idle_reason')}"
        ),
    ]
    live = payload.get("live")
    if isinstance(live, dict):
        from .streaming import format_live_step_screen

        lines.extend(["", format_live_step_screen("proposal", vehicle_id=payload["vehicle_id"], live=live)])
    return "\n".join(lines)
