"""Deterministic M008 workbench probe.

Run from a historical checkout with::

    python3 qca/experiments/workbench_probe.py

The probe uses the public ``ImageReplayRunner`` and ``WorkbenchServer`` APIs,
plus the documented ``./cli/automa vehicles workbench replay --json`` command.
It creates a temporary three-frame ``manifest.json`` capture with a fixed
source/frame identity, exercises empty and selected plugin states, observes
memory output, resets the runner, and repeats one run through the loopback API.
It does not open Chrome and makes no browser/UI acceptance claim.

Output schema: ``qca/verification/workbench-probe/v1``.  The trace is a
comparison aid, not a quality score.  Normalization is deliberately explicit:
the fields ``run_id``, ``server_identity``, ``started_at_ms``,
``completed_at_ms``, ``duration_ms``, ``last_duration_ms``, ``at_ms``,
``port``, and ``url`` are replaced with ``<volatile>``; temporary capture
paths are replaced with ``<synthetic-capture>``; the checkout root is replaced
with ``<checkout>``; and embedded ``duration_ms=<number>`` text is replaced.
All other fields—including frame IDs, source timestamps, plugin/catalog
digests, memory counts, phases, failures, and cleanup flags—are retained.
The normalization list is emitted in the result so consumers do not mistake
omitted identity/timing fields for product behavior.
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen


PROBE_SCHEMA = "qca/verification/workbench-probe/v1"
_VOLATILE_KEYS = {
    "run_id",
    "server_identity",
    "started_at_ms",
    "completed_at_ms",
    "duration_ms",
    "last_duration_ms",
    "at_ms",
    "port",
    "url",
}
_DURATION_IN_TEXT = re.compile(r"duration_ms=\d+(?:\.\d+)?")


def _repository_root() -> Path:
    # refine_m008 executes this file from the current tree with cwd set to the
    # historical checkout; product imports and the CLI must use that checkout.
    return Path.cwd().resolve()


def _make_capture(root: Path) -> Path:
    """Create a fixed-identity image-directory capture inside ``root``."""

    from PIL import Image

    capture = root / "capture"
    capture.mkdir()
    colors = ((18, 40, 72), (44, 86, 34), (118, 52, 24))
    frames = []
    for index, color in enumerate(colors):
        name = f"probe_{index:02d}.png"
        Image.new("RGB", (48, 32), color).save(capture / name)
        frames.append(
            {
                "frame_id": f"probe-frame-{index}",
                "frame_index": index,
                "timestamp_ms": (index + 1) * 1000,
                "image_path": name,
                "annotation": {"fixture": "qca-workbench-probe", "index": index},
            }
        )
    (capture / "manifest.json").write_text(
        json.dumps(
            {
                "source_id": "qca-workbench-probe",
                "frames": frames,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return capture


def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise RuntimeError("workbench probe condition did not become true before timeout")


def _path_forms(path: Path) -> list[str]:
    """Return equivalent absolute spellings so macOS /private/var matches /var."""

    forms = {str(path), str(path.resolve())}
    extra: set[str] = set()
    for item in forms:
        if item.startswith("/private/var/"):
            extra.add(item[len("/private") :])
        elif item.startswith("/var/"):
            extra.add("/private" + item)
    forms.update(extra)
    return sorted(forms, key=len, reverse=True)


def _replace_path_prefix(value: str, path: Path, token: str) -> str:
    for form in _path_forms(path):
        if value == form:
            return token
        prefix = form + os.sep
        if value.startswith(prefix):
            return token + "/" + value[len(prefix) :].replace(os.sep, "/")
        prefix = form + "/"
        if value.startswith(prefix):
            return token + "/" + value[len(prefix) :]
    return value


def _normalize(value: Any, *, capture: Path, checkout: Path | None = None) -> Any:
    """Replace documented volatile fields, timings, and host-local paths."""

    checkout = (checkout or Path.cwd()).resolve()
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if key in _VOLATILE_KEYS:
                normalized[key] = "<volatile>"
            else:
                normalized[key] = _normalize(item, capture=capture, checkout=checkout)
        return normalized
    if isinstance(value, list):
        return [_normalize(item, capture=capture, checkout=checkout) for item in value]
    if isinstance(value, tuple):
        return [_normalize(item, capture=capture, checkout=checkout) for item in value]
    if isinstance(value, str):
        replaced = _replace_path_prefix(value, capture, "<synthetic-capture>")
        replaced = _replace_path_prefix(replaced, checkout, "<checkout>")
        return _DURATION_IN_TEXT.sub("duration_ms=<volatile>", replaced)
    return value


def _record(
    trace: list[dict[str, Any]],
    name: str,
    payload: Any,
    *,
    capture: Path,
    action: str | None = None,
) -> Any:
    item: dict[str, Any] = {"name": name}
    if action is not None:
        item["action"] = action
    item["state"] = _normalize(copy.deepcopy(payload), capture=capture)
    trace.append(item)
    return payload


def _dispatch(
    runner: Any,
    action: str,
    *,
    trace: list[dict[str, Any]],
    capture: Path,
    **kwargs: Any,
) -> dict[str, Any] | None:
    try:
        state = runner.dispatch(action, **kwargs)
    except Exception as exc:  # product boundary is recorded for comparison
        state = {
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "boundary": getattr(exc, "boundary", None),
            },
            "state": runner.state(),
        }
        _record(trace, f"runner.{action}.error", state, capture=capture, action=action)
        return None
    _record(trace, f"runner.{action}", state, capture=capture, action=action)
    return state


def _select_ready_plugin(catalog: Any) -> str | None:
    for plugin in getattr(catalog, "plugins", ()):
        if bool(getattr(plugin, "ready", False)):
            plugin_id = getattr(plugin, "plugin_id", None)
            if isinstance(plugin_id, str) and plugin_id:
                return plugin_id
    return None


def _run_runner_trace(capture: Path, plugin_dir: Path | None) -> list[dict[str, Any]]:
    from cli.automa_cli.workbench import ImageReplayRunner

    trace: list[dict[str, Any]] = []
    runner = ImageReplayRunner(
        cadence_ms=0,
        loop=False,
        plugin_dir=plugin_dir,
        active_plugin_ids=[],
    )
    try:
        catalog = runner.plugin_catalog
        _record(
            trace,
            "runner.catalog",
            {
                "digest": getattr(catalog, "digest", None),
                "plugins": [
                    {
                        "id": getattr(plugin, "plugin_id", None),
                        "ready": bool(getattr(plugin, "ready", False)),
                    }
                    for plugin in getattr(catalog, "plugins", ())
                ],
            },
            capture=capture,
        )

        raw_start = runner.start(capture, cadence_ms=0)
        raw_run_id = raw_start.get("run_id")
        _record(trace, "runner.start.raw", raw_start, capture=capture, action="start")
        raw_state = runner.wait(10)
        _record(trace, "runner.complete.raw", raw_state, capture=capture)

        # Empty selection is a normal, explicit capture mode.  It should still
        # produce observations/memory state without invoking a selected plugin.
        _dispatch(
            runner,
            "reset",
            run_id=raw_run_id,
            trace=trace,
            capture=capture,
        )
        plugin_id = _select_ready_plugin(catalog)
        selected = [plugin_id] if plugin_id is not None else []
        _dispatch(
            runner,
            "select_plugins",
            active_plugin_ids=selected,
            trace=trace,
            capture=capture,
        )

        # Keep the worker active long enough to exercise a live toggle and a
        # paused step.  These calls are all server-owned public actions.
        live_start = runner.start(capture, cadence_ms=5000)
        live_run_id = live_start.get("run_id")
        _record(trace, "runner.start.selected", live_start, capture=capture, action="start")
        _wait_until(lambda: bool(runner.state().get("timeline")))
        _record(trace, "runner.running.selected", runner.state(), capture=capture)
        _dispatch(
            runner,
            "select_plugins",
            run_id=live_run_id,
            active_plugin_ids=[],
            trace=trace,
            capture=capture,
        )
        _dispatch(
            runner,
            "pause",
            run_id=live_run_id,
            trace=trace,
            capture=capture,
        )
        _dispatch(
            runner,
            "step",
            run_id=live_run_id,
            trace=trace,
            capture=capture,
        )
        _dispatch(
            runner,
            "cancel",
            run_id=live_run_id,
            trace=trace,
            capture=capture,
        )
        _record(trace, "runner.cancelled", runner.state(), capture=capture)

        # A fresh selected run records memory effects and a terminal cleanup;
        # the final reset records the persistent idle state after lifecycle.
        _dispatch(
            runner,
            "reset",
            run_id=live_run_id,
            trace=trace,
            capture=capture,
        )
        _dispatch(
            runner,
            "select_plugins",
            active_plugin_ids=selected,
            trace=trace,
            capture=capture,
        )
        selected_start = runner.start(capture, cadence_ms=0)
        selected_run_id = selected_start.get("run_id")
        _record(trace, "runner.start.selected_complete", selected_start, capture=capture, action="start")
        selected_state = runner.wait(10)
        _record(trace, "runner.complete.selected", selected_state, capture=capture)
        _dispatch(
            runner,
            "reset",
            run_id=selected_run_id,
            trace=trace,
            capture=capture,
        )
        _record(trace, "runner.reset.final", runner.state(), capture=capture)
    finally:
        runner.close()
    return trace


def _get_json(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _run_api_trace(capture: Path) -> list[dict[str, Any]]:
    from cli.automa_cli.workbench import ImageReplayRunner, WorkbenchServer

    trace: list[dict[str, Any]] = []
    runner = ImageReplayRunner(cadence_ms=0, loop=False)
    server = WorkbenchServer(runner).start()
    try:
        base = server.url
        if not base:
            raise RuntimeError("workbench server did not expose a loopback URL")
        health = _get_json(base + "api/health")
        _record(trace, "api.health", health, capture=capture)
        started = _post_json(
            base + "api/action",
            {"action": "start", "source_dir": str(capture), "cadence_ms": 0},
        )
        _record(trace, "api.action.start", started, capture=capture, action="start")
        run_id = ((started.get("state") or {}).get("run_id"))
        completed = runner.wait(10)
        _record(trace, "api.state.completed", _get_json(base + "api/state"), capture=capture)
        if completed.get("run_id") == run_id:
            reset = _post_json(base + "api/action", {"action": "reset", "run_id": run_id})
            _record(trace, "api.action.reset", reset, capture=capture, action="reset")
    finally:
        server.stop()
    return trace


def _run_cli_trace(root: Path, capture: Path) -> dict[str, Any]:
    command = [
        str(root / "cli" / "automa"),
        "vehicles",
        "workbench",
        "replay",
        str(capture),
        "--cadence-ms",
        "0",
        "--json",
    ]
    result = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )
    payload: Any = None
    parse_error = None
    if result.stdout.strip():
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            parse_error = str(exc)
    return {
        "command": command,
        "returncode": result.returncode,
        "stdout": payload,
        "stderr": result.stderr,
        "parse_error": parse_error,
        "claim": "CLI machine-readable result only; no browser/UI claim",
    }


def run_probe() -> dict[str, Any]:
    root = _repository_root()
    # Put both package roots on sys.path so direct execution works from a
    # checkout without requiring installation.
    for item in (root, root / "cli"):
        value = str(item)
        if value not in sys.path:
            sys.path.insert(0, value)

    with tempfile.TemporaryDirectory(prefix="qca-workbench-probe-") as temporary:
        workspace = Path(temporary)
        capture = _make_capture(workspace)
        plugin_dir = root / "lab" / "plugins" / "perception"
        plugin_root = plugin_dir if plugin_dir.is_dir() else None
        runner_trace = _run_runner_trace(capture, plugin_root)
        api_trace = _run_api_trace(capture)
        cli_trace = _run_cli_trace(root, capture)
        payload = {
            "schema": PROBE_SCHEMA,
            "supported_interface": {
                "runner": "cli.automa_cli.workbench.ImageReplayRunner",
                "server": "cli.automa_cli.workbench.WorkbenchServer",
                "cli": "./cli/automa vehicles workbench replay <source> --json",
                "capture": "manifest.json with deterministic synthetic PNG frames",
                "browser_claim": False,
            },
            "normalization": {
                "volatile_keys_replaced": sorted(_VOLATILE_KEYS),
                "temporary_capture_path_replaced": True,
                "checkout_path_replaced": True,
                "embedded_duration_text_replaced": True,
                "preserved": [
                    "frame IDs and source timestamps",
                    "plugin selection and catalog digest",
                    "phase/progress/perception/memory results",
                    "failure and cleanup flags",
                ],
            },
            "capture": {
                "source_id": "qca-workbench-probe",
                "frame_ids": ["probe-frame-0", "probe-frame-1", "probe-frame-2"],
                "frame_timestamps_ms": [1000, 2000, 3000],
                "plugin_dir_available": plugin_root is not None,
            },
            "traces": {
                "runner": _normalize(runner_trace, capture=capture),
                "api": _normalize(api_trace, capture=capture),
                "cli": _normalize(cli_trace, capture=capture),
            },
            "claims": {
                "runner_api_exercised": True,
                "loopback_api_exercised": True,
                "cli_json_exercised": True,
                "browser_ui_measured": False,
                "observation_only": True,
            },
        }
    return payload


def main() -> int:
    try:
        print(json.dumps(run_probe(), indent=2, sort_keys=True))
    except Exception as exc:  # keep failures machine-readable for historical runs
        print(
            json.dumps(
                {
                    "schema": PROBE_SCHEMA,
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                    "claims": {"browser_ui_measured": False},
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
