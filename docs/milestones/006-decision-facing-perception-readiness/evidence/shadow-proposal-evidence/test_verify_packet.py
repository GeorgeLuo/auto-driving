"""Regressions for incomplete and synthetic post-capture M006 packets."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


HERE = Path(__file__).resolve().parent
RECORD = HERE / "result.json"
VERIFY = HERE / "verify_packet.py"
RENDER = HERE / "render_result.py"
REPORT = HERE / "report_packet.py"


def _verify_mutation(mutate) -> subprocess.CompletedProcess[str]:
    payload = json.loads(RECORD.read_text(encoding="utf-8"))
    mutate(payload)
    with TemporaryDirectory() as temporary:
        record = Path(temporary) / "mutated-result.json"
        record.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-B", str(VERIFY), "--record", str(record)],
            cwd=HERE,
            capture_output=True,
            text=True,
            check=False,
        )


def _mutate_package_host_observation(payload: dict) -> None:
    payload["environment_packages"]["piracer"]["authoritative_host_observation"] = True


def _mutate_accepted_intervals(payload: dict) -> None:
    payload["interval_coverage"]["piracer"].update(
        status="complete", accepted_intervals=12, uncovered_intervals=0
    )


def _mutate_package_interval_coverage(payload: dict) -> None:
    payload["environment_packages"]["piracer"]["interval_coverage"] = "complete"


def _mutate_positive_pilot_observation(payload: dict) -> None:
    payload["cleanup"]["pilot_output_observed"] = True


def _drop_interval_coverage(payload: dict) -> None:
    del payload["interval_coverage"]


def _drop_cleanup_field(payload: dict) -> None:
    del payload["cleanup"]["pilot_output_observed"]


def _drop_authority_field(payload: dict) -> None:
    del payload["preparatory_checks"]["offline_public_door"]["proposed_applied"]


def _mutate_proposed_applied(payload: dict) -> None:
    payload["preparatory_checks"]["offline_public_door"]["proposed_applied"] = True


def _mutate_authorized_output(payload: dict) -> None:
    payload["preparatory_checks"]["offline_public_door"]["authorized_throttle"] = 0.8


@pytest.mark.parametrize(
    "mutate",
    [
        _mutate_package_host_observation,
        _mutate_accepted_intervals,
        _mutate_package_interval_coverage,
        _mutate_positive_pilot_observation,
        _drop_interval_coverage,
        _drop_cleanup_field,
        _drop_authority_field,
        _mutate_proposed_applied,
        _mutate_authorized_output,
    ],
    ids=[
        "fabricated-package-host-observation",
        "fabricated-accepted-intervals",
        "fabricated-package-interval-coverage",
        "positive-pilot-observation",
        "missing-interval-coverage",
        "missing-cleanup-field",
        "missing-authority-field",
        "proposed-applied",
        "nonzero-authorized-output",
    ],
)
def test_public_door_rejects_rr1_mutations(mutate) -> None:
    result = _verify_mutation(mutate)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "ERROR:" in result.stderr


def _synthetic_success(root: Path) -> dict:
    for environment in ("chase", "piracer"):
        package = root / environment
        package.mkdir()
        (package / "capture.json").write_text(
            json.dumps({"synthetic": True, "environment": environment}),
            encoding="utf-8",
        )
    return {
        "schema": "m006_shadow_proposal_evidence_v1",
        "status": "captured",
        "criteria": {
            "M006-06": {"status": "Review", "evidence": "synthetic test only"},
            "M006-07": {"status": "Review", "evidence": "synthetic test only"},
        },
        "case_outcomes": {
            f"C{index}": {"status": "passed", "evidence": "synthetic test only"}
            for index in range(1, 8)
        },
        "environment_packages": {
            environment: {
                "status": "passed",
                "path": f"{environment}/",
                "artifacts": [{"path": "capture.json", "role": "synthetic test artifact"}],
            }
            for environment in ("chase", "piracer")
        },
        "interval_coverage": {
            environment: {
                "status": "passed",
                "accepted_intervals": 1,
                "uncovered_intervals": 0,
                "evidence": "synthetic test only",
            }
            for environment in ("chase", "piracer")
        },
        "reviews": {
            "visual": {"status": "review", "evidence": "manual review pending"},
            "operator": {"status": "review", "evidence": "manual review pending"},
        },
    }


def _write_success(root: Path, payload: dict) -> tuple[Path, Path]:
    record = root / "result.json"
    page = root / "result.html"
    record.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    rendered = subprocess.run(
        [sys.executable, "-B", str(RENDER), "--record", str(record), "--output", str(page)],
        cwd=HERE,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rendered.returncode == 0, rendered.stdout + rendered.stderr
    return record, page


def test_synthetic_success_packet_and_report() -> None:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        record, page = _write_success(root, _synthetic_success(root))

        verified = subprocess.run(
            [sys.executable, "-B", str(VERIFY), "--record", str(record), "--html", str(page), "--check-html"],
            cwd=HERE,
            capture_output=True,
            text=True,
            check=False,
        )
        assert verified.returncode == 0, verified.stdout + verified.stderr

        reported = subprocess.run(
            [sys.executable, "-B", str(REPORT), "--record", str(record), "--html", str(page)],
            cwd=HERE,
            capture_output=True,
            text=True,
            check=False,
        )
        assert reported.returncode == 0, reported.stdout + reported.stderr
        assert "PASS    packet" in reported.stdout
        assert "REVIEW  reviews.visual" in reported.stdout
        assert "SUMMARY" in reported.stdout


def test_synthetic_success_rejects_missing_artifact() -> None:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        payload = _synthetic_success(root)
        payload["environment_packages"]["piracer"]["artifacts"][0]["path"] = "missing.json"
        record = root / "result.json"
        record.write_text(json.dumps(payload), encoding="utf-8")

        verified = subprocess.run(
            [sys.executable, "-B", str(VERIFY), "--record", str(record)],
            cwd=HERE,
            capture_output=True,
            text=True,
            check=False,
        )
        assert verified.returncode != 0
        assert "missing.json" in verified.stderr

        reported = subprocess.run(
            [sys.executable, "-B", str(REPORT), "--record", str(record)],
            cwd=HERE,
            capture_output=True,
            text=True,
            check=False,
        )
        assert reported.returncode != 0
        assert "FAIL    packet" in reported.stdout
        assert "missing.json" in reported.stdout


def test_synthetic_success_rejects_artifact_path_traversal() -> None:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        payload = _synthetic_success(root)
        payload["environment_packages"]["chase"]["artifacts"][0]["path"] = "../result.json"
        record = root / "result.json"
        record.write_text(json.dumps(payload), encoding="utf-8")

        verified = subprocess.run(
            [sys.executable, "-B", str(VERIFY), "--record", str(record)],
            cwd=HERE,
            capture_output=True,
            text=True,
            check=False,
        )
        assert verified.returncode != 0
        assert "stay within the evidence root" in verified.stderr
