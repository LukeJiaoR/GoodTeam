from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from goodteam_os.execution.pr_diff_contract import relative_artifact_path
from goodteam_os.execution.review_workbench_contract import (
    REVIEW_WORKBENCH_SCHEMA_VERSION,
)


BROWSER_JSON_FILENAME = "review-workbench.json"
BROWSER_HTML_FILENAME = "review-workbench.html"


def export_review_workbench_html_artifacts(
    project_root: Path,
    review: dict[str, Any],
) -> dict[str, str]:
    execution_id = str(review.get("execution_id", "")).strip()
    if not execution_id:
        raise ValueError("review workbench execution_id is required")
    html_path = _artifact_path(project_root, execution_id, BROWSER_HTML_FILENAME)
    json_path = _artifact_path(project_root, execution_id, BROWSER_JSON_FILENAME)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(_json_dump(review), encoding="utf-8")
    html_path.write_text(render_review_workbench_html(review), encoding="utf-8")
    return {
        "status": "exported",
        "execution_id": execution_id,
        "schema_version": str(review.get("schema_version", "")),
        "html_path": str(html_path),
        "json_path": str(json_path),
    }


def render_review_workbench_html(review: dict[str, Any]) -> str:
    execution_id = _text(review.get("execution_id"))
    status = _text(review.get("status"))
    embedded_json = _json_dump(review).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Review Workbench - {escape(execution_id)}</title>
  <style>{_stylesheet()}</style>
</head>
<body data-schema-version="{REVIEW_WORKBENCH_SCHEMA_VERSION}">
  <main class="shell">
    <header class="page-header">
      <p class="eyebrow">GoodTeam Review Workbench</p>
      <h1>Review Workbench - {escape(execution_id)}</h1>
      <span class="status-pill">{escape(status or "unknown")}</span>
    </header>
    {_summary_section(review)}
    {_diff_section(review)}
    {_evidence_section(review)}
    {_warnings_section(review)}
    {_timeline_section(review)}
  </main>
  <script id="review-workbench-data" type="application/json">{embedded_json}</script>
</body>
</html>
"""


def _summary_section(review: dict[str, Any]) -> str:
    pr = _dict(review.get("pr"))
    evidence = _dict(review.get("evidence"))
    ci = _dict(evidence.get("ci_summary"))
    review_summary = _dict(evidence.get("review_summary"))
    integrity = _dict(review.get("memory_provenance_integrity"))
    return _section(
        "Execution summary",
        _details(
            [
                ("Execution ID", review.get("execution_id")),
                ("Status", review.get("status")),
                ("Source issue", review.get("source_issue") or "n/a"),
                ("PR", pr.get("ref") or "n/a"),
                ("Branch", pr.get("branch_name") or "n/a"),
                ("Commit", pr.get("commit_sha") or "n/a"),
                ("CI", ci.get("state") or "unavailable"),
                ("Review observations", _review_observation_label(review_summary)),
                ("Memory provenance", _memory_integrity_label(integrity)),
                ("Decision", _decision_label(_dict(review.get("reviewer_decision")))),
                ("Sandbox", _sandbox_label(_dict(review.get("sandbox")))),
            ]
        ),
    )


def _diff_section(review: dict[str, Any]) -> str:
    diff = _dict(review.get("diff"))
    if not diff.get("available"):
        body = _empty_state("No changed files available from persisted diff artifact.")
        body += _details([("Reason", diff.get("reason") or "diff_artifact_missing")])
        return _section("Changed files", body)
    rows = [
        "<li>"
        f"<code>{escape(_text(item.get('path')))}</code>"
        f"<span>{escape(_text(item.get('status')))}</span>"
        f"<span>+{escape(_text(item.get('additions', 0)))}"
        f" / -{escape(_text(item.get('deletions', 0)))}</span>"
        "</li>"
        for item in _dict_items(diff.get("changed_files"))
    ]
    body = _details([("Diff summary", diff.get("summary") or "")])
    body += f'<ul class="file-list">{"".join(rows) or _list_empty("No files.")}</ul>'
    return _section("Changed files", body)


def _evidence_section(review: dict[str, Any]) -> str:
    rationale = _dict(review.get("rationale"))
    memory_refs = _dict_items(review.get("memory_refs"))
    body = _details(
        [
            ("Rationale", _rationale_label(rationale)),
            ("Worker result", _worker_result_label(_dict(rationale.get("worker_result")))),
            ("Memory refs", str(len(memory_refs))),
        ]
    )
    if memory_refs:
        body += _memory_ref_list(memory_refs)
    else:
        body += _empty_state("No memory provenance refs recorded.")
    return _section("Evidence provenance", body)


def _warnings_section(review: dict[str, Any]) -> str:
    warnings = _dict_items(review.get("warnings"))
    sandbox = _dict(review.get("sandbox"))
    if not warnings and sandbox.get("available"):
        return _section("Warnings and empty states", _empty_state("No warnings."))
    items = [_warning_item(item) for item in warnings]
    if not sandbox.get("available"):
        items.append(_warning_item({"code": sandbox.get("reason"), "message": _sandbox_empty_message()}))
    return _section("Warnings and empty states", f'<ul class="warning-list">{"".join(items)}</ul>')


def _timeline_section(review: dict[str, Any]) -> str:
    events = _dict_items(_dict(review.get("evidence")).get("ledger_events"))
    rows = []
    for event in events:
        payload = _dict(event.get("payload"))
        transition = payload.get("transition") or event.get("event_type")
        rows.append(
            "<li>"
            f"<time>{escape(_text(event.get('created_at')))}</time>"
            f"<span>{escape(_text(transition))}</span>"
            "</li>"
        )
    return _section("Ledger timeline", f'<ol class="timeline">{"".join(rows) or _list_empty("No ledger events.")}</ol>')


def _section(title: str, body: str) -> str:
    return f'<section class="panel"><h2>{escape(title)}</h2>{body}</section>'


def _details(items: list[tuple[str, Any]]) -> str:
    rows = [f"<dt>{escape(label)}</dt><dd>{escape(_text(value))}</dd>" for label, value in items]
    return f'<dl class="details">{"".join(rows)}</dl>'


def _warning_item(item: dict[str, Any]) -> str:
    code = _text(item.get("code") or "warning")
    message = _text(item.get("message") or "")
    return f"<li><strong>{escape(code)}</strong><span>{escape(message)}</span></li>"


def _memory_ref_list(memory_refs: list[dict[str, Any]]) -> str:
    items = []
    for ref in memory_refs:
        label = ref.get("memory_event_id") or ref.get("ledger_event_id") or "memory-ref"
        items.append(f"<li><code>{escape(_text(label))}</code></li>")
    return f'<ul class="memory-list">{"".join(items)}</ul>'


def _artifact_path(project_root: Path, execution_id: str, filename: str) -> Path:
    return Path(project_root) / relative_artifact_path(execution_id, filename)


def _json_dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_items(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value) if value is not None else ""


def _review_observation_label(summary: dict[str, Any]) -> str:
    return f"{summary.get('reviews', 0)} reviews, {summary.get('comments', 0)} comments"


def _memory_integrity_label(integrity: dict[str, Any]) -> str:
    if integrity.get("complete") is True:
        return "complete"
    if integrity.get("pending") is True:
        return f"pending ({integrity.get('pending_reason', '')})"
    return "incomplete"


def _decision_label(decision: dict[str, Any]) -> str:
    if not decision.get("available"):
        return "No review decision recorded."
    return f"{decision.get('decision', '')} by {decision.get('reviewer', '')}"


def _sandbox_label(sandbox: dict[str, Any]) -> str:
    if sandbox.get("available"):
        return _text(sandbox.get("summary") or "available")
    return f"Unavailable - {sandbox.get('reason', 'sandbox_evidence_not_implemented')}"


def _rationale_label(rationale: dict[str, Any]) -> str:
    if rationale.get("available"):
        return "available"
    return f"unavailable ({rationale.get('reason', 'rationale_artifact_missing')})"


def _worker_result_label(worker_result: dict[str, Any]) -> str:
    if worker_result.get("available"):
        return _text(worker_result.get("summary") or worker_result.get("path") or "available")
    return "No rationale artifact is available."


def _sandbox_empty_message() -> str:
    return "Sandbox evidence is not implemented in this MVP."


def _empty_state(message: str) -> str:
    return f'<p class="empty-state">{escape(message)}</p>'


def _list_empty(message: str) -> str:
    return f"<li>{escape(message)}</li>"


def _stylesheet() -> str:
    return """
:root { color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
body { margin: 0; background: #f6f7f9; color: #151922; }
.shell { width: min(1120px, calc(100% - 32px)); margin: 0 auto; padding: 32px 0 48px; }
.page-header { display: grid; gap: 8px; margin-bottom: 16px; }
.eyebrow { margin: 0; color: #5d6b82; font-size: 12px; font-weight: 800; text-transform: uppercase; }
h1 { margin: 0; font-size: clamp(28px, 4vw, 44px); letter-spacing: 0; }
h2 { margin: 0 0 14px; font-size: 16px; letter-spacing: 0; }
.status-pill { width: max-content; border: 1px solid #b8c2d6; border-radius: 999px; padding: 5px 10px; font-size: 12px; font-weight: 800; text-transform: uppercase; }
.panel { background: #ffffff; border: 1px solid #dce2ec; border-radius: 8px; padding: 18px; margin-top: 14px; }
.details { display: grid; grid-template-columns: minmax(140px, 220px) 1fr; gap: 10px 16px; margin: 0; }
dt { color: #5d6b82; font-size: 12px; font-weight: 800; text-transform: uppercase; }
dd { margin: 0; min-width: 0; overflow-wrap: anywhere; font-weight: 650; }
ul, ol { margin: 0; padding-left: 18px; }
.file-list, .warning-list, .timeline, .memory-list { display: grid; gap: 8px; }
.file-list li, .timeline li { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 12px; align-items: center; }
.warning-list li { display: grid; gap: 3px; }
code, time { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.empty-state { margin: 0 0 12px; color: #5d6b82; font-weight: 650; }
@media (max-width: 720px) {
  .details, .file-list li, .timeline li { grid-template-columns: 1fr; }
}
"""
