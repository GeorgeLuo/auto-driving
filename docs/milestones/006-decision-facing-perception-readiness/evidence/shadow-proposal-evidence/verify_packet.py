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

    receipts = payload.get("readiness_receipts")
    if not isinstance(receipts, dict):
        raise PacketError("readiness_receipts must be an object")
    for name in ("proposal_acceptance", "procedure_and_checks", "D1", "D2", "operator_capture_authorization"):
        if not isinstance(receipts.get(name), dict):
            raise PacketError(f"missing readiness receipt: {name}")
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

    prep = payload.get("preparatory_checks")
    if not isinstance(prep, dict):
        raise PacketError("preparatory_checks must be an object")
    public = prep.get("offline_public_door")
    if not isinstance(public, dict) or public.get("status") != "passed_preparatory_only":
        raise PacketError("offline public-door check must be explicitly preparatory")
    digest = public.get("digest_sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise PacketError("preparatory digest must be a SHA-256 string")
    if public.get("source_image") is not None:
        raise PacketError("fixture source_image must stay null; it is not visual live evidence")

    packages = payload.get("environment_packages")
    if not isinstance(packages, dict) or set(packages) != {"chase", "piracer"}:
        raise PacketError("environment_packages must contain Chase and PiRacer")
    if any(
        not isinstance(item, dict) or item.get("status") != "not_captured"
        for item in packages.values()
    ):
        raise PacketError("canonical environment packages must not be fabricated")
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
