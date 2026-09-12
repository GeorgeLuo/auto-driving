#!/usr/bin/env python3
"""Verify the M006 evidence result and its fail-closed preparation boundary."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from render_result import render


HERE = Path(__file__).resolve().parent
RECORD = HERE / "result.json"
PAGE = HERE / "result.html"


class PacketError(ValueError):
    pass


def _required_fields(value: object, fields: tuple[str, ...], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PacketError(f"{context} must be an object")
    missing = [field for field in fields if field not in value]
    if missing:
        raise PacketError(f"{context} missing required fields: {', '.join(missing)}")
    return value


def _required_false(value: object, field: str) -> None:
    if type(value) is not bool or value is not False:
        raise PacketError(f"{field} must remain false in the incomplete packet")


def _required_zero_number(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != 0:
        raise PacketError(f"{field} must remain zero in the incomplete packet")


def validate(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PacketError("result.json must be an object")
    if payload.get("schema") != "m006_shadow_proposal_evidence_v1":
        raise PacketError("unsupported M006 evidence schema")
    if payload.get("status") != "incomplete":
        raise PacketError("preparation record must remain incomplete")
    if payload.get("canonical_capture_ready") is not False:
        raise PacketError("canonical_capture_ready must be false until all receipts pass")
    if payload.get("operator_capture_authorized") is not False:
        raise PacketError("operator capture authorization must be false in this packet")

    criteria = payload.get("criteria")
    if not isinstance(criteria, dict) or set(criteria) != {"M006-06", "M006-07"}:
        raise PacketError("criteria must contain exactly M006-06 and M006-07")
    if any(
        not isinstance(item, dict) or item.get("status") != "Unmet"
        for item in criteria.values()
    ):
        raise PacketError("preparation cannot mark either criterion Met")

    receipts = _required_fields(
        payload.get("readiness_receipts"),
        ("proposal_acceptance", "procedure_and_checks", "D1", "D2", "operator_capture_authorization"),
        "readiness_receipts",
    )
    receipt_fields = {
        "proposal_acceptance": ("status", "source", "review", "observation"),
        "procedure_and_checks": ("status", "source", "observation"),
        "D1": ("status", "owner", "minimum_capability", "observation", "impact", "next"),
        "D2": ("status", "owner", "minimum_capability", "observation", "impact", "next"),
        "operator_capture_authorization": ("status", "observation", "required_after"),
    }
    for name, fields in receipt_fields.items():
        _required_fields(receipts[name], fields, f"readiness_receipts.{name}")
    if receipts["proposal_acceptance"].get("status") != "verified":
        raise PacketError("proposal acceptance receipt must remain verified")
    if receipts["procedure_and_checks"].get("status") != "frozen_for_operator_review":
        raise PacketError("procedure and checks receipt must remain frozen for operator review")
    if receipts["D1"].get("status") != "blocked":
        raise PacketError("D1 must remain blocked until a genuine physical shadow-cycle receipt exists")
    if receipts["D2"].get("status") != "blocked":
        raise PacketError("D2 must remain blocked until a live decision-view receipt exists")
    if receipts["operator_capture_authorization"].get("status") != "pending":
        raise PacketError("operator authorization must remain pending")

    cases = payload.get("case_outcomes")
    if not isinstance(cases, dict) or set(cases) != {f"C{i}" for i in range(1, 8)}:
        raise PacketError("case_outcomes must contain C1 through C7")
    if any(
        not isinstance(item, dict) or item.get("status") != "not_run"
        for item in cases.values()
    ):
        raise PacketError("canonical cases cannot be passed before capture readiness")

    prep = _required_fields(
        payload.get("preparatory_checks"),
        ("chase_passive_status", "decision_info", "offline_public_door"),
        "preparatory_checks",
    )
    passive = _required_fields(
        prep["chase_passive_status"],
        ("status", "command", "observation", "next_action"),
        "preparatory_checks.chase_passive_status",
    )
    if passive.get("status") != "blocked":
        raise PacketError("Chase passive status must remain blocked before capture authorization")

    decision_info = _required_fields(
        prep["decision_info"],
        ("status", "command", "observation", "live_capture_eligible"),
        "preparatory_checks.decision_info",
    )
    if decision_info.get("status") != "passed_contract_probe":
        raise PacketError("decision info receipt must remain a contract probe")
    _required_false(
        decision_info["live_capture_eligible"],
        "preparatory_checks.decision_info.live_capture_eligible",
    )

    public = _required_fields(
        prep["offline_public_door"],
        (
            "status",
            "record",
            "fixture_sha256",
            "digest_sha256",
            "proposed_steering",
            "authorized_steering",
            "authorized_throttle",
            "proposed_applied",
            "source_image",
            "limitation",
        ),
        "preparatory_checks.offline_public_door",
    )
    if public.get("status") != "passed_preparatory_only":
        raise PacketError("offline public-door check must be explicitly preparatory")
    digest = public["digest_sha256"]
    if not isinstance(digest, str) or len(digest) != 64:
        raise PacketError("preparatory digest must be a SHA-256 string")
    _required_zero_number(
        public["authorized_steering"],
        "preparatory_checks.offline_public_door.authorized_steering",
    )
    _required_zero_number(
        public["authorized_throttle"],
        "preparatory_checks.offline_public_door.authorized_throttle",
    )
    _required_false(public["proposed_applied"], "preparatory_checks.offline_public_door.proposed_applied")
    if public.get("source_image") is not None:
        raise PacketError("fixture source_image must stay null; it is not visual live evidence")

    packages = _required_fields(
        payload.get("environment_packages"),
        ("chase", "piracer"),
        "environment_packages",
    )
    for environment in ("chase", "piracer"):
        package = _required_fields(
            packages[environment],
            ("status", "path", "interval_coverage", "authoritative_host_observation"),
            f"environment_packages.{environment}",
        )
        if package["status"] != "not_captured":
            raise PacketError("canonical environment packages must not be fabricated")
        if package["interval_coverage"] != "none":
            raise PacketError(f"environment_packages.{environment}.interval_coverage must remain none")
        _required_false(
            package["authoritative_host_observation"],
            f"environment_packages.{environment}.authoritative_host_observation",
        )

    coverage = _required_fields(
        payload.get("interval_coverage"),
        ("chase", "piracer"),
        "interval_coverage",
    )
    for environment in ("chase", "piracer"):
        interval = _required_fields(
            coverage[environment],
            ("status", "accepted_intervals", "uncovered_intervals"),
            f"interval_coverage.{environment}",
        )
        if interval["status"] != "unavailable":
            raise PacketError(f"interval_coverage.{environment}.status must remain unavailable")
        if type(interval["accepted_intervals"]) is not int or interval["accepted_intervals"] != 0:
            raise PacketError(f"interval_coverage.{environment}.accepted_intervals must remain zero")
        if interval["uncovered_intervals"] != "all":
            raise PacketError(f"interval_coverage.{environment}.uncovered_intervals must remain all")

    cleanup = _required_fields(
        payload.get("cleanup"),
        (
            "live_worker_started",
            "simulator_started_or_changed",
            "vehicle_control_applied",
            "pilot_output_observed",
            "temporary_probe_runtime",
        ),
        "cleanup",
    )
    for field in (
        "live_worker_started",
        "simulator_started_or_changed",
        "vehicle_control_applied",
        "pilot_output_observed",
    ):
        _required_false(cleanup[field], f"cleanup.{field}")
    return payload


def _load(path: Path) -> tuple[dict[str, Any], str]:
    import hashlib

    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    digest = hashlib.sha256(raw).hexdigest()
    return validate(payload), digest


def _check_html(record: Path, page: Path) -> None:
    payload, record_sha256 = _load(record)
    expected = render(payload, record_sha256=record_sha256)
    if not page.exists() or page.read_text(encoding="utf-8") != expected:
        raise PacketError(f"{page} is not derived from {record}")


def _self_test() -> None:
    payload, _ = _load(RECORD)
    mutated = copy.deepcopy(payload)
    mutated["canonical_capture_ready"] = True
    try:
        validate(mutated)
    except PacketError:
        return
    raise PacketError("self-test failed: fabricated readiness was accepted")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, default=RECORD)
    parser.add_argument("--html", type=Path, default=PAGE)
    parser.add_argument("--check-html", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    payload, _ = _load(args.record)
    print(f"OK: {args.record} status={payload['status']} canonical_capture_ready={payload['canonical_capture_ready']}")
    if args.check_html:
        _check_html(args.record, args.html)
        print(f"OK: {args.html} is derived from {args.record}")
    if args.self_test:
        _self_test()
        print("OK: fabricated readiness mutation is rejected")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, PacketError) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
