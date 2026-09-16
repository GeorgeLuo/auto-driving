#!/usr/bin/env python3
"""Render result.html from the adjacent authoritative result.json."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
RECORD = HERE / "result.json"
PAGE = HERE / "result.html"


def _cell(value: Any) -> str:
    if value is None:
        text = "null"
    elif isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, sort_keys=True)
    return html.escape(text)


def _rows(items: dict[str, Any], *, reason_key: str = "reason") -> str:
    rows: list[str] = []
    for key, value in items.items():
        item = value if isinstance(value, dict) else {"status": value}
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(key))}</code></td>"
            f"<td>{_cell(item.get('status'))}</td>"
            f"<td>{_cell(item.get(reason_key) or item.get('observation') or item.get('impact'))}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def render(payload: dict[str, Any], *, record_sha256: str) -> str:
    criteria = payload.get("criteria") if isinstance(payload.get("criteria"), dict) else {}
    receipts = payload.get("readiness_receipts") if isinstance(payload.get("readiness_receipts"), dict) else {}
    cases = payload.get("case_outcomes") if isinstance(payload.get("case_outcomes"), dict) else {}
    prep = payload.get("preparatory_checks") if isinstance(payload.get("preparatory_checks"), dict) else {}
    identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>M006 shadow-proposal evidence</title>
  <style>
    :root {{ font-family: ui-sans-serif, system-ui, sans-serif; color: #17191c; }}
    body {{ margin: 24px; max-width: 1080px; line-height: 1.4; }}
    h1 {{ font-size: 1.35rem; }}
    h2 {{ margin-top: 28px; font-size: 1.05rem; }}
    .meta {{ color: #62676f; }}
    .badge {{ display: inline-block; border: 1px solid #d9dde2; border-radius: 999px; padding: 2px 8px; font-size: 12px; background: #fff4d6; }}
    .blocked {{ border-left: 3px solid #a33; padding: 10px 12px; background: #fff1f1; }}
    .note {{ border-left: 3px solid #176b87; padding: 10px 12px; background: #e7f3f8; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #d9dde2; padding: 6px 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f6f7f8; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; overflow-wrap: anywhere; }}
  </style>
</head>
<body>
  <h1>M006 cross-environment shadow-proposal evidence</h1>
  <p class="meta">status=<span class="badge">{_cell(payload.get('status'))}</span>
    · canonical_capture_ready=<code>{_cell(payload.get('canonical_capture_ready'))}</code>
    · result.json sha256=<code>{html.escape(record_sha256)}</code></p>
  <div class="blocked">
    This page is derived from adjacent committed <code>result.json</code>.
    The record is incomplete and fail-closed: no canonical Chase or PiRacer
    success claim is present, and M006-06/M006-07 remain unmet.
  </div>
  <h2>Identity</h2>
  <table>
    <tr><th>Branch</th><td><code>{_cell(identity.get('implementation_branch'))}</code></td></tr>
    <tr><th>Base</th><td><code>{_cell(identity.get('base'))}</code></td></tr>
    <tr><th>Head</th><td><code>{_cell(identity.get('head'))}</code></td></tr>
    <tr><th>Proposal</th><td>PR {_cell(identity.get('proposal_pr'))}, merge <code>{_cell(identity.get('proposal_merge'))}</code></td></tr>
    <tr><th>Policy</th><td><code>{_cell(identity.get('policy_commit'))}</code></td></tr>
  </table>
  <h2>Criteria</h2>
  <table><tr><th>Criterion</th><th>Status</th><th>Reason</th></tr>{_rows(criteria)}</table>
  <h2>Readiness receipts</h2>
  <table><tr><th>Receipt</th><th>Status</th><th>Observation / impact</th></tr>{_rows(receipts, reason_key='observation')}</table>
  <h2>Case outcomes</h2>
  <table><tr><th>Case</th><th>Status</th><th>Reason</th></tr>{_rows(cases)}</table>
  <h2>Preparatory checks</h2>
  <table><tr><th>Check</th><th>Status</th><th>Observation</th></tr>{_rows(prep, reason_key='observation')}</table>
  <h2>Review boundary</h2>
  <div class="note">
    D1 physical shadow-cycle publication/liveness and D2 live decision URL /
    retained-evidence overlay are provisional candidate conditions. Their
    current blocked receipts prevent canonical capture. Offline replay is
    retained as preparation only and cannot substitute for live evidence.
  </div>
  <p>See <a href="README.md">README.md</a> for the frozen procedure, C1-C7
    map, bounds, and non-claims. The authoritative preparatory record is
    <a href="preparatory/public-door-result.json"><code>preparatory/public-door-result.json</code></a>.</p>
</body>
</html>
"""


def _load_record(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("result.json must be an object")
    return payload, hashlib.sha256(raw).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, default=RECORD)
    parser.add_argument("--output", type=Path, default=PAGE)
    parser.add_argument("--check", action="store_true", help="check output without writing")
    args = parser.parse_args()
    payload, record_sha256 = _load_record(args.record)
    rendered = render(payload, record_sha256=record_sha256)
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"{args.output} is not derived from {args.record}")
        print(f"OK: {args.output} is derived from {args.record}")
        return 0
    args.output.write_text(rendered, encoding="utf-8")
    print(f"Wrote {args.output} from {args.record}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
