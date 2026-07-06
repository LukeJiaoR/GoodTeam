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


def render_review_workbench_html(
    review: dict[str, Any],
    *,
    decision_actions_enabled: bool = False,
) -> str:
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
    {_decision_section(review, decision_actions_enabled=decision_actions_enabled)}
    {_diff_section(review)}
    {_evidence_section(review)}
    {_warnings_section(review)}
    {_timeline_section(review)}
  </main>
  <script id="review-workbench-data" type="application/json">{embedded_json}</script>
  {_decision_script() if decision_actions_enabled else ""}
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
    files = _dict_items(diff.get("changed_files"))
    body = _details([("Diff summary", diff.get("summary") or "")])
    body += _diff_file_nav(files)
    body += _diff_file_sections(files, _text(diff.get("patch_state") or "available"))
    return _section("Changed files", body)


def _diff_file_nav(files: list[dict[str, Any]]) -> str:
    rows = []
    for item in files:
        rows.append(
            "<li>"
            f"<a href=\"#{escape(_text(item.get('anchor')))}\"><code>{escape(_text(item.get('path')))}</code></a>"
            f"<span>{escape(_text(item.get('status')))}</span>"
            f"<span>+{escape(_text(item.get('additions', 0)))}"
            f" / -{escape(_text(item.get('deletions', 0)))}</span>"
            "</li>"
        )
    return (
        '<nav class="diff-file-nav" aria-label="Changed files">'
        f'<ul class="diff-file-nav-list">{"".join(rows) or _list_empty("No files.")}</ul>'
        "</nav>"
    )


def _diff_file_sections(files: list[dict[str, Any]], patch_state: str) -> str:
    if not files:
        return _empty_state("No files.")
    return "".join(_diff_file_section(file, patch_state) for file in files)


def _diff_file_section(file: dict[str, Any], patch_state: str) -> str:
    hunks = _dict_items(file.get("hunks"))
    body = (
        f'<article class="diff-file-section" id="{escape(_text(file.get("anchor")))}">'
        "<header>"
        f"<h3><code>{escape(_text(file.get('path')))}</code></h3>"
        "<p>"
        f"{escape(_text(file.get('status')))} "
        f"+{escape(_text(file.get('additions', 0)))} "
        f"-{escape(_text(file.get('deletions', 0)))}"
        "</p>"
        "</header>"
    )
    body += _rationale_evidence(file)
    if not hunks:
        body += _empty_state(f"No parsed patch hunks available ({patch_state}).")
    else:
        body += "".join(_diff_hunk(hunk) for hunk in hunks)
    return body + "</article>"


def _rationale_evidence(file: dict[str, Any]) -> str:
    status = _text(file.get("rationale_status") or "not_mapped")
    refs = _dict_items(file.get("rationale_refs"))
    body = (
        '<div class="rationale-evidence">'
        "<h4>Rationale evidence</h4>"
        f"{_details([('status', status)])}"
    )
    if refs:
        body += f'<ul class="rationale-ref-list">{"".join(_rationale_ref_item(ref) for ref in refs)}</ul>'
    else:
        body += _empty_state(_rationale_empty_message(status))
    return body + "</div>"


def _rationale_ref_item(ref: dict[str, Any]) -> str:
    return (
        "<li>"
        + _details(
            [
                ("kind", ref.get("kind")),
                ("name", ref.get("name") or "n/a"),
                ("title", ref.get("title")),
                ("path", ref.get("path")),
                ("confidence", ref.get("confidence")),
                ("excerpt", ref.get("excerpt")),
            ]
        )
        + "</li>"
    )


def _rationale_empty_message(status: str) -> str:
    if status == "rationale_artifact_missing":
        return "Rationale artifacts missing."
    return "No file-level rationale mapping available."


def _diff_hunk(hunk: dict[str, Any]) -> str:
    rows = [_diff_line_row(line) for line in _dict_items(hunk.get("lines"))]
    return (
        '<div class="diff-hunk">'
        f"<h4><code>{escape(_text(hunk.get('header')))}</code></h4>"
        '<table class="diff-hunk-table"><tbody>'
        f'{"".join(rows) or _diff_empty_row()}'
        "</tbody></table>"
        "</div>"
    )


def _diff_line_row(line: dict[str, Any]) -> str:
    kind = _diff_line_kind(line.get("kind"))
    return (
        f'<tr class="diff-line diff-line-{kind}">'
        f'<td class="diff-line-number">{escape(_text(line.get("old_line")))}</td>'
        f'<td class="diff-line-number">{escape(_text(line.get("new_line")))}</td>'
        f'<td class="diff-line-code"><code>{escape(_text(line.get("text")))}</code></td>'
        "</tr>"
    )


def _diff_empty_row() -> str:
    return '<tr class="diff-line diff-line-metadata"><td></td><td></td><td>No diff lines.</td></tr>'


def _diff_line_kind(value: Any) -> str:
    kind = _text(value)
    return kind if kind in {"add", "delete", "context", "metadata"} else "metadata"


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


def _decision_section(review: dict[str, Any], *, decision_actions_enabled: bool) -> str:
    decision = _dict(review.get("reviewer_decision"))
    disabled = "" if decision_actions_enabled else " disabled"
    note = (
        '<p class="action-note" id="decision-action-note">Ready to record through the local server route.</p>'
        if decision_actions_enabled
        else '<p class="action-note" id="decision-action-note">Decision actions require local server route.</p>'
    )
    body = _details(
        [
            ("Current decision", _decision_label(decision)),
            ("Reason", decision.get("reason") or "n/a"),
        ]
    )
    body += note
    body += f"""
<form id="review-decision-form" class="decision-form" data-actions-enabled="{str(decision_actions_enabled).lower()}">
  <label>Reviewer <input name="reviewer" value="{escape(_text(decision.get("reviewer")))}" autocomplete="off"{disabled}></label>
  <label>Decision {_decision_select(_text(decision.get("decision")), disabled=disabled)}</label>
  <label>Reason <textarea name="reason" rows="4"{disabled}>{escape(_text(decision.get("reason")))}</textarea></label>
  <button type="submit"{disabled}>Submit decision</button>
  <p class="action-status" id="review-decision-status" role="status"></p>
</form>
"""
    return _section("Review decision", body)


def _decision_select(current_decision: str, *, disabled: str) -> str:
    options = []
    for value, label in [
        ("approve", "approve"),
        ("request_changes", "request_changes"),
        ("reject", "reject"),
    ]:
        selected = " selected" if value == current_decision else ""
        options.append(f'<option value="{value}"{selected}>{label}</option>')
    return f'<select name="decision"{disabled}>{"".join(options)}</select>'


def _decision_script() -> str:
    return """
<script>
(() => {
  const form = document.getElementById("review-decision-form");
  const statusEl = document.getElementById("review-decision-status");
  const dataEl = document.getElementById("review-workbench-data");
  const currentDecision = document.querySelector(".details dd");
  if (!form || !statusEl || !dataEl) return;

  const initial = JSON.parse(dataEl.textContent || "{}");
  const executionId = initial.execution_id || "";
  const params = new URLSearchParams(window.location.search);
  const projectRoot = params.get("project_root") || "";

  function setStatus(message, state) {
    statusEl.textContent = message;
    statusEl.dataset.state = state;
  }

  function decisionLabel(decision) {
    if (!decision || !decision.available) return "No review decision recorded.";
    return `${decision.decision || ""} by ${decision.reviewer || ""}`;
  }

  function updateCurrentDecision(decision) {
    const details = Array.from(document.querySelectorAll(".details"));
    const decisionDetails = details.find((item) => item.textContent.includes("Current decision"));
    const value = decisionDetails ? decisionDetails.querySelector("dd") : currentDecision;
    if (value) value.textContent = decisionLabel(decision);
  }

  async function refreshWorkbench() {
    const query = new URLSearchParams({ project_root: projectRoot });
    const response = await fetch(`/api/executions/${encodeURIComponent(executionId)}/review-workbench?${query}`);
    const payload = await response.json();
    if (response.ok) updateCurrentDecision(payload.reviewer_decision);
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!projectRoot) {
      setStatus("project_root is required in the page URL.", "error");
      return;
    }
    const formData = new FormData(form);
    const body = {
      project_root: projectRoot,
      reviewer: String(formData.get("reviewer") || ""),
      decision: String(formData.get("decision") || ""),
      reason: String(formData.get("reason") || ""),
    };
    setStatus("Recording decision...", "pending");
    try {
      const response = await fetch(`/api/executions/${encodeURIComponent(executionId)}/review-workbench/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Decision request failed.");
      setStatus(`Decision ${payload.status}.`, "success");
      updateCurrentDecision(payload.reviewer_decision);
      await refreshWorkbench();
    } catch (error) {
      setStatus(error.message || "Decision request failed.", "error");
    }
  });
})();
</script>
"""


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
.diff-file-nav { margin: 14px 0 16px; }
.diff-file-nav-list, .warning-list, .timeline, .memory-list { display: grid; gap: 8px; }
.diff-file-nav-list li, .timeline li { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 12px; align-items: center; }
.diff-file-nav-list a { color: #151922; text-decoration-thickness: 1px; text-underline-offset: 3px; }
.diff-file-section { border-top: 1px solid #dce2ec; padding-top: 16px; margin-top: 16px; }
.diff-file-section header { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; align-items: start; margin-bottom: 12px; }
.diff-file-section h3 { margin: 0; font-size: 15px; letter-spacing: 0; overflow-wrap: anywhere; }
.diff-file-section p { margin: 0; color: #5d6b82; font-weight: 800; }
.diff-hunk { border: 1px solid #dce2ec; border-radius: 6px; overflow: hidden; margin-top: 10px; }
.diff-hunk h4 { margin: 0; background: #f2f5f9; border-bottom: 1px solid #dce2ec; font-size: 12px; padding: 8px 10px; }
.diff-hunk-table { width: 100%; border-collapse: collapse; table-layout: fixed; }
.diff-line-number { width: 56px; background: #f7f9fc; border-right: 1px solid #dce2ec; color: #5d6b82; font-variant-numeric: tabular-nums; text-align: right; user-select: none; }
.diff-line td { padding: 3px 8px; vertical-align: top; }
.diff-line-code code { display: block; overflow-wrap: anywhere; white-space: pre-wrap; }
.diff-line-add .diff-line-code { background: #e8f6ee; }
.diff-line-delete .diff-line-code { background: #fdecea; }
.diff-line-context .diff-line-code { background: #ffffff; }
.diff-line-metadata .diff-line-code { background: #f7f9fc; color: #5d6b82; }
.rationale-evidence { border: 1px solid #dce2ec; border-radius: 6px; margin: 10px 0 12px; padding: 12px; }
.rationale-evidence h4 { margin: 0 0 10px; font-size: 12px; letter-spacing: 0; text-transform: uppercase; }
.rationale-ref-list { display: grid; gap: 10px; list-style: none; margin-top: 10px; padding-left: 0; }
.rationale-ref-list li { border-top: 1px solid #eef2f7; padding-top: 10px; }
.warning-list li { display: grid; gap: 3px; }
code, time { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.empty-state { margin: 0 0 12px; color: #5d6b82; font-weight: 650; }
.decision-form { display: grid; gap: 12px; margin-top: 14px; }
.decision-form label { display: grid; gap: 6px; color: #5d6b82; font-size: 12px; font-weight: 800; text-transform: uppercase; }
.decision-form input, .decision-form select, .decision-form textarea { border: 1px solid #b8c2d6; border-radius: 6px; color: #151922; font: inherit; font-size: 14px; padding: 9px 10px; text-transform: none; }
.decision-form textarea { resize: vertical; }
.decision-form button { width: max-content; border: 0; border-radius: 6px; background: #151922; color: #ffffff; cursor: pointer; font-weight: 800; padding: 10px 14px; }
.decision-form button:disabled, .decision-form input:disabled, .decision-form select:disabled, .decision-form textarea:disabled { cursor: not-allowed; opacity: 0.62; }
.action-note, .action-status { color: #5d6b82; font-weight: 650; margin: 12px 0 0; }
.action-status[data-state="error"] { color: #a13d2d; }
.action-status[data-state="success"] { color: #246b45; }
@media (max-width: 720px) {
  .details, .diff-file-nav-list li, .timeline li, .diff-file-section header { grid-template-columns: 1fr; }
  .diff-line-number { width: 42px; }
}
"""
