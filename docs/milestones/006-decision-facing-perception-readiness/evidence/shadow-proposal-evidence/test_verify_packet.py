"""Public-door regressions for the incomplete M006 preparation packet."""

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
