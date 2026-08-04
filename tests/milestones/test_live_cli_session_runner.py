from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = (
    ROOT
    / "docs"
    / "milestones"
    / "007-cli-operator-usability"
    / "tools"
    / "live-cli-session-runner"
    / "session_runner.py"
)
CATALOGS = RUNNER_PATH.parent / "catalogs"
VEHICLE = "chase-sim-chaser"


def _load_runner_module():
    name = "live_cli_session_runner"
    # Always reload so tests see the latest file.
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, RUNNER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _session_fp(**overrides):
    base = {
        "game_id": "chase",
        "scenario_id": "default",
        "simulation_epoch": "chase-run:abc",
        "playback": {"frameIndex": 10, "pendingAction": False, "phase": "running"},
        "control_source": "programmatic",
        "control_input": None,
    }
    base.update(overrides)
    return base


def _status_with_passive(
    *,
    vehicle_id: str = VEHICLE,
    worker_state: str = "running",
    view_state: str = "available",
    deployment: str = "deployed",
    fingerprint: dict | None = None,
    mutation_attempted: bool = False,
    preserved: bool = True,
    recording: bool = False,
    applied: bool = False,
    changed_fields: list | None = None,
    unknown_fields: list | None = None,
    pid: int | None = 4242,
    omit_schema: bool = False,
    omit_vehicle_id: bool = False,
) -> dict:
    fp = fingerprint or _session_fp()
    payload = {
        "schema": "automa_vehicle_status_v1",
        "vehicle_id": vehicle_id,
        "layers": {
            "simulator_server": {"state": "reachable"},
            "simulator_frontend": {"state": "connected"},
            "chase_game": {"state": "ready"},
            "vehicle": {"state": "discoverable"},
            "passive_capture": {
                "state": "available",
                "mutation_attempted": mutation_attempted,
                "session_preservation": {
                    "preserved": preserved,
                    "changed_fields": [] if changed_fields is None else changed_fields,
                    "unknown_fields": [] if unknown_fields is None else unknown_fields,
                    "before": {
                        k: fp[k]
                        for k in (
                            "game_id",
                            "scenario_id",
                            "simulation_epoch",
                            "playback",
                            "control_source",
                            "control_input",
                        )
                    },
                    "after": {
                        k: fp[k]
                        for k in (
                            "game_id",
                            "scenario_id",
                            "simulation_epoch",
                            "playback",
                            "control_source",
                            "control_input",
                        )
                    },
                },
            },
            "automation_deployment": {"state": deployment},
            "automation_worker": {
                "state": worker_state,
                "details": {
                    "pid": pid,
                    "authority": {
                        "action_policy": "observe_only",
                        "control_application": "not_applied",
                        "recording": recording,
                        "last_frame": {
                            "control": {
                                "applied": applied,
                                "steering": 0.0,
                                "throttle": 0.0,
                            }
                        },
                    },
                },
            },
            "perception_view": {
                "state": view_state,
                "details": {"url": "http://127.0.0.1:8898/"},
            },
        },
    }
    if omit_schema:
        del payload["schema"]
    if omit_vehicle_id:
        del payload["vehicle_id"]
    return payload


def _aggregate(vehicle_id: str = VEHICLE) -> dict:
    card = _status_with_passive(worker_state="stopped", view_state="stale", pid=1111)
    return {
        "schema": "automa_vehicle_status_list_v1",
        "layers": None,
        "vehicles": [card],
    }


def _current_view_payload(**overrides) -> dict:
    payload = {
        "schema": "automa_perception_publication_v1",
        "vehicle_id": VEHICLE,
        "frame": {"frame_id": "chase_frame_1", "frame_index": 1},
        "overlay": {
            "status": "current",
            "source_frame_id": "chase_frame_1",
            "frame_lag": 0,
        },
        "perception": {"things": [{"thing_id": "x"}], "signals": []},
        "cycle": {
            "action_policy": "observe_only",
            "control_application": "not_applied",
        },
        "control": {"applied": False, "steering": 0.0, "throttle": 0.0},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(payload.get(key), dict):
            merged = dict(payload[key])
            merged.update(value)
            payload[key] = merged
        else:
            payload[key] = value
    return payload


class LiveCliSessionRunnerTests(unittest.TestCase):
    def test_runner_script_exists(self) -> None:
        self.assertTrue(RUNNER_PATH.is_file())

    def test_list_catalogs(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(RUNNER_PATH), "--list-catalogs"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_dry_run_cannot_pass_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "session"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER_PATH),
                    "--catalog",
                    str(CATALOGS / "m007-acceptance.yaml"),
                    "--session-dir",
                    str(session_dir),
                    "--repo-root",
                    str(ROOT),
                    "--dry-run",
                    "--non-interactive",
                    "--auto-visual",
                    "pass",
                    "--browser-name",
                    "Chrome",
                    "--browser-version",
                    "999",
                    "--metrics-ui-repo",
                    str(ROOT),
                    "--operator",
                    "test-operator",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
            result = json.loads((session_dir / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["result"], "incomplete")

    def test_digests_match_final_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "session"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER_PATH),
                    "--catalog",
                    str(CATALOGS / "m007-acceptance.yaml"),
                    "--session-dir",
                    str(session_dir),
                    "--repo-root",
                    str(ROOT),
                    "--dry-run",
                    "--non-interactive",
                    "--auto-visual",
                    "skip",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertIn(completed.returncode, {0, 1, 2})
            digests = json.loads((session_dir / "digests.json").read_text(encoding="utf-8"))
            runner = _load_runner_module()
            for entry in digests["artifacts"]:
                path = session_dir / entry["path"]
                self.assertEqual(entry["sha256"], runner._sha256_file(path))

    def test_aggregate_and_wrong_vehicle_extraction(self) -> None:
        runner = _load_runner_module()
        aggregate = _aggregate()
        ok, msg = runner.validate_initial_layers(aggregate, vehicle_id=VEHICLE)
        self.assertTrue(ok, msg)
        # Sole wrong-id card must not be substituted.
        wrong = {
            "layers": None,
            "vehicles": [
                {
                    "schema": "automa_vehicle_status_v1",
                    "vehicle_id": "other",
                    "layers": aggregate["vehicles"][0]["layers"],
                }
            ],
        }
        card = runner.extract_vehicle_status(wrong, VEHICLE)
        self.assertIsNone(card)
        ok, msg = runner.validate_initial_layers(wrong, vehicle_id=VEHICLE)
        self.assertFalse(ok)
        # Aggregate card missing schema is rejected.
        no_schema = {
            "layers": None,
            "vehicles": [
                {
                    "vehicle_id": VEHICLE,
                    "layers": aggregate["vehicles"][0]["layers"],
                }
            ],
        }
        self.assertIsNone(runner.extract_vehicle_status(no_schema, VEHICLE))

    def test_status_identity_required(self) -> None:
        runner = _load_runner_module()
        self.assertIsNone(
            runner.extract_vehicle_status(
                _status_with_passive(omit_schema=True), VEHICLE
            )
        )
        self.assertIsNone(
            runner.extract_vehicle_status(
                _status_with_passive(omit_vehicle_id=True), VEHICLE
            )
        )
        self.assertIsNone(
            runner.extract_vehicle_status(
                _status_with_passive(vehicle_id="other"), VEHICLE
            )
        )
        # Initial baseline rejects a still-running worker.
        running = _status_with_passive(worker_state="running")
        ok, msg = runner.validate_initial_layers(running, vehicle_id=VEHICLE)
        self.assertFalse(ok)
        self.assertIn("running", msg)

    def test_preservation_stable_projection_and_stale_latest(self) -> None:
        runner = _load_runner_module()
        # Real capture shape: frameIndex advances across commands; mode stays fixed.
        initial = runner.extract_session_fingerprint(
            _status_with_passive(
                worker_state="stopped",
                view_state="stale",
                fingerprint=_session_fp(playback={
                    "frameIndex": 168465,
                    "pendingAction": False,
                    "phase": "running",
                }),
            ),
            VEHICLE,
        )
        running = runner.extract_session_fingerprint(
            _status_with_passive(
                fingerprint=_session_fp(playback={
                    "frameIndex": 168775,
                    "pendingAction": False,
                    "phase": "running",
                }),
            ),
            VEHICLE,
        )
        stopped = runner.extract_session_fingerprint(
            _status_with_passive(
                worker_state="stopped",
                view_state="stale",
                fingerprint=_session_fp(playback={
                    "frameIndex": 169257,
                    "pendingAction": False,
                    "phase": "running",
                }),
            ),
            VEHICLE,
        )
        self.assertIsNotNone(initial)
        self.assertIsNotNone(running)
        self.assertIsNotNone(stopped)
        ok, msg = runner.validate_preservation(initial, running)
        self.assertTrue(ok, msg)
        ok, msg = runner.validate_preservation(initial, stopped)
        self.assertTrue(ok, msg)

        # Mode/authority change fails.
        mode_change = runner.extract_session_fingerprint(
            _status_with_passive(
                fingerprint=_session_fp(playback={
                    "frameIndex": 169257,
                    "pendingAction": True,
                    "phase": "running",
                }),
            ),
            VEHICLE,
        )
        ok, msg = runner.validate_preservation(initial, mode_change)
        self.assertFalse(ok)

        # Missing changed_fields/unknown_fields fail extraction (not normalized).
        missing_keys = _status_with_passive()
        del missing_keys["layers"]["passive_capture"]["session_preservation"][
            "changed_fields"
        ]
        self.assertIsNone(runner.extract_session_fingerprint(missing_keys, VEHICLE))

        # Invalid current must not pass when compared as None against baseline.
        ok, msg = runner.validate_preservation(initial, None)
        self.assertFalse(ok)
        self.assertIn("current fingerprint missing", msg)

        # Control-source drift within a receipt fails extraction.
        drifted = _status_with_passive()
        drifted["layers"]["passive_capture"]["session_preservation"]["after"] = (
            _session_fp(control_source="keyboard")
        )
        self.assertIsNone(runner.extract_session_fingerprint(drifted, VEHICLE))

    def test_view_and_authority_fail_closed(self) -> None:
        runner = _load_runner_module()
        ok, msg = runner.validate_view_latest(
            _current_view_payload(), vehicle_id=VEHICLE
        )
        self.assertTrue(ok, msg)
        ok, msg = runner.validate_view_latest(
            _current_view_payload(control=None), vehicle_id=VEHICLE
        )
        self.assertFalse(ok)
        self.assertIn("control object missing", msg)
        ok, msg = runner.validate_view_latest({"frame_id": "x"}, vehicle_id=VEHICLE)
        self.assertFalse(ok)
        # Wrong product schema / vehicle identity fail closed.
        ok, msg = runner.validate_view_latest(
            _current_view_payload(schema="automa_perception_view_publication_v1"),
            vehicle_id=VEHICLE,
        )
        self.assertFalse(ok)
        self.assertIn("schema", msg)
        ok, msg = runner.validate_view_latest(
            _current_view_payload(vehicle_id="other"), vehicle_id=VEHICLE
        )
        self.assertFalse(ok)
        ok, msg = runner.validate_view_latest(
            {
                "frame": {"frame_id": "x"},
                "overlay": {"status": "current", "source_frame_id": "x"},
                "perception": {"things": []},
                "cycle": {
                    "action_policy": "observe_only",
                    "control_application": "not_applied",
                },
                "control": {"applied": False},
            },
            vehicle_id=VEHICLE,
        )
        self.assertFalse(ok)

        status = _status_with_passive()
        ok, msg = runner.validate_authority(status, vehicle_id=VEHICLE)
        self.assertTrue(ok, msg)
        status["layers"]["automation_worker"]["details"]["authority"]["last_frame"] = {}
        ok, msg = runner.validate_authority(status, vehicle_id=VEHICLE)
        self.assertFalse(ok)

    def _fake_cleanup_run(
        self,
        runner,
        *,
        stop_exit: int = 0,
        status_exit: int = 0,
        status_payload: dict | None = None,
        status_has_pid: bool = True,
        pid: int | None = 4242,
    ):
        def fake_run(argv, **kwargs):
            step_dir = kwargs["step_dir"]
            step_dir.mkdir(parents=True, exist_ok=True)
            index = kwargs["index"]
            if index == 0:
                out = ""
                code = stop_exit
            else:
                payload = status_payload or _status_with_passive(
                    worker_state="stopped", view_state="stale", pid=pid
                )
                if not status_has_pid:
                    details = payload["layers"]["automation_worker"]["details"]
                    details.pop("pid", None)
                    payload["layers"]["automation_worker"]["details"] = {
                        "authority": details.get("authority", {})
                    }
                elif pid is not None:
                    payload["layers"]["automation_worker"]["details"]["pid"] = pid
                out = json.dumps(payload)
                code = status_exit
            (step_dir / f"cmd-{index:02d}.stdout.txt").write_text(
                out + "\n", encoding="utf-8"
            )
            (step_dir / f"cmd-{index:02d}.stderr.txt").write_text("", encoding="utf-8")
            return runner.CommandOutcome(
                argv=list(argv),
                command=" ".join(argv),
                exit_code=code,
                elapsed_ms=1,
                stdout_path=f"steps/_cleanup/cmd-{index:02d}.stdout.txt",
                stderr_path=f"steps/_cleanup/cmd-{index:02d}.stderr.txt",
                started_at_utc="t0",
                ended_at_utc="t1",
            )

        return fake_run

    def _cleanup_state(self, runner, session_dir: Path, **kwargs):
        last = kwargs.get("last_worker_pid")
        observed = set(kwargs.pop("observed_worker_pids", set()))
        if isinstance(last, int) and last > 0:
            observed.add(last)
        baseline = kwargs.pop("baseline_fingerprint", None)
        if baseline is None:
            baseline = runner.extract_session_fingerprint(
                _status_with_passive(worker_state="stopped", view_state="stale"),
                VEHICLE,
            )
        defaults = dict(
            catalog={
                "track": "acceptance",
                "gates": [{"id": "cleanup", "required": True}],
            },
            session_dir=session_dir,
            repo_root=ROOT,
            variables={"vehicle_id": VEHICLE},
            execution_mode="interactive_live",
            session_id="testsession",
            worker_may_exist=True,
            last_worker_pid=last,
            observed_worker_pids=observed,
            dry_run=False,
            non_interactive=False,
            baseline_fingerprint=baseline,
        )
        defaults.update(kwargs)
        (session_dir / "steps").mkdir(exist_ok=True)
        return runner.SessionState(**defaults)

    def test_cleanup_unknown_pid_fails(self) -> None:
        runner = _load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            state = self._cleanup_state(
                runner, session_dir, last_worker_pid=None, observed_worker_pids=set()
            )
            original = runner._run_command
            runner._run_command = self._fake_cleanup_run(
                runner, status_has_pid=False, pid=None
            )  # type: ignore[assignment]
            try:
                cleanup = runner._enforce_cleanup(
                    state,
                    command_timeout_s=5,
                    transcript_path=session_dir / "t.txt",
                )
            finally:
                runner._run_command = original  # type: ignore[assignment]
            self.assertIsNot(cleanup.get("worker_stopped"), True)
            self.assertTrue(state.findings)

    def test_cleanup_nonzero_exit_fails(self) -> None:
        runner = _load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            state = self._cleanup_state(runner, session_dir, last_worker_pid=4242)
            original = runner._run_command
            runner._run_command = self._fake_cleanup_run(
                runner, stop_exit=1, pid=4242
            )  # type: ignore[assignment]
            try:
                cleanup = runner._enforce_cleanup(
                    state,
                    command_timeout_s=5,
                    transcript_path=session_dir / "t.txt",
                )
            finally:
                runner._run_command = original  # type: ignore[assignment]
            self.assertIsNot(cleanup.get("worker_stopped"), True)
            self.assertEqual(cleanup.get("stop_exit_code"), 1)

    def test_cleanup_known_live_pid_fails(self) -> None:
        runner = _load_runner_module()
        live_pid = os.getpid()
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            state = self._cleanup_state(
                runner, session_dir, last_worker_pid=live_pid
            )
            original = runner._run_command
            runner._run_command = self._fake_cleanup_run(
                runner, pid=live_pid
            )  # type: ignore[assignment]
            try:
                cleanup = runner._enforce_cleanup(
                    state,
                    command_timeout_s=5,
                    transcript_path=session_dir / "t.txt",
                )
            finally:
                runner._run_command = original  # type: ignore[assignment]
            self.assertIs(cleanup.get("pid_alive"), True)
            self.assertIsNot(cleanup.get("worker_stopped"), True)

    def test_cleanup_known_dead_pid_passes(self) -> None:
        runner = _load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            dead_pid = 999_999_999
            state = self._cleanup_state(
                runner, session_dir, last_worker_pid=dead_pid
            )
            original_run = runner._run_command
            original_alive = runner._pid_alive
            runner._run_command = self._fake_cleanup_run(
                runner, pid=dead_pid
            )  # type: ignore[assignment]
            runner._pid_alive = (
                lambda pid: False if pid == dead_pid else original_alive(pid)
            )  # type: ignore[assignment]
            try:
                cleanup = runner._enforce_cleanup(
                    state,
                    command_timeout_s=5,
                    transcript_path=session_dir / "t.txt",
                )
            finally:
                runner._run_command = original_run  # type: ignore[assignment]
                runner._pid_alive = original_alive  # type: ignore[assignment]
            self.assertIs(cleanup.get("pid_alive"), False)
            self.assertIs(cleanup.get("worker_stopped"), True)
            self.assertFalse(state.findings)
            self.assertTrue(cleanup.get("preservation", {}).get("ok"))

    def test_cleanup_requires_all_observed_pids_dead(self) -> None:
        runner = _load_runner_module()
        live_pid = os.getpid()
        dead_status_pid = 999_999_998
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            state = self._cleanup_state(
                runner,
                session_dir,
                last_worker_pid=live_pid,
                observed_worker_pids={live_pid},
            )
            original_run = runner._run_command
            original_alive = runner._pid_alive

            def alive(pid):
                if pid == live_pid:
                    return True
                if pid == dead_status_pid:
                    return False
                return original_alive(pid)

            runner._run_command = self._fake_cleanup_run(
                runner, pid=dead_status_pid
            )  # type: ignore[assignment]
            runner._pid_alive = alive  # type: ignore[assignment]
            try:
                cleanup = runner._enforce_cleanup(
                    state,
                    command_timeout_s=5,
                    transcript_path=session_dir / "t.txt",
                )
            finally:
                runner._run_command = original_run  # type: ignore[assignment]
                runner._pid_alive = original_alive  # type: ignore[assignment]
            # Status PID dead is not enough when last-known startup PID is live.
            self.assertIn(live_pid, cleanup.get("pids") or [])
            self.assertIsNot(cleanup.get("worker_stopped"), True)

    def test_browser_view_rejects_stale_mtime(self) -> None:
        runner = _load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shot.png"
            Image.new("RGB", (8, 8), (1, 2, 3)).save(path)
            old = time.time() - 30 * 24 * 3600
            os.utime(path, (old, old))
            ok, msg, meta = runner.validate_browser_view_image(
                path, not_before_unix=time.time()
            )
            self.assertFalse(ok)
            self.assertIn("predate", msg)
            self.assertIn("source_sha256", meta)

    def test_browser_view_bind_preserves_mtime_and_requires_floor(self) -> None:
        runner = _load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "shot.png"
            dst = Path(tmp) / "browser-view.png"
            Image.new("RGB", (8, 8), (4, 5, 6)).save(src)
            ok, msg, _ = runner._bind_browser_view_image(
                src, dst, not_before_unix=None
            )
            self.assertFalse(ok)
            self.assertIn("floor", msg)
            self.assertFalse(dst.exists())

            floor = time.time() - 5
            ok, msg, meta = runner._bind_browser_view_image(
                src, dst, not_before_unix=floor
            )
            self.assertTrue(ok, msg)
            self.assertTrue(dst.is_file())
            self.assertAlmostEqual(
                dst.stat().st_mtime, meta["source_mtime_unix"], delta=1.0
            )

            old = time.time() - 30 * 24 * 3600
            os.utime(src, (old, old))
            ok, msg, _ = runner._bind_browser_view_image(
                src, dst, not_before_unix=time.time()
            )
            self.assertFalse(ok)
            self.assertFalse(dst.exists())

    def test_acceptance_catalog_uses_id_and_preservation(self) -> None:
        import yaml

        catalog = yaml.safe_load(
            (CATALOGS / "m007-acceptance.yaml").read_text(encoding="utf-8")
        )
        initial = next(s for s in catalog["steps"] if s["id"] == "status-initial")
        # Frozen primary command is aggregate; targeted JSON is supplemental.
        primary = " ".join(initial["commands"][0])
        self.assertNotIn("--id", primary)
        self.assertIn("--chase-url", primary)
        self.assertIn("--id", " ".join(initial["capture_json"]["command"]))
        update = next(s for s in catalog["steps"] if s["id"] == "update-perception")
        self.assertIn("staged_layers", update["machine_validators"])
        self.assertEqual(update["capture_json"]["path"], "staged-status.json")
        running = next(s for s in catalog["steps"] if s["id"] == "status-running")
        self.assertIn("preservation", running["machine_validators"])
        stopped = next(s for s in catalog["steps"] if s["id"] == "status-stopped")
        self.assertIn("preservation", stopped["machine_validators"])
        runner = _load_runner_module()
        ok, reason = runner._is_canonical_acceptance_catalog(
            CATALOGS / "m007-acceptance.yaml", catalog
        )
        self.assertTrue(ok, reason)

    def test_fake_acceptance_catalog_cannot_pass(self) -> None:
        runner = _load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            (session_dir / "steps").mkdir()
            Image.new("RGB", (8, 8), (1, 2, 3)).save(session_dir / "browser-view.png")
            floor = time.time() - 1
            os.utime(session_dir / "browser-view.png", (floor + 2, floor + 2))
            baseline = {
                "operator": "op",
                "browser": {"name": "Chrome", "version": "1"},
                "repositories": {
                    "auto_driving": {
                        "commit": "a",
                        "worktree_state": "clean",
                    },
                    "metrics_ui": {
                        "commit": "b",
                        "worktree_state": "clean",
                    },
                },
                "session_visible": {"game_id": "chase"},
                "precondition_cleanup": {"ok": True},
            }
            (session_dir / "baseline.json").write_text(
                json.dumps(baseline), encoding="utf-8"
            )
            (session_dir / "browser-view-meta.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "source_mtime_unix": floor + 2,
                    }
                ),
                encoding="utf-8",
            )
            state = runner.SessionState(
                catalog={
                    "schema": "live_cli_session_catalog_v0",
                    "id": "fake",
                    "track": "acceptance",
                    "gates": [],
                },
                session_dir=session_dir,
                repo_root=ROOT,
                variables={"vehicle_id": VEHICLE},
                execution_mode="interactive_live",
                session_id="x",
                interactive_human_confirmation=True,
                dry_run=False,
                non_interactive=False,
                canonical_acceptance=False,
                view_healthy_at_unix=floor,
            )
            result, reason = runner._derive_verdict(state)
            self.assertEqual(result, "incomplete")
            self.assertIn("bundled", reason.lower())

    def test_view_capture_passes_vehicle_id(self) -> None:
        runner = _load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            status = _status_with_passive()
            status_path = session_dir / "running-status.json"
            status_path.write_text(json.dumps(status), encoding="utf-8")

            class _Resp:
                def read(self):
                    return json.dumps(
                        _current_view_payload()
                    ).encode("utf-8")

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            import urllib.request

            original = urllib.request.urlopen
            urllib.request.urlopen = lambda *a, **k: _Resp()  # type: ignore[assignment]
            try:
                meta = runner._capture_view_latest(
                    session_dir, status_path, vehicle_id=VEHICLE
                )
            finally:
                urllib.request.urlopen = original  # type: ignore[assignment]
            self.assertNotIn("error", meta, meta)
            self.assertTrue((session_dir / "view-publication.json").is_file())

    def test_precondition_nonzero_and_missing_worker_fail(self) -> None:
        runner = _load_runner_module()
        # Missing automation_worker is not a healthy baseline.
        card = _status_with_passive(worker_state="stopped", view_state="stale")
        del card["layers"]["automation_worker"]
        ok, msg = runner.validate_initial_layers(card, vehicle_id=VEHICLE)
        self.assertFalse(ok)
        self.assertIn("stopped", msg)

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            state = runner.SessionState(
                catalog={"track": "acceptance"},
                session_dir=session_dir,
                repo_root=ROOT,
                variables={"vehicle_id": VEHICLE},
                execution_mode="interactive_live",
                session_id="pre",
                dry_run=False,
                non_interactive=False,
            )
            (session_dir / "steps").mkdir()

            def fake_run(argv, **kwargs):
                step_dir = kwargs["step_dir"]
                step_dir.mkdir(parents=True, exist_ok=True)
                index = kwargs["index"]
                # Nonzero status with stopped-shaped body must still fail.
                payload = _status_with_passive(
                    worker_state="stopped", view_state="stale", pid=111
                )
                (step_dir / f"cmd-{index:02d}.stdout.txt").write_text(
                    json.dumps(payload) + "\n", encoding="utf-8"
                )
                (step_dir / f"cmd-{index:02d}.stderr.txt").write_text("", encoding="utf-8")
                return runner.CommandOutcome(
                    argv=list(argv),
                    command=" ".join(argv),
                    exit_code=1,
                    elapsed_ms=1,
                    stdout_path=f"steps/_precondition_cleanup/cmd-{index:02d}.stdout.txt",
                    stderr_path=f"steps/_precondition_cleanup/cmd-{index:02d}.stderr.txt",
                    started_at_utc="t0",
                    ended_at_utc="t1",
                )

            original = runner._run_command
            runner._run_command = fake_run  # type: ignore[assignment]
            try:
                record = runner._run_precondition_cleanup(
                    state,
                    command_timeout_s=5,
                    transcript_path=session_dir / "t.txt",
                    metrics_ui_origin="http://localhost:5050",
                )
            finally:
                runner._run_command = original  # type: ignore[assignment]
            self.assertIsNot(record.get("ok"), True)
            self.assertIn("exit=1", str(record.get("error")))

    def test_dirty_metrics_ui_without_reviewable_diff_incomplete(self) -> None:
        runner = _load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            (session_dir / "steps").mkdir()
            Image.new("RGB", (8, 8), (9, 9, 9)).save(session_dir / "browser-view.png")
            floor = time.time() - 1
            os.utime(session_dir / "browser-view.png", (floor + 2, floor + 2))
            baseline = {
                "operator": "op",
                "browser": {"name": "Chrome", "version": "1"},
                "repositories": {
                    "auto_driving": {"commit": "a", "worktree_state": "clean"},
                    "metrics_ui": {
                        "commit": "b",
                        "worktree_state": "dirty",
                        "diff_identity": "unreviewable-hash",
                        "untracked_files": [],
                    },
                },
                "session_visible": {"game_id": "chase"},
                "precondition_cleanup": {"ok": True},
            }
            (session_dir / "baseline.json").write_text(
                json.dumps(baseline), encoding="utf-8"
            )
            (session_dir / "browser-view-meta.json").write_text(
                json.dumps({"ok": True, "source_mtime_unix": floor + 2}),
                encoding="utf-8",
            )
            state = runner.SessionState(
                catalog={
                    "id": "m007-acceptance",
                    "track": "acceptance",
                    "gates": [
                        {"id": g, "required": True}
                        for g in runner.CANONICAL_ACCEPTANCE_GATES
                    ],
                },
                session_dir=session_dir,
                repo_root=ROOT,
                variables={"vehicle_id": VEHICLE},
                execution_mode="interactive_live",
                session_id="dirty",
                interactive_human_confirmation=True,
                dry_run=False,
                non_interactive=False,
                canonical_acceptance=True,
                view_healthy_at_unix=floor,
            )
            for gate in runner.CANONICAL_ACCEPTANCE_GATES:
                state.gate_results[gate] = {
                    "id": gate,
                    "status": "pass",
                    "summary": "ok",
                    "evidence": [],
                }
            result, reason = runner._derive_verdict(state)
            self.assertEqual(result, "incomplete")
            self.assertIn("metrics-ui", reason.lower())

    def test_redact_path_collapses_foreign_absolute(self) -> None:
        runner = _load_runner_module()
        redacted = runner._redact_path("/var/tmp/secret/shot.png", ROOT)
        self.assertEqual(redacted, "<path>/shot.png")
        self.assertNotIn("/var/tmp", redacted)


if __name__ == "__main__":
    unittest.main(verbosity=2)
