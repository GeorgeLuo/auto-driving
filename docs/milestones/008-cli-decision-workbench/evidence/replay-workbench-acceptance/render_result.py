#!/usr/bin/env python3
"""Render result.html from the committed result.json in this directory."""

from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECORD = HERE / "result.json"
PAGE = HERE / "result.html"
HASHED_SIBLINGS = (
    "README.md",
    "render_result.py",
    "record_session.py",
    "browser-view.png",
    "cli-transcript.txt",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _refresh_artifact_digests(payload: dict[str, object]) -> dict[str, object]:
    artifacts: list[dict[str, object]] = []
    for name in HASHED_SIBLINGS:
        path = HERE / name
        if path.is_file():
            artifacts.append(
                {"path": name, "sha256": _sha256(path), "captured": True}
            )
        else:
            artifacts.append(
                {"path": name, "sha256": None, "captured": False}
            )
    artifacts.append(
        {
            "path": "result.html",
            "sha256": None,
            "captured": True,
            "note": "derived from this result.json; regenerate with render_result.py",
        }
    )
    payload["artifacts"] = artifacts
    return payload


def _cell(value: object) -> str:
    if value is None:
        text = "null"
    elif isinstance(value, str):
        text = value
    else:
        text = json.dumps(value)
    return html.escape(text)


def render(payload: dict[str, object]) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    ).hexdigest()
    status = _cell(payload.get("status"))
    steps = payload.get("steps")
    step_rows = ""
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            step_rows += (
                "<tr>"
                f"<td><code>{html.escape(str(step.get('id', '')))}</code></td>"
                f"<td>{_cell(step.get('status'))}</td>"
                f"<td>{_cell(step.get('required'))}</td>"
                f"<td>{_cell(step.get('observation'))}</td>"
                f"<td>{_cell(step.get('notes'))}</td>"
                "</tr>\n"
            )
    findings = payload.get("findings")
    finding_text = (
        "<p>None recorded.</p>"
        if not findings
        else "<pre>" + html.escape(json.dumps(findings, indent=2)) + "</pre>"
    )
    artifacts = payload.get("artifacts")
    artifact_rows = ""
    if isinstance(artifacts, list):
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            artifact_rows += (
                "<tr>"
                f"<td><code>{html.escape(str(item.get('path', '')))}</code></td>"
                f"<td>{_cell(item.get('captured'))}</td>"
                f"<td><code>{_cell(item.get('sha256'))}</code></td>"
                "</tr>\n"
            )
    env = payload.get("environment")
    env = env if isinstance(env, dict) else {}
    browser = env.get("browser")
    browser = browser if isinstance(browser, dict) else {}
    repo = payload.get("repository")
    repo = repo if isinstance(repo, dict) else {}
    source = payload.get("source")
    source = source if isinstance(source, dict) else {}
    times = payload.get("timestamps")
    times = times if isinstance(times, dict) else {}
    identities = payload.get("identities")
    identities = identities if isinstance(identities, dict) else {}
    shot = payload.get("screenshot")
    shot = shot if isinstance(shot, dict) else {}
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Replay workbench POC acceptance</title>
  <style>
    :root {{ font-family: ui-sans-serif, system-ui, sans-serif; color: #17191c; }}
    body {{ margin: 24px; max-width: 960px; }}
    h1 {{ font-size: 1.25rem; }}
    .meta {{ color: #62676f; }}
    .badge {{
      display: inline-block; border: 1px solid #d9dde2; border-radius: 999px;
      padding: 2px 8px; font-size: 12px; background: #f6f7f8;
    }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #d9dde2; padding: 6px 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f6f7f8; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
    .note {{
      border-left: 3px solid #176b87; padding: 8px 12px; background: #e7f3f8; margin: 16px 0;
    }}
  </style>
</head>
<body>
  <h1>Replay workbench POC acceptance</h1>
  <p class="meta">
    status=<span class="badge">{status}</span>
    · record digest=<code>{html.escape(digest)}</code>
  </p>
  <div class="note">
    This page is derived from committed <code>result.json</code>.
    Regenerate with <code>python3 render_result.py</code>.
    The JSON record is authoritative.
  </div>
  <h2>Receipt</h2>
  <table>
    <tr><th>Operator</th><td>{_cell(payload.get("operator"))}</td></tr>
    <tr><th>Verdict</th><td>{_cell(payload.get("verdict"))}</td></tr>
    <tr><th>Incomplete reason</th><td>{_cell(payload.get("incomplete_reason"))}</td></tr>
    <tr><th>Started</th><td>{_cell(times.get("started_at_utc"))}</td></tr>
    <tr><th>Ended</th><td>{_cell(times.get("ended_at_utc"))}</td></tr>
    <tr><th>OS</th><td>{_cell(env.get("operating_system"))}</td></tr>
    <tr><th>Browser</th><td>{_cell(browser.get("name"))} {_cell(browser.get("version"))}</td></tr>
    <tr><th>Commit</th><td><code>{_cell(repo.get("commit"))}</code></td></tr>
    <tr><th>Worktree</th><td>{_cell(repo.get("worktree_state"))}</td></tr>
    <tr><th>Source</th><td>{_cell(source.get("path_redacted"))}</td></tr>
    <tr><th>Plugin root</th><td>{_cell(source.get("plugin_root"))}</td></tr>
    <tr><th>Loopback URL</th><td>{_cell(source.get("loopback_url"))}</td></tr>
    <tr><th>Server identity</th><td><code>{_cell(identities.get("server_identity"))}</code></td></tr>
    <tr><th>First run id</th><td><code>{_cell(identities.get("first_run_id"))}</code></td></tr>
    <tr><th>Second run id</th><td><code>{_cell(identities.get("second_run_id"))}</code></td></tr>
    <tr><th>Failed run id</th><td><code>{_cell(identities.get("failed_run_id"))}</code></td></tr>
    <tr><th>Recovered run id</th><td><code>{_cell(identities.get("recovered_run_id"))}</code></td></tr>
    <tr><th>Inspect screenshot</th><td>captured={_cell(shot.get("captured"))} redaction={_cell(shot.get("path_redaction"))} during={_cell(shot.get("asked_during"))}</td></tr>
  </table>
  <h2>Procedure steps</h2>
  <table>
    <tr><th>Id</th><th>Status</th><th>Required</th><th>Observation</th><th>Notes</th></tr>
    {step_rows}
  </table>
  <h2>Findings</h2>
  {finding_text}
  <h2>Artifacts</h2>
  <table>
    <tr><th>Path</th><th>Captured</th><th>SHA-256</th></tr>
    {artifact_rows}
  </table>
</body>
</html>
"""


def main() -> int:
    payload = json.loads(RECORD.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("result.json must be an object")
    payload = _refresh_artifact_digests(payload)
    RECORD.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    PAGE.write_text(render(payload), encoding="utf-8")
    print(f"Wrote {PAGE.relative_to(HERE)} from {RECORD.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
