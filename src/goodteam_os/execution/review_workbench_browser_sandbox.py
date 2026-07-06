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
    body += _sandbox_conflicts(_dict_items(sandbox.get("conflict_risks")))
    body += _sandbox_commands(_dict_items(sandbox.get("commands")))
    return _section("Sandbox evidence", body)


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
                    ("Redacted env", ", ".join(str(item) for item in command.get("redacted_env_keys", []))),
                ]
            )
            + "</li>"
        )
    return f'<h3>Command history</h3><ul class="sandbox-command-list">{"".join(items)}</ul>'


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
