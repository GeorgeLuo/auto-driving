"""Standalone HTML from the same JSON record consumed by agents."""

from __future__ import annotations

import html
import json
from typing import Any


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    return (
        '<div class="scroll"><table><thead><tr>'
        + "".join(f"<th>{_escape(item)}</th>" for item in headers)
        + "</tr></thead><tbody>"
        + "".join("<tr>" + "".join(f"<td>{_escape(item)}</td>" for item in row) + "</tr>" for row in rows)
        + "</tbody></table></div>"
    )


def render_html(payload: dict[str, Any], *, title: str = "Quantitative change analysis") -> str:
    """Render a per-analysis or historical batch record without external assets."""

    sections = [f"<h1>{_escape(title)}</h1>"]
    identity = payload.get("identity", {})
    if identity:
        sections.append(_table(["Run", "Value"], [
            ["Analyzer", identity["analyzer_version"]],
            ["Base", identity.get("base_sha") or "snapshot"],
            ["Head", identity.get("head_sha") or identity.get("working_tree_digest")],
            ["Scope", identity["analyzed_path"]],
        ]))
        diff = payload.get("diff")
        if diff:
            sections.extend(["<h2>Change</h2>", _table(["Measurement", "Value"], [
                ["Changed files", len(diff["changed_files"])],
                ["Core changed files", len(diff["included_changed_files"])],
                ["Added / deleted lines", f"{diff['added_lines']} / {diff['deleted_lines']}"],
                ["Core churn", diff["included_churn"]],
                ["Decision burden delta", diff["decision_burden_delta"]],
            ])])
            sections.append(_table(["Source class", "Files", "Added", "Deleted"], [
                [key, value["files"], value["added_lines"], value["deleted_lines"]]
                for key, value in diff["changed_by_class"].items() if value["files"]
            ]))
            sections.extend(["<details><summary>Changed files</summary>", _table(
                ["File", "Source class", "Core measured", "Added", "Deleted"],
                [[item["path"], item["source_class"], item["included"], item["added_lines"], item["deleted_lines"]]
                 for item in diff["file_changes"]],
            ), "</details>"])
        else:
            sections.append(_table(["Snapshot measurement", "Value"], [
                [key, value] for key, value in payload["head"].items() if isinstance(value, int)
            ]))
        sections.append("<h2>Factors</h2><p>Metrics cover the configured Python scope. Diff candidates point at changed files. Recognized static patterns are inspection leads; behavior evidence is shown separately.</p>")
        for name, factor in payload.get("factors", {}).items():
            sections.extend([f"<h3>{_escape(name.replace('_', ' '))}</h3>", f"<p>Status: {_escape(factor['status'])}</p>"])
            if factor["metrics"]:
                sections.append(_table(["Measurement", "Base", "Head", "Delta"], [
                    [key, factor.get("base_metrics", {}).get(key, "—"), value, factor.get("delta", {}).get(key, "—")]
                    for key, value in factor["metrics"].items()
                ]))
            findings = factor["findings"]
            if findings:
                sections.append(f"<details><summary>{len(findings)} inspection candidates</summary>")
                sections.append(_table(["Location", "Pattern", "Reason"], [
                    [f"{item.get('path', '')}:{item.get('line', '')}", item.get("kind", ""), item["message"]]
                    for item in findings
                ]))
                sections.append("</details>")
            if factor.get("verification"):
                sections.append("<pre>" + _escape(json.dumps(factor["verification"], indent=2)) + "</pre>")
            sections.append("<p class=limit>" + _escape(" ".join(factor["limitations"])) + "</p>")
        if diff:
            targets = diff["review_targets"]
            sections.extend([f"<details><summary>{len(targets)} structural review targets</summary>", _table(
                ["Kind", "Target", "Reason"], [
                    [item["kind"], item.get("path", item.get("edge", item.get("symbol", ""))), "; ".join(item["reasons"])]
                    for item in targets
                ]
            ), "</details>"])
    else:
        sections.append("<p>Historical experiment record. Per-analysis reports are linked from its accompanying index.</p>")
        for state in payload.get("states", []):
            diff = state["diff"]
            sections.append(f"<h2>{_escape(state['id'])}</h2>")
            sections.append(_table(["Base", "Head", "Files", "Churn"], [[
                state["base"], state["head"], len(diff["changed_files"]), diff["churn"],
            ]]))
    sections.append(
        "<details><summary>Complete JSON record</summary><pre>"
        + _escape(json.dumps(payload, indent=2, sort_keys=True))
        + "</pre></details>"
    )
    body = "\n".join(sections)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_escape(title)}</title><style>
*{{box-sizing:border-box}}body{{margin:0;color:#20312f;background:#f6f8f7;font:15px/1.55 system-ui,sans-serif}}
main{{max-width:1140px;margin:auto;padding:28px 24px 64px}}h1{{font-size:26px}}h2{{margin-top:32px;font-size:21px}}h3{{margin-top:28px;font-size:17px}}
.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;margin:12px 0;background:white;font-size:13px}}th,td{{border:1px solid #d8e1dc;padding:8px;text-align:left;vertical-align:top;overflow-wrap:anywhere}}th{{background:#eaf0ec}}
details{{margin:16px 0}}summary{{cursor:pointer;padding:10px 0}}pre{{max-height:65vh;overflow:auto;padding:16px;background:#1d2e2b;color:#e6f1eb;font-size:12px}}.limit{{font-size:13px;color:#596962}}
</style></head><body><main>{body}</main></body></html>
'''
