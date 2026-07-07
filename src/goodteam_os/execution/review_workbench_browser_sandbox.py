from __future__ import annotations

from html import escape
from typing import Any


def sandbox_section(review: dict[str, Any]) -> str:
    sandbox = _dict(review.get("sandbox"))
    if not sandbox.get("available"):
        return _section(
            "Sandbox evidence",
            _empty_state("No sandbox evidence recorded.")
            + _details([("Reason", sandbox.get("reason") or "sandbox_evidence_missing")]),
        )
    body = _details(
        [
            ("Status", sandbox.get("status")),
            ("Mode", sandbox.get("mode")),
            ("Workspace path", sandbox.get("workspace_path")),
            ("Worktree path", sandbox.get("worktree_path") or "n/a"),
            ("Branch", sandbox.get("branch_name") or "n/a"),
        ]
    )
    body += _sandbox_warnings(sandbox)
    body += _runtime_details(_dict(sandbox.get("runtime")))
    body += _sandbox_conflicts(_dict_items(sandbox.get("conflict_risks")))
    body += _sandbox_commands(_dict_items(sandbox.get("commands")))
    body += _sandbox_events(_dict_items(sandbox.get("events")))
    return _section("Sandbox evidence", body)


def _sandbox_warnings(sandbox: dict[str, Any]) -> str:
    warnings = []
    if sandbox.get("reason") == "sandbox_evidence_missing":
        warnings.append("Sandbox evidence missing; runtime evidence is shown from worker status.")
    if sandbox.get("cleanup_required"):
        reason = sandbox.get("cleanup_warning_reason") or "cleanup_required"
        warnings.append(f"Sandbox cleanup required: {reason}.")
    if sandbox.get("cleanup_recovery_required"):
        recovery = sandbox.get("recovery_state") or "sandbox_cleanup_recovery_required"
        error = sandbox.get("cleanup_failure_error") or sandbox.get("cleanup_failure_reason") or "n/a"
        warnings.append(f"Cleanup recovery required: {recovery}. Last cleanup failure: {error}.")
    runtime = _dict(sandbox.get("runtime"))
    if runtime.get("runtime_policy_warning"):
        reason = runtime.get("runtime_policy_warning_reason") or "runtime_policy_warning"
        warnings.append(f"Runtime policy warning: {reason}.")
    if runtime.get("runtime_version_warning"):
        reason = runtime.get("runtime_version_failure_reason") or "runtime_version_unavailable"
        warnings.append(f"Runtime version warning: {reason}.")
    if not warnings:
        return ""
    items = "".join(f"<li>{escape(item)}</li>" for item in warnings)
    return f'<ul class="sandbox-warning-list">{items}</ul>'


def _runtime_details(runtime: dict[str, Any]) -> str:
    if not runtime:
        return "<h3>Runtime adapter</h3>" + _empty_state("No runtime adapter evidence recorded.")
    return "<h3>Runtime adapter</h3>" + _details(
        [
            ("Runtime adapter", runtime.get("runtime_adapter")),
            ("Runtime binary", runtime.get("runtime_binary")),
            ("Runtime version", runtime.get("runtime_version")),
            ("Runtime model", runtime.get("runtime_model")),
            ("Runtime cwd", runtime.get("runtime_cwd")),
            ("Workspace mode", runtime.get("runtime_workspace_mode")),
            ("Workspace status at run", runtime.get("runtime_workspace_status")),
            ("Sandbox mode", runtime.get("runtime_sandbox_mode")),
            ("Approval policy", runtime.get("runtime_approval_policy")),
            ("Unsafe mode", runtime.get("runtime_unsafe_mode")),
            ("Policy warning", runtime.get("runtime_policy_warning_reason") or "n/a"),
            ("Transcript path", runtime.get("runtime_transcript_path") or "n/a"),
            ("Stdout path", runtime.get("stdout_path") or "n/a"),
            ("Stderr path", runtime.get("stderr_path") or "n/a"),
            ("Result path", runtime.get("result_path") or "n/a"),
        ]
    )


def _sandbox_conflicts(conflicts: list[dict[str, Any]]) -> str:
    if not conflicts:
        return _empty_state("No sandbox conflict risk recorded.")
    items = []
    for conflict in conflicts:
        items.append(
            "<li>"
            "<strong>Conflict risk</strong>"
            + _details(
                [
                    ("Execution", conflict.get("execution_id")),
                    ("Path", conflict.get("path")),
                    ("Conflicting path", conflict.get("conflicting_path")),
                ]
            )
            + "</li>"
        )
    return f'<ul class="sandbox-conflict-list">{"".join(items)}</ul>'


def _sandbox_commands(commands: list[dict[str, Any]]) -> str:
    if not commands:
        return "<h3>Command history</h3>" + _empty_state("No sandbox commands recorded.")
    items = []
    for command in commands:
        items.append(
            "<li>"
            + _details(
                [
                    ("Transition", command.get("transition")),
                    ("Status", command.get("status")),
                    ("Reason", command.get("reason") or "n/a"),
                    ("Argv", " ".join(str(item) for item in command.get("argv", []))),
                    ("Exit", command.get("exit_code")),
                    ("Stdout", command.get("stdout_excerpt")),
                    ("Stderr", command.get("stderr_excerpt")),
                    ("Error", command.get("error_excerpt")),
                    ("Redacted env", ", ".join(str(item) for item in command.get("redacted_env_keys", []))),
                ]
            )
            + "</li>"
        )
    return f'<h3>Command history</h3><ul class="sandbox-command-list">{"".join(items)}</ul>'


def _sandbox_events(events: list[dict[str, Any]]) -> str:
    if not events:
        return "<h3>Sandbox lifecycle</h3>" + _empty_state("No sandbox lifecycle events recorded.")
    items = []
    for event in events:
        payload = _dict(event.get("payload"))
        items.append(
            "<li>"
            + _details(
                [
                    ("Transition", payload.get("transition")),
                    ("Status", payload.get("status") or "n/a"),
                    ("Reason", payload.get("reason") or "n/a"),
                    ("Recovery", payload.get("recovery_state") or "n/a"),
                    ("Created", event.get("created_at")),
                ]
            )
            + "</li>"
        )
    return f'<h3>Sandbox lifecycle</h3><ul class="sandbox-event-list">{"".join(items)}</ul>'


def _section(title: str, body: str) -> str:
    return f'<section class="panel"><h2>{escape(title)}</h2>{body}</section>'


def _details(items: list[tuple[str, Any]]) -> str:
    rows = [f"<dt>{escape(label)}</dt><dd>{escape(_text(value))}</dd>" for label, value in items]
    return f'<dl class="details">{"".join(rows)}</dl>'


def _empty_state(message: str) -> str:
    return f'<p class="empty-state">{escape(message)}</p>'


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_items(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value) if value is not None else ""
