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
MAX_FRAME_LAG = 24


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


def _stale_view_payload(
    lag: int,
    *,
    current_index: int = 100,
    claimed_lag=None,
) -> dict:
    if claimed_lag is None:
        claimed_lag = lag
    payload = _current_view_payload()
    payload["frame"] = {
        "frame_id": f"chase_frame_{current_index}",
        "frame_index": current_index,
    }
    payload["overlay"] = {
        "status": "stale",
        "source_frame_id": f"chase_frame_{current_index - lag}",
        "source_frame_index": current_index - lag,
        "frame_lag": claimed_lag,
        "frame_lag_ms": 8.5,
        "result_age_ms": 11.0,
    }
    return payload


class LiveCliSessionRunnerTests(unittest.TestCase):
    def test_runner_script_exists(self) -> None:
        self.assertTrue(RUNNER_PATH.is_file())

    def test_machine_only_mode_has_gateable_exit_contract(self) -> None:
        runner = _load_runner_module()
        args = runner.build_parser().parse_args(["--machine-only"])
        self.assertTrue(args.machine_only)
        for verdict, expected in (("pass", 0), ("fail", 1), ("not_run", 2)):
            with self.subTest(verdict=verdict):
                self.assertEqual(
                    runner._result_exit_code(
                        {"result": "incomplete", "machine_preflight": {"verdict": verdict}},
                        machine_only=True,
                    ),
                    expected,
                )

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
            _current_view_payload(),
            vehicle_id=VEHICLE,
            max_frame_lag=MAX_FRAME_LAG,
        )
        self.assertTrue(ok, msg)
        ok, msg = runner.validate_view_latest(
            _current_view_payload(control=None),
            vehicle_id=VEHICLE,
            max_frame_lag=MAX_FRAME_LAG,
        )
        self.assertFalse(ok)
        self.assertIn("control object missing", msg)
        ok, msg = runner.validate_view_latest(
            {"frame_id": "x"},
            vehicle_id=VEHICLE,
            max_frame_lag=MAX_FRAME_LAG,
        )
        self.assertFalse(ok)
        # Wrong product schema / vehicle identity fail closed.
        ok, msg = runner.validate_view_latest(
            _current_view_payload(schema="automa_perception_view_publication_v1"),
            vehicle_id=VEHICLE,
            max_frame_lag=MAX_FRAME_LAG,
        )
        self.assertFalse(ok)
        self.assertIn("schema", msg)
        ok, msg = runner.validate_view_latest(
            _current_view_payload(vehicle_id="other"),
            vehicle_id=VEHICLE,
            max_frame_lag=MAX_FRAME_LAG,
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
            max_frame_lag=MAX_FRAME_LAG,
        )
        self.assertFalse(ok)

        status = _status_with_passive()
        ok, msg = runner.validate_authority(status, vehicle_id=VEHICLE)
        self.assertTrue(ok, msg)
        status["layers"]["automation_worker"]["details"]["authority"]["last_frame"] = {}
        ok, msg = runner.validate_authority(status, vehicle_id=VEHICLE)
        self.assertFalse(ok)

    def test_view_correlation_accepts_current_and_bounded_stale(self) -> None:
        runner = _load_runner_module()

        current = _current_view_payload()
        del current["overlay"]["frame_lag"]
        evidence = runner._view_correlation_evidence(
            current,
            vehicle_id=VEHICLE,
            max_frame_lag=MAX_FRAME_LAG,
        )
        self.assertEqual(evidence["verdict"], "pass", evidence)
        self.assertEqual(evidence["mode"], "current")
        self.assertEqual(evidence["derived_frame_lag"], 0)
        self.assertIn("mode=current derived_lag=0 bound=24", evidence["summary"])

        for lag in (1, 12, 17, 24):
            with self.subTest(lag=lag):
                evidence = runner._view_correlation_evidence(
                    _stale_view_payload(lag),
                    vehicle_id=VEHICLE,
                    max_frame_lag=MAX_FRAME_LAG,
                )
                self.assertEqual(evidence["verdict"], "pass", evidence)
                self.assertEqual(evidence["mode"], "bounded_stale")
                self.assertEqual(evidence["claimed_frame_lag"], lag)
                self.assertEqual(evidence["derived_frame_lag"], lag)
                self.assertEqual(evidence["max_frame_lag"], MAX_FRAME_LAG)
                self.assertIn(
                    f"mode=bounded_stale derived_lag={lag} bound=24",
                    evidence["summary"],
                )

    def test_view_correlation_rejects_unproven_or_over_budget_lag(self) -> None:
        runner = _load_runner_module()

        cases = []
        over_budget = _stale_view_payload(25)
        cases.append(("over_budget", over_budget, "derived_lag=25 > max_frame_lag=24"))

        mismatch = _stale_view_payload(12, claimed_lag=11)
        cases.append(("claimed_mismatch", mismatch, "claimed_lag=11 != derived_lag=12"))

        reverse = _stale_view_payload(1)
        reverse["overlay"]["source_frame_index"] = 101
        reverse["overlay"]["frame_lag"] = -1
        cases.append(("reverse", reverse, "reverse or zero lineage"))

        pending = _stale_view_payload(1)
        pending["overlay"]["status"] = "pending"
        cases.append(("pending", pending, "overlay.status='pending'"))

        unknown = _stale_view_payload(1)
        unknown["overlay"]["status"] = "caught_up"
        cases.append(("unknown", unknown, "overlay.status='caught_up'"))

        current_mismatch = _current_view_payload(
            overlay={"source_frame_id": "different", "frame_lag": 0}
        )
        cases.append(("current_ids", current_mismatch, "current ids conflict"))

        current_nonzero = _current_view_payload(overlay={"frame_lag": 1})
        cases.append(("current_lag", current_nonzero, "must be integer 0"))

        missing_current_id = _stale_view_payload(1)
        missing_current_id["frame"]["frame_id"] = ""
        cases.append(("current_id_missing", missing_current_id, "frame.frame_id"))

        missing_source_id = _stale_view_payload(1)
        missing_source_id["overlay"]["source_frame_id"] = None
        cases.append(
            ("source_id_missing", missing_source_id, "overlay.source_frame_id")
        )

        for name, payload, reason in cases:
            with self.subTest(name=name):
                evidence = runner._view_correlation_evidence(
                    payload,
                    vehicle_id=VEHICLE,
                    max_frame_lag=MAX_FRAME_LAG,
                )
                self.assertEqual(evidence["verdict"], "fail", evidence)
                self.assertIn(reason, evidence["summary"])

    def test_view_correlation_requires_type_strict_indexes_and_lag(self) -> None:
        runner = _load_runner_module()
        fields = (
            ("frame", "frame_index", "frame.frame_index"),
            ("overlay", "source_frame_index", "overlay.source_frame_index"),
            ("overlay", "frame_lag", "overlay.frame_lag"),
        )
        for container, field, label in fields:
            for malformed in (True, 1.0, "1", None):
                with self.subTest(field=label, value=repr(malformed)):
                    payload = _stale_view_payload(1)
                    payload[container][field] = malformed
                    evidence = runner._view_correlation_evidence(
                        payload,
                        vehicle_id=VEHICLE,
                        max_frame_lag=MAX_FRAME_LAG,
                    )
                    self.assertEqual(evidence["verdict"], "fail", evidence)
                    self.assertIn(label, evidence["summary"])
                    self.assertIn("must be an integer", evidence["summary"])

    def test_view_correlation_preserves_timing_as_diagnostic_evidence(self) -> None:
        runner = _load_runner_module()
        payload = _stale_view_payload(17)
        payload["overlay"]["frame_lag_ms"] = "fast"
        payload["overlay"]["result_age_ms"] = -1
        evidence = runner._view_correlation_evidence(
            payload,
            vehicle_id=VEHICLE,
            max_frame_lag=MAX_FRAME_LAG,
        )
        self.assertEqual(evidence["verdict"], "pass", evidence)
        self.assertEqual(evidence["frame_lag_ms"], "fast")
        self.assertEqual(evidence["result_age_ms"], -1)
        self.assertEqual(len(evidence["diagnostic_findings"]), 2)

        huge_integer = 10**400
        payload = _stale_view_payload(17)
        payload["overlay"]["frame_lag_ms"] = huge_integer
        evidence = runner._view_correlation_evidence(
            payload,
            vehicle_id=VEHICLE,
            max_frame_lag=MAX_FRAME_LAG,
        )
        self.assertEqual(evidence["verdict"], "pass", evidence)
        self.assertEqual(evidence["frame_lag_ms"], huge_integer)
        self.assertEqual(evidence["diagnostic_findings"], [])

        for malformed in (-huge_integer, True, float("inf"), float("nan"), "1"):
            with self.subTest(malformed=repr(malformed)):
                payload = _stale_view_payload(17)
                payload["overlay"]["frame_lag_ms"] = malformed
                evidence = runner._view_correlation_evidence(
                    payload,
                    vehicle_id=VEHICLE,
                    max_frame_lag=MAX_FRAME_LAG,
                )
                self.assertEqual(evidence["verdict"], "pass", evidence)
                self.assertEqual(len(evidence["diagnostic_findings"]), 1)
                self.assertIn("overlay.frame_lag_ms", evidence["diagnostic_findings"][0])

    def test_view_correlation_preserves_independent_blockers(self) -> None:
        runner = _load_runner_module()
        cases = (
            ("perception", None, "perception result absent"),
            (
                "cycle",
                {"action_policy": "apply_controls"},
                "cycle.action_policy",
            ),
            ("control", {"applied": True}, "control.applied=True"),
        )
        for field, value, reason in cases:
            with self.subTest(field=field):
                payload = _stale_view_payload(12)
                payload[field] = value
                evidence = runner._view_correlation_evidence(
                    payload,
                    vehicle_id=VEHICLE,
                    max_frame_lag=MAX_FRAME_LAG,
                )
                self.assertEqual(evidence["verdict"], "fail", evidence)
                self.assertIn(reason, evidence["summary"])

    def test_machine_failure_wins_over_skipped_or_passing_visual_check(self) -> None:
        runner = _load_runner_module()
        for visual in ("skip", "pass"):
            with self.subTest(visual=visual):
                status = runner._finalize_step_status(
                    "ok",
                    machine_ok=False,
                    visual=visual,
                    required_for_verdict=True,
                )
                self.assertEqual(status, "fail")
        self.assertEqual(
            runner._finalize_step_status(
                "ok",
                machine_ok=True,
                visual="skip",
                required_for_verdict=True,
            ),
            "skip",
        )

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
        self.assertEqual(
            catalog["acceptance_contract"]["correlation"]["max_frame_lag"],
            MAX_FRAME_LAG,
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
                    session_dir,
                    status_path,
                    vehicle_id=VEHICLE,
                    max_frame_lag=MAX_FRAME_LAG,
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

    def test_linked_pr_must_be_real_reference(self) -> None:
        runner = _load_runner_module()
        self.assertFalse(runner._valid_linked_pr("x"))
        self.assertFalse(runner._valid_linked_pr("not-a-pr"))
        self.assertTrue(runner._valid_linked_pr("#92"))
        self.assertTrue(runner._valid_linked_pr("92"))
        self.assertTrue(
            runner._valid_linked_pr("https://github.com/GeorgeLuo/auto-driving/pull/92")
        )
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            (session_dir / "metrics-ui-worktree.diff").write_text(
                "diff --git a/x b/x\n", encoding="utf-8"
            )
            # Free-text PR still fails.
            ok, msg = runner._repo_reviewable(
                {
                    "worktree_state": "dirty",
                    "diff_identity": "abc",
                    "untracked_files": [],
                    "linked_pr": "x",
                    "commit": "deadbeef",
                },
                session_dir=session_dir,
                label="metrics-ui",
                linked_pr="x",
                repo=ROOT,
            )
            self.assertFalse(ok)
            # Untracked cannot be blessed by any PR.
            ok, msg = runner._repo_reviewable(
                {
                    "worktree_state": "dirty",
                    "diff_identity": "abc",
                    "untracked_files": ["runtime_override.py"],
                    "commit": "deadbeef",
                },
                session_dir=session_dir,
                label="metrics-ui",
                linked_pr="https://github.com/unrelated/example/pull/1",
                repo=ROOT,
            )
            self.assertFalse(ok)
            self.assertIn("untracked", msg.lower())
            # Unrelated repository PR fails even for tracked-only dirty.
            ok, msg = runner._repo_reviewable(
                {
                    "worktree_state": "dirty",
                    "diff_identity": "abc",
                    "untracked_files": [],
                    "commit": "deadbeef",
                    "github_owner": "GeorgeLuo",
                    "github_repo": "auto-driving",
                },
                session_dir=session_dir,
                label="metrics-ui",
                linked_pr="https://github.com/unrelated/example/pull/1",
                repo=ROOT,
            )
            self.assertFalse(ok)
            self.assertIn("does not match", msg)

    def test_in_memory_catalog_mutation_not_canonical(self) -> None:
        import copy
        import yaml

        runner = _load_runner_module()
        path = CATALOGS / "m007-acceptance.yaml"
        catalog = yaml.safe_load(path.read_text(encoding="utf-8"))
        ok, reason = runner._is_canonical_acceptance_catalog(path, catalog)
        self.assertTrue(ok, reason)
        mutated = copy.deepcopy(catalog)
        for step in mutated["steps"]:
            if step.get("id") == "automation-run":
                step["commands"][0] = [
                    p for p in step["commands"][0] if p != "--observe-only"
                ]
        ok, reason = runner._is_canonical_acceptance_catalog(path, mutated)
        self.assertFalse(ok)
        self.assertIn("does not match", reason)

        threshold_mutation = copy.deepcopy(catalog)
        threshold_mutation["acceptance_contract"]["correlation"][
            "max_frame_lag"
        ] = 25
        ok, reason = runner._is_canonical_acceptance_catalog(
            path, threshold_mutation
        )
        self.assertFalse(ok)
        self.assertIn("does not match", reason)

        invalid_threshold = copy.deepcopy(catalog)
        invalid_threshold["acceptance_contract"]["correlation"][
            "max_frame_lag"
        ] = True
        self.assertIsNone(runner._catalog_max_frame_lag(invalid_threshold))

    def test_pinned_digest_is_independent_constant(self) -> None:
        runner = _load_runner_module()
        # Constant must be a literal reviewed pin, not empty / import-only hash.
        self.assertEqual(len(runner.PINNED_ACCEPTANCE_CATALOG_DIGEST), 64)
        path = CATALOGS / "m007-acceptance.yaml"
        self.assertEqual(
            runner._catalog_bytes_digest(path),
            runner.PINNED_ACCEPTANCE_CATALOG_DIGEST,
        )
        # On-disk content that does not match the constant fails closed.
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "m007-acceptance.yaml"
            original = path.read_bytes()
            # Guaranteed content change before process-style digest check.
            fake.write_bytes(original + b"\n# local-edit-before-import\n")
            edited_digest = runner._catalog_bytes_digest(fake)
            self.assertNotEqual(edited_digest, runner.PINNED_ACCEPTANCE_CATALOG_DIGEST)
            import yaml

            catalog = yaml.safe_load(path.read_text(encoding="utf-8"))
            ok, reason = runner._is_canonical_acceptance_catalog(fake, catalog)
            self.assertFalse(ok)
            self.assertIn("digest", reason.lower())

    def test_failed_precondition_blocks_automation_run(self) -> None:
        """Orchestration: live_mutation must not run after failed safety prereqs."""
        runner = _load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "session"
            catalog = {
                "schema": "live_cli_session_catalog_v0",
                "id": "orch-test",
                "track": "acceptance",
                "vehicle_id": VEHICLE,
                "gates": [
                    {"id": "initial_layers", "required": True},
                    {"id": "staging", "required": True},
                    {"id": "startup", "required": True},
                    {"id": "cleanup", "required": True},
                ],
                "steps": [
                    {
                        "id": "status-initial",
                        "kind": "command",
                        "safety": "read",
                        "commands": [
                            ["./cli/automa", "vehicles", "status", "--chase-url", "http://localhost:5050"]
                        ],
                        "gate_ids": ["initial_layers"],
                        "required_for_verdict": True,
                        "visual_required": False,
                        "expect_exit": 0,
                    },
                    {
                        "id": "update-perception",
                        "kind": "command",
                        "safety": "local_write",
                        "commands": [
                            [
                                "./cli/automa",
                                "vehicles",
                                "update",
                                "perception",
                                "--id",
                                VEHICLE,
                                "--algorithm",
                                "lightweight_observer",
                            ]
                        ],
                        "gate_ids": ["staging"],
                        "required_for_verdict": True,
                        "visual_required": False,
                        "expect_exit": 0,
                    },
                    {
                        "id": "automation-run",
                        "kind": "command",
                        "safety": "live_mutation",
                        "commands": [
                            [
                                "./cli/automa",
                                "vehicles",
                                "automation",
                                "run",
                                "--id",
                                VEHICLE,
                                "--observe-only",
                                "--frames",
                                "0",
                                "--open-view",
                            ]
                        ],
                        "gate_ids": ["startup"],
                        "required_for_verdict": True,
                        "visual_required": False,
                        "expect_exit": 0,
                    },
                ],
            }

            def fake_run(argv, **kwargs):
                step_dir = kwargs["step_dir"]
                step_dir.mkdir(parents=True, exist_ok=True)
                index = kwargs["index"]
                (step_dir / f"cmd-{index:02d}.stdout.txt").write_text(
                    "ok\n", encoding="utf-8"
                )
                (step_dir / f"cmd-{index:02d}.stderr.txt").write_text("", encoding="utf-8")
                return runner.CommandOutcome(
                    argv=list(argv),
                    command=" ".join(argv),
                    exit_code=1,
                    elapsed_ms=1,
                    stdout_path=f"steps/x/cmd-{index:02d}.stdout.txt",
                    stderr_path=f"steps/x/cmd-{index:02d}.stderr.txt",
                    started_at_utc="t0",
                    ended_at_utc="t1",
                )

            def fake_precondition(state, **kwargs):
                return {"ok": False, "error": "unproven baseline", "attempted": False}

            original_run = runner._run_command
            original_pre = runner._run_precondition_cleanup
            original_load = runner._load_pinned_acceptance_catalog
            original_canon = runner._is_canonical_acceptance_catalog
            # Treat fixture as canonical so refusal-for-noncanonical does not mask the
            # precondition short-circuit under test.
            runner._run_command = fake_run  # type: ignore[assignment]
            runner._run_precondition_cleanup = fake_precondition  # type: ignore[assignment]
            runner._load_pinned_acceptance_catalog = (  # type: ignore[assignment]
                lambda path=None: catalog
            )
            runner._is_canonical_acceptance_catalog = (  # type: ignore[assignment]
                lambda path, cat: (True, "test fixture")
            )
            try:
                result = runner.run_session(
                    catalog=catalog,
                    session_dir=session_dir,
                    repo_root=ROOT,
                    metrics_ui_origin="http://localhost:5050",
                    metrics_ui_repo=None,
                    browser_name="Chrome",
                    browser_version="1",
                    prompt=lambda _m: "skip",
                    non_interactive=True,
                    auto_visual="skip",
                    command_timeout_s=5,
                    dry_run=False,
                    browser_view_path=None,
                    operator="test",
                    catalog_path=CATALOGS / "m007-acceptance.yaml",
                )
            finally:
                runner._run_command = original_run  # type: ignore[assignment]
                runner._run_precondition_cleanup = original_pre  # type: ignore[assignment]
                runner._load_pinned_acceptance_catalog = original_load  # type: ignore[assignment]
                runner._is_canonical_acceptance_catalog = original_canon  # type: ignore[assignment]

            executed = result.get("ordered_step_outcomes") or []
            statuses = {s["id"]: s["status"] for s in executed}
            self.assertEqual(statuses.get("automation-run"), "blocked")
            # Ensure no automation run argv was executed.
            all_cmds = []
            for step in executed:
                for cmd in step.get("commands") or []:
                    all_cmds.append(cmd.get("argv") or [])
            for argv in all_cmds:
                self.assertFalse(
                    "automation" in argv and "run" in argv,
                    f"automation run should not execute: {argv}",
                )
            self.assertNotEqual(result.get("result"), "pass")

    def test_pre_session_identity_reports_sibling_dirt(self) -> None:
        """Pre-session identity must not hide unrelated dirt under evidence/."""
        runner = _load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "t@example.com"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "t"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            (repo / "cli").mkdir()
            (repo / "cli" / "automa").write_text("#!/bin/sh\n", encoding="utf-8")
            (repo / "README").write_text("x\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            # Sibling untracked file under evidence/ — not session artifacts.
            (repo / "evidence").mkdir()
            (repo / "evidence" / "unrelated.txt").write_text("dirty\n", encoding="utf-8")
            identity = runner._git_identity(repo)
            self.assertEqual(identity.get("worktree_state"), "dirty")
            # Collapsed or expanded path must still mark dirty (not false clean).
            self.assertTrue(
                identity.get("untracked_files")
                or identity.get("status_porcelain")
            )

    def test_failed_initial_blocks_automation_run(self) -> None:
        """Orchestration: failed initial_layers blocks live_mutation."""
        runner = _load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "session"
            catalog = {
                "schema": "live_cli_session_catalog_v0",
                "id": "orch-initial",
                "track": "acceptance",
                "vehicle_id": VEHICLE,
                "gates": [
                    {"id": "initial_layers", "required": True},
                    {"id": "staging", "required": True},
                    {"id": "startup", "required": True},
                ],
                "steps": [
                    {
                        "id": "status-initial",
                        "kind": "command",
                        "safety": "read",
                        "commands": [["./cli/automa", "vehicles", "status"]],
                        "gate_ids": ["initial_layers"],
                        "required_for_verdict": True,
                        "visual_required": False,
                        "expect_exit": 0,
                    },
                    {
                        "id": "update-perception",
                        "kind": "command",
                        "safety": "local_write",
                        "commands": [["./cli/automa", "vehicles", "update", "perception"]],
                        "gate_ids": ["staging"],
                        "required_for_verdict": True,
                        "visual_required": False,
                        "expect_exit": 0,
                    },
                    {
                        "id": "automation-run",
                        "kind": "command",
                        "safety": "live_mutation",
                        "commands": [
                            [
                                "./cli/automa",
                                "vehicles",
                                "automation",
                                "run",
                                "--observe-only",
                            ]
                        ],
                        "gate_ids": ["startup"],
                        "required_for_verdict": True,
                        "visual_required": False,
                        "expect_exit": 0,
                    },
                ],
            }

            def fake_run(argv, **kwargs):
                step_dir = kwargs["step_dir"]
                step_dir.mkdir(parents=True, exist_ok=True)
                index = kwargs["index"]
                (step_dir / f"cmd-{index:02d}.stdout.txt").write_text(
                    "ok\n", encoding="utf-8"
                )
                (step_dir / f"cmd-{index:02d}.stderr.txt").write_text("", encoding="utf-8")
                # Fail only the initial status human command; allow others if reached.
                code = 1 if "status" in argv and "automation" not in argv else 0
                if "update" in argv:
                    code = 0
                return runner.CommandOutcome(
                    argv=list(argv),
                    command=" ".join(argv),
                    exit_code=code,
                    elapsed_ms=1,
                    stdout_path=f"steps/x/cmd-{index:02d}.stdout.txt",
                    stderr_path=f"steps/x/cmd-{index:02d}.stderr.txt",
                    started_at_utc="t0",
                    ended_at_utc="t1",
                )

            def fake_precondition(state, **kwargs):
                return {"ok": True, "error": None, "attempted": False}

            original_run = runner._run_command
            original_pre = runner._run_precondition_cleanup
            original_load = runner._load_pinned_acceptance_catalog
            original_canon = runner._is_canonical_acceptance_catalog
            runner._run_command = fake_run  # type: ignore[assignment]
            runner._run_precondition_cleanup = fake_precondition  # type: ignore[assignment]
            runner._load_pinned_acceptance_catalog = (  # type: ignore[assignment]
                lambda path=None: catalog
            )
            runner._is_canonical_acceptance_catalog = (  # type: ignore[assignment]
                lambda path, cat: (True, "test fixture")
            )
            try:
                result = runner.run_session(
                    catalog=catalog,
                    session_dir=session_dir,
                    repo_root=ROOT,
                    metrics_ui_origin="http://localhost:5050",
                    metrics_ui_repo=None,
                    browser_name="Chrome",
                    browser_version="1",
                    prompt=lambda _m: "skip",
                    non_interactive=True,
                    auto_visual="skip",
                    command_timeout_s=5,
                    dry_run=False,
                    browser_view_path=None,
                    operator="test",
                    catalog_path=CATALOGS / "m007-acceptance.yaml",
                )
            finally:
                runner._run_command = original_run  # type: ignore[assignment]
                runner._run_precondition_cleanup = original_pre  # type: ignore[assignment]
                runner._load_pinned_acceptance_catalog = original_load  # type: ignore[assignment]
                runner._is_canonical_acceptance_catalog = original_canon  # type: ignore[assignment]

            statuses = {
                s["id"]: s["status"] for s in (result.get("ordered_step_outcomes") or [])
            }
            self.assertEqual(statuses.get("status-initial"), "fail")
            self.assertEqual(statuses.get("automation-run"), "blocked")
            for step in result.get("ordered_step_outcomes") or []:
                for cmd in step.get("commands") or []:
                    argv = cmd.get("argv") or []
                    self.assertFalse(
                        "automation" in argv and "run" in argv,
                        f"automation run must not execute: {argv}",
                    )

    def test_noncanonical_acceptance_executes_no_commands(self) -> None:
        """Altered acceptance catalog must not run any CLI — including non-observe-only run."""
        runner = _load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "session"
            catalog = {
                "schema": "live_cli_session_catalog_v0",
                "id": "fake-acceptance",
                "track": "acceptance",
                "vehicle_id": VEHICLE,
                "gates": [
                    {"id": "initial_layers", "required": True},
                    {"id": "staging", "required": True},
                    {"id": "startup", "required": True},
                ],
                "steps": [
                    {
                        "id": "prereq-a",
                        "kind": "command",
                        "safety": "read",
                        "commands": [["true"]],
                        "gate_ids": ["initial_layers"],
                        "required_for_verdict": True,
                        "visual_required": False,
                        "expect_exit": 0,
                    },
                    {
                        "id": "prereq-b",
                        "kind": "command",
                        "safety": "local_write",
                        "commands": [["true"]],
                        "gate_ids": ["staging"],
                        "required_for_verdict": True,
                        "visual_required": False,
                        "expect_exit": 0,
                    },
                    {
                        "id": "automation-run",
                        "kind": "command",
                        "safety": "live_mutation",
                        "commands": [
                            [
                                "./cli/automa",
                                "vehicles",
                                "automation",
                                "run",
                                "--id",
                                VEHICLE,
                                "--frames",
                                "0",
                            ]
                        ],
                        "gate_ids": ["startup"],
                        "required_for_verdict": True,
                        "visual_required": False,
                        "expect_exit": 0,
                    },
                ],
            }
            calls: list[list[str]] = []

            def fake_run(argv, **kwargs):
                calls.append(list(argv))
                step_dir = kwargs["step_dir"]
                step_dir.mkdir(parents=True, exist_ok=True)
                index = kwargs["index"]
                (step_dir / f"cmd-{index:02d}.stdout.txt").write_text("", encoding="utf-8")
                (step_dir / f"cmd-{index:02d}.stderr.txt").write_text("", encoding="utf-8")
                return runner.CommandOutcome(
                    argv=list(argv),
                    command=" ".join(argv),
                    exit_code=0,
                    elapsed_ms=1,
                    stdout_path=f"steps/x/cmd-{index:02d}.stdout.txt",
                    stderr_path=f"steps/x/cmd-{index:02d}.stderr.txt",
                    started_at_utc="t0",
                    ended_at_utc="t1",
                )

            original = runner._run_command
            runner._run_command = fake_run  # type: ignore[assignment]
            try:
                result = runner.run_session(
                    catalog=catalog,
                    session_dir=session_dir,
                    repo_root=ROOT,
                    metrics_ui_origin="http://localhost:5050",
                    metrics_ui_repo=None,
                    browser_name="Chrome",
                    browser_version="1",
                    prompt=lambda _m: "pass",
                    non_interactive=True,
                    auto_visual="pass",
                    command_timeout_s=5,
                    dry_run=False,
                    browser_view_path=None,
                    operator="test",
                    catalog_path=None,
                )
            finally:
                runner._run_command = original  # type: ignore[assignment]

            self.assertEqual(calls, [], f"expected no CLI; got {calls}")
            self.assertEqual(result.get("result"), "incomplete")
            self.assertFalse(result.get("ordered_step_outcomes"))

    def test_failed_staging_blocks_automation_run(self) -> None:
        runner = _load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "session"
            catalog = {
                "schema": "live_cli_session_catalog_v0",
                "id": "orch-staging",
                "track": "acceptance",
                "vehicle_id": VEHICLE,
                "gates": [
                    {"id": "initial_layers", "required": True},
                    {"id": "staging", "required": True},
                    {"id": "startup", "required": True},
                ],
                "steps": [
                    {
                        "id": "status-initial",
                        "kind": "command",
                        "safety": "read",
                        "commands": [["./cli/automa", "vehicles", "status"]],
                        "gate_ids": ["initial_layers"],
                        "required_for_verdict": True,
                        "visual_required": False,
                        "expect_exit": 0,
                    },
                    {
                        "id": "update-perception",
                        "kind": "command",
                        "safety": "local_write",
                        "commands": [["./cli/automa", "vehicles", "update", "perception"]],
                        "gate_ids": ["staging"],
                        "required_for_verdict": True,
                        "visual_required": False,
                        "expect_exit": 0,
                    },
                    {
                        "id": "automation-run",
                        "kind": "command",
                        "safety": "live_mutation",
                        "commands": [
                            [
                                "./cli/automa",
                                "vehicles",
                                "automation",
                                "run",
                                "--observe-only",
                            ]
                        ],
                        "gate_ids": ["startup"],
                        "required_for_verdict": True,
                        "visual_required": False,
                        "expect_exit": 0,
                    },
                ],
            }

            def fake_run(argv, **kwargs):
                step_dir = kwargs["step_dir"]
                step_dir.mkdir(parents=True, exist_ok=True)
                index = kwargs["index"]
                (step_dir / f"cmd-{index:02d}.stdout.txt").write_text(
                    "ok\n", encoding="utf-8"
                )
                (step_dir / f"cmd-{index:02d}.stderr.txt").write_text("", encoding="utf-8")
                code = 1 if "update" in argv else 0
                return runner.CommandOutcome(
                    argv=list(argv),
                    command=" ".join(argv),
                    exit_code=code,
                    elapsed_ms=1,
                    stdout_path=f"steps/x/cmd-{index:02d}.stdout.txt",
                    stderr_path=f"steps/x/cmd-{index:02d}.stderr.txt",
                    started_at_utc="t0",
                    ended_at_utc="t1",
                )

            original_run = runner._run_command
            original_pre = runner._run_precondition_cleanup
            original_load = runner._load_pinned_acceptance_catalog
            original_canon = runner._is_canonical_acceptance_catalog
            runner._run_command = fake_run  # type: ignore[assignment]
            runner._run_precondition_cleanup = (  # type: ignore[assignment]
                lambda state, **kwargs: {"ok": True, "error": None, "attempted": False}
            )
            runner._load_pinned_acceptance_catalog = (  # type: ignore[assignment]
                lambda path=None: catalog
            )
            runner._is_canonical_acceptance_catalog = (  # type: ignore[assignment]
                lambda path, cat: (True, "test fixture")
            )
            try:
                result = runner.run_session(
                    catalog=catalog,
                    session_dir=session_dir,
                    repo_root=ROOT,
                    metrics_ui_origin="http://localhost:5050",
                    metrics_ui_repo=None,
                    browser_name="Chrome",
                    browser_version="1",
                    prompt=lambda _m: "skip",
                    non_interactive=True,
                    auto_visual="skip",
                    command_timeout_s=5,
                    dry_run=False,
                    browser_view_path=None,
                    operator="test",
                    catalog_path=CATALOGS / "m007-acceptance.yaml",
                )
            finally:
                runner._run_command = original_run  # type: ignore[assignment]
                runner._run_precondition_cleanup = original_pre  # type: ignore[assignment]
                runner._load_pinned_acceptance_catalog = original_load  # type: ignore[assignment]
                runner._is_canonical_acceptance_catalog = original_canon  # type: ignore[assignment]

            statuses = {
                s["id"]: s["status"] for s in (result.get("ordered_step_outcomes") or [])
            }
            self.assertEqual(statuses.get("update-perception"), "fail")
            self.assertEqual(statuses.get("automation-run"), "blocked")
            for step in result.get("ordered_step_outcomes") or []:
                for cmd in step.get("commands") or []:
                    argv = cmd.get("argv") or []
                    self.assertFalse("automation" in argv and "run" in argv)

    def test_canonical_catalog_command_order_smoke(self) -> None:
        """Pinned catalog executes primary sequence argv order under command doubles."""
        import yaml

        runner = _load_runner_module()
        catalog = yaml.safe_load(
            (CATALOGS / "m007-acceptance.yaml").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "session"
            seen: list[list[str]] = []

            def fake_run(argv, **kwargs):
                seen.append(list(argv))
                step_dir = kwargs["step_dir"]
                sess = kwargs["session_dir"]
                step_dir.mkdir(parents=True, exist_ok=True)
                index = kwargs["index"]
                out = ""
                # Provide minimal JSON for status --json captures.
                if "--json" in argv:
                    worker = "stopped"
                    view = "stale"
                    if step_dir.name == "status-running":
                        worker, view = "running", "available"
                    payload = _status_with_passive(
                        worker_state=worker,
                        view_state=view,
                        pid=4242,
                        recording=False,
                        applied=False,
                    )
                    out = json.dumps(payload)
                stdout = step_dir / f"cmd-{index:02d}.stdout.txt"
                stderr = step_dir / f"cmd-{index:02d}.stderr.txt"
                stdout.write_text(out + "\n", encoding="utf-8")
                stderr.write_text("", encoding="utf-8")
                return runner.CommandOutcome(
                    argv=list(argv),
                    command=" ".join(argv),
                    exit_code=0,
                    elapsed_ms=1,
                    stdout_path=str(stdout.relative_to(sess)),
                    stderr_path=str(stderr.relative_to(sess)),
                    started_at_utc="t0",
                    ended_at_utc="t1",
                )

            original_run = runner._run_command
            original_pre = runner._run_precondition_cleanup
            original_view = runner._capture_view_latest
            runner._run_command = fake_run  # type: ignore[assignment]
            runner._run_precondition_cleanup = (  # type: ignore[assignment]
                lambda state, **kwargs: {
                    "ok": True,
                    "error": None,
                    "attempted": False,
                    "needed": False,
                }
            )
            def fake_view(
                session_dir,
                running_status,
                *,
                vehicle_id,
                max_frame_lag,
            ):
                payload = _stale_view_payload(17)
                (session_dir / "view-publication.json").write_text(
                    json.dumps(payload), encoding="utf-8"
                )
                evidence = runner._view_correlation_evidence(
                    payload,
                    vehicle_id=vehicle_id,
                    max_frame_lag=max_frame_lag,
                )
                return {
                    "url": "http://127.0.0.1:1/api/latest",
                    "path": "view-publication.json",
                    "http_status": 200,
                    "summary": evidence["summary"],
                    "correlation": evidence,
                    "vehicle_id": vehicle_id,
                }

            runner._capture_view_latest = fake_view  # type: ignore[assignment]
            try:
                result = runner.run_session(
                    catalog=catalog,
                    session_dir=session_dir,
                    repo_root=ROOT,
                    metrics_ui_origin="http://localhost:5050",
                    metrics_ui_repo=ROOT,
                    browser_name="Chrome",
                    browser_version="1",
                    prompt=lambda _m: "skip",
                    non_interactive=True,
                    auto_visual="skip",
                    command_timeout_s=5,
                    dry_run=False,
                    browser_view_path=None,
                    operator="test",
                    catalog_path=CATALOGS / "m007-acceptance.yaml",
                    machine_only=True,
                )
            finally:
                runner._run_command = original_run  # type: ignore[assignment]
                runner._run_precondition_cleanup = original_pre  # type: ignore[assignment]
                runner._capture_view_latest = original_view  # type: ignore[assignment]

            # Primary human surfaces appear in order.
            joined = [" ".join(a) for a in seen]
            self.assertTrue(
                any("vehicles status" in j and "--chase-url" in j for j in joined),
                joined,
            )
            self.assertTrue(any("update perception" in j for j in joined), joined)
            run_lines = [
                j
                for j in joined
                if "automation run" in j and "--help" not in j
            ]
            self.assertTrue(run_lines, joined)
            self.assertTrue(all("--observe-only" in j for j in run_lines), run_lines)
            self.assertTrue(any("automation stop" in j for j in joined), joined)
            self.assertNotEqual(result.get("result"), "pass")  # non-interactive
            self.assertEqual(result.get("execution_mode"), "machine_only_live")
            self.assertEqual(
                result["machine_preflight"]["verdict"],
                "pass",
                result["machine_preflight"],
            )
            correlation = result.get("view_correlation") or {}
            self.assertEqual(correlation.get("verdict"), "pass", correlation)
            self.assertEqual(correlation.get("mode"), "bounded_stale")
            self.assertEqual(correlation.get("derived_frame_lag"), 17)
            self.assertEqual(correlation.get("max_frame_lag"), MAX_FRAME_LAG)
            correlation_gate = next(
                gate for gate in result["gates"] if gate["id"] == "correlation"
            )
            self.assertEqual(
                correlation_gate["details"]["derived_frame_lag"], 17
            )
            self.assertTrue((session_dir / "result.json").is_file())
            self.assertTrue((session_dir / "digests.json").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
