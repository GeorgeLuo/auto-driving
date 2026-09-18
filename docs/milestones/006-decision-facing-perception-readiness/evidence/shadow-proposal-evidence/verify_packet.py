#!/usr/bin/env python3
"""Verify incomplete or post-capture M006 evidence packets."""

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
SUCCESS_STATUSES = frozenset({"captured", "accepted"})
OUTCOME_STATUSES = frozenset({"passed", "failed", "blocked", "review"})


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


def _validate_incomplete(payload: dict[str, Any]) -> dict[str, Any]:
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


def _outcome(value: object, context: str, fields: tuple[str, ...]) -> dict[str, Any]:
    item = _required_fields(value, ("status", *fields), context)
    if item["status"] not in OUTCOME_STATUSES:
        allowed = ", ".join(sorted(OUTCOME_STATUSES))
        raise PacketError(f"{context}.status must be one of: {allowed}")
    return item


def _existing_path(root: Path, declared: object, context: str, *, directory: bool = False) -> Path:
    if not isinstance(declared, str) or not declared.strip():
        raise PacketError(f"{context} must be a non-empty relative path")
    relative = Path(declared)
    if relative.is_absolute() or ".." in relative.parts:
        raise PacketError(f"{context} must stay within the evidence root")
    try:
        resolved_root = root.resolve(strict=True)
        resolved = (resolved_root / relative).resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise PacketError(f"{context} does not resolve within the evidence root: {declared}") from exc
    if directory and not resolved.is_dir():
        raise PacketError(f"{context} is not a directory: {declared}")
    if not directory and not resolved.is_file():
        raise PacketError(f"{context} is not a file: {declared}")
    return resolved


def _artifact_path(value: object, context: str) -> str:
    if isinstance(value, str):
        return value
    artifact = _required_fields(value, ("path",), context)
    return artifact["path"]


def _validate_success(payload: dict[str, Any], root: Path | None) -> dict[str, Any]:
    if root is None:
        raise PacketError("success-mode validation requires an evidence root")

    cases = payload.get("case_outcomes")
    if not isinstance(cases, dict) or set(cases) != {f"C{i}" for i in range(1, 8)}:
        raise PacketError("case_outcomes must contain C1 through C7")
    for name, value in cases.items():
        _outcome(value, f"case_outcomes.{name}", ("evidence",))

    packages = _required_fields(
        payload.get("environment_packages"),
        ("chase", "piracer"),
        "environment_packages",
    )
    if set(packages) != {"chase", "piracer"}:
        raise PacketError("environment_packages must contain exactly chase and piracer")
    for environment in ("chase", "piracer"):
        context = f"environment_packages.{environment}"
        package = _outcome(packages[environment], context, ("path", "artifacts"))
        package_root = _existing_path(root, package["path"], f"{context}.path", directory=True)
        artifacts = package["artifacts"]
        if not isinstance(artifacts, list) or not artifacts:
            raise PacketError(f"{context}.artifacts must be a non-empty array")
        for index, value in enumerate(artifacts):
            artifact_context = f"{context}.artifacts[{index}]"
            declared = _artifact_path(value, artifact_context)
            _existing_path(package_root, declared, f"{artifact_context}.path")

    coverage = _required_fields(
        payload.get("interval_coverage"),
        ("chase", "piracer"),
        "interval_coverage",
    )
    if set(coverage) != {"chase", "piracer"}:
        raise PacketError("interval_coverage must contain exactly chase and piracer")
    for environment in ("chase", "piracer"):
        interval = _outcome(
            coverage[environment],
            f"interval_coverage.{environment}",
            ("accepted_intervals", "uncovered_intervals", "evidence"),
        )
        if type(interval["accepted_intervals"]) is not int:
            raise PacketError(
                f"interval_coverage.{environment}.accepted_intervals must be an integer"
            )

    reviews = _required_fields(payload.get("reviews"), ("visual", "operator"), "reviews")
    if set(reviews) != {"visual", "operator"}:
        raise PacketError("reviews must contain exactly visual and operator")
    for name in ("visual", "operator"):
        _outcome(reviews[name], f"reviews.{name}", ("evidence",))
    return payload


def validate(payload: object, *, root: Path | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PacketError("result.json must be an object")
    if payload.get("schema") != "m006_shadow_proposal_evidence_v1":
        raise PacketError("unsupported M006 evidence schema")
    status = payload.get("status")
    if status == "incomplete":
        return _validate_incomplete(payload)
    if status in SUCCESS_STATUSES:
        return _validate_success(payload, root)
    allowed = ", ".join(("incomplete", *sorted(SUCCESS_STATUSES)))
    raise PacketError(f"status must be one of: {allowed}")


def load(path: Path) -> tuple[dict[str, Any], str]:
    import hashlib

    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    digest = hashlib.sha256(raw).hexdigest()
    return validate(payload, root=path.resolve().parent), digest


def check_html(record: Path, page: Path) -> None:
    payload, record_sha256 = load(record)
    expected = render(payload, record_sha256=record_sha256)
    if not page.exists() or page.read_text(encoding="utf-8") != expected:
        raise PacketError(f"{page} is not derived from {record}")


def _self_test() -> None:
    payload, _ = load(RECORD)
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
    parser.add_argument("--html", type=Path)
    parser.add_argument("--check-html", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    payload, _ = load(args.record)
    print(f"OK: {args.record} status={payload['status']}")
    page = args.html or args.record.with_name(PAGE.name)
    if args.check_html or payload["status"] in SUCCESS_STATUSES:
        check_html(args.record, page)
        print(f"OK: {page} is derived from {args.record}")
    if args.self_test:
        _self_test()
        print("OK: fabricated readiness mutation is rejected")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, PacketError) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
