"""SQLite persistence layer for GoodTeam v2."""
from __future__ import annotations

from pathlib import Path
import sqlite3
import threading
from typing import Any

from goodteam.config import db_path
from goodteam.schema import SCHEMA_SQL, dumps, loads, new_id, now_iso


def _row_to_project(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "name": row["name"], "path": row["path"],
        "default_branch": row["default_branch"],
        "autopilot_enabled": bool(row["autopilot_enabled"]),
        "autopilot_max_rounds": row["autopilot_max_rounds"],
        "settings": loads(row["settings_json"], {}),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def _row_to_role(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "project_id": row["project_id"], "key": row["key"],
        "name": row["name"], "description": row["description"],
        "system_prompt": row["system_prompt"],
        "execution_policy": row["execution_policy"],
        "write_domains": loads(row["write_domains_json"], []),
        "allowed_tools": loads(row["allowed_tools_json"], []),
        "adapter": row["adapter"], "provider_id": row["provider_id"],
        "model": row["model"], "reasoning_effort": row["reasoning_effort"],
        "skill_bindings": loads(row["skill_bindings_json"], []),
        "is_builtin": bool(row["is_builtin"]), "updated_at": row["updated_at"],
    }


def _row_to_run(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "project_id": row["project_id"], "task_id": row["task_id"],
        "parent_message_id": row["parent_message_id"], "iteration_id": row["iteration_id"],
        "role_key": row["role_key"], "adapter": row["adapter"],
        "provider_id": row["provider_id"], "model": row["model"], "prompt": row["prompt"],
        "execution_policy": row["execution_policy"],
        "write_domains": loads(row["write_domains_json"], []),
        "base_ref": row["base_ref"], "branch_name": row["branch_name"],
        "worktree_path": row["worktree_path"], "status": row["status"],
        "summary": row["summary"], "diff_stat": loads(row["diff_stat_json"], {}),
        "changed_files": loads(row["changed_files_json"], []),
        "scope_ok": bool(row["scope_ok"]), "error": row["error"],
        "usage": loads(row["usage_json"], {}),
        "started_at": row["started_at"], "finished_at": row["finished_at"],
    }


class Store:
    def __init__(self, state_dir: str | Path | None = None) -> None:
        self.path = db_path(state_dir)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA_SQL)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- Projects ----
    def create_project(self, *, name: str, path: str, default_branch: str = "main",
                       settings: dict[str, Any] | None = None) -> dict[str, Any]:
        ts = now_iso()
        pid = new_id("proj")
        resolved = str(Path(path).expanduser().resolve())
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO projects (id, name, path, default_branch, settings_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (pid, name, resolved, default_branch, dumps(settings or {}), ts, ts),
            )
        return self.get_project(pid)  # type: ignore[return-value]

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return _row_to_project(row) if row else None

    def get_project_by_path(self, path: str) -> dict[str, Any] | None:
        resolved = str(Path(path).expanduser().resolve())
        with self._lock:
            row = self._conn.execute("SELECT * FROM projects WHERE path = ?", (resolved,)).fetchone()
        return _row_to_project(row) if row else None

    def list_projects(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
        return [_row_to_project(r) for r in rows]

    def update_project(self, project_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {"name", "default_branch", "autopilot_enabled", "autopilot_max_rounds", "settings"}
        sets, params = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "settings":
                sets.append("settings_json = ?")
                params.append(dumps(v))
            elif k == "autopilot_enabled":
                sets.append("autopilot_enabled = ?")
                params.append(1 if v else 0)
            else:
                sets.append(f"{k} = ?")
                params.append(v)
        if not sets:
            return self.get_project(project_id)
        sets.append("updated_at = ?")
        params.extend([now_iso(), project_id])
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", params)
        return self.get_project(project_id)

    def delete_project(self, project_id: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return cur.rowcount > 0

    # ---- Roles ----
    def upsert_role(self, *, project_id: str | None, key: str, name: str, description: str = "",
                    system_prompt: str = "", execution_policy: str = "read_only",
                    write_domains: list[str] | None = None, allowed_tools: list[str] | None = None,
                    adapter: str = "codex", provider_id: str | None = None, model: str = "",
                    reasoning_effort: str = "high", skill_bindings: list[dict[str, Any]] | None = None,
                    is_builtin: bool = False) -> dict[str, Any]:
        ts = now_iso()
        with self._lock, self._conn:
            existing = self.get_role(project_id, key)
            rid = existing["id"] if existing else new_id("role")
            if existing:
                self._conn.execute(
                    """UPDATE roles SET name=?, description=?, system_prompt=?, execution_policy=?,
                       write_domains_json=?, allowed_tools_json=?, adapter=?, provider_id=?,
                       model=?, reasoning_effort=?, skill_bindings_json=?, updated_at=? WHERE id=?""",
                    (name, description, system_prompt, execution_policy,
                     dumps(write_domains or []), dumps(allowed_tools or []),
                     adapter, provider_id, model, reasoning_effort,
                     dumps(skill_bindings or []), ts, rid),
                )
            else:
                self._conn.execute(
                    """INSERT INTO roles (id, project_id, key, name, description, system_prompt,
                       execution_policy, write_domains_json, allowed_tools_json, adapter,
                       provider_id, model, reasoning_effort, skill_bindings_json, is_builtin, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (rid, project_id, key, name, description, system_prompt, execution_policy,
                     dumps(write_domains or []), dumps(allowed_tools or []),
                     adapter, provider_id, model, reasoning_effort,
                     dumps(skill_bindings or []), 1 if is_builtin else 0, ts),
                )
        return self.get_role(project_id, key)  # type: ignore[return-value]

    def get_role(self, project_id: str | None, key: str) -> dict[str, Any] | None:
        with self._lock:
            if project_id is None:
                row = self._conn.execute(
                    "SELECT * FROM roles WHERE project_id IS NULL AND key = ?", (key,)
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM roles WHERE project_id = ? AND key = ?", (project_id, key)
                ).fetchone()
        return _row_to_role(row) if row else None

    def list_roles(self, project_id: str | None) -> list[dict[str, Any]]:
        with self._lock:
            if project_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM roles WHERE project_id IS NULL ORDER BY is_builtin DESC, name"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM roles WHERE project_id = ? ORDER BY is_builtin DESC, name", (project_id,)
                ).fetchall()
        return [_row_to_role(r) for r in rows]

    def delete_role(self, project_id: str | None, key: str) -> bool:
        with self._lock, self._conn:
            if project_id is None:
                cur = self._conn.execute("DELETE FROM roles WHERE project_id IS NULL AND key = ?", (key,))
            else:
                cur = self._conn.execute("DELETE FROM roles WHERE project_id = ? AND key = ?", (project_id, key))
        return cur.rowcount > 0

    # ---- Skills ----
    def upsert_skill(self, *, skill_id: str, name: str, description: str, source_type: str,
                     source_ref: str, commit_sha: str, path: str, enabled: bool = True) -> dict[str, Any]:
        ts = now_iso()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO skills (id, name, description, source_type, source_ref, commit_sha, path, enabled, installed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description,
                   source_type=excluded.source_type, source_ref=excluded.source_ref,
                   commit_sha=excluded.commit_sha, path=excluded.path, enabled=excluded.enabled""",
                (skill_id, name, description, source_type, source_ref, commit_sha, path, 1 if enabled else 0, ts),
            )
        return self.get_skill(skill_id)  # type: ignore[return-value]

    def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        return self._row_to_skill(row) if row else None

    def list_skills(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM skills ORDER BY source_type, name").fetchall()
        return [self._row_to_skill(r) for r in rows]

    def delete_skill(self, skill_id: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
        return cur.rowcount > 0

    @staticmethod
    def _row_to_skill(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "name": row["name"], "description": row["description"],
            "source_type": row["source_type"], "source_ref": row["source_ref"],
            "commit_sha": row["commit_sha"], "path": row["path"],
            "enabled": bool(row["enabled"]), "installed_at": row["installed_at"],
        }

    # ---- Messages ----
    def add_message(self, *, project_id: str, role: str, content: str,
                    attachments: list[dict[str, Any]] | None = None,
                    meta: dict[str, Any] | None = None, message_id: str | None = None) -> dict[str, Any]:
        mid = message_id or new_id("msg")
        ts = now_iso()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO messages (id, project_id, role, content, attachments_json, meta_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (mid, project_id, role, content, dumps(attachments or []), dumps(meta or {}), ts),
            )
        return {"id": mid, "project_id": project_id, "role": role, "content": content,
                "attachments": attachments or [], "meta": meta or {}, "created_at": ts}

    def update_message(self, message_id: str, *, content: str | None = None,
                       meta: dict[str, Any] | None = None) -> None:
        sets, params = [], []
        if content is not None:
            sets.append("content = ?")
            params.append(content)
        if meta is not None:
            sets.append("meta_json = ?")
            params.append(dumps(meta))
        if not sets:
            return
        params.append(message_id)
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE messages SET {', '.join(sets)} WHERE id = ?", params)

    def list_messages(self, project_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE project_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        rows.reverse()
        return [{
            "id": r["id"], "project_id": r["project_id"], "role": r["role"], "content": r["content"],
            "attachments": loads(r["attachments_json"], []), "meta": loads(r["meta_json"], {}),
            "created_at": r["created_at"],
        } for r in rows]

    # ---- Tasks ----
    def create_task(self, *, project_id: str, title: str, description: str = "",
                    status: str = "todo", assigned_role: str = "", priority: int = 0,
                    source_ref: str = "", parent_run_id: str = "") -> dict[str, Any]:
        tid = new_id("task")
        ts = now_iso()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO tasks (id, project_id, title, description, status, assigned_role,
                   priority, source_ref, parent_run_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (tid, project_id, title, description, status, assigned_role, priority,
                 source_ref, parent_run_id, ts, ts),
            )
        return self.get_task(tid)  # type: ignore[return-value]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def list_tasks(self, project_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE project_id = ? ORDER BY priority DESC, created_at ASC",
                (project_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_task(self, task_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {"title", "description", "status", "assigned_role", "priority", "parent_run_id"}
        sets = [f"{k} = ?" for k in fields if k in allowed]
        params = [fields[k] for k in fields if k in allowed]
        if not sets:
            return self.get_task(task_id)
        sets.append("updated_at = ?")
        params.extend([now_iso(), task_id])
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)
        return self.get_task(task_id)

    # ---- Runs & Events ----
    def create_run(self, *, project_id: str, role_key: str, adapter: str, prompt: str,
                   execution_policy: str = "read_only", write_domains: list[str] | None = None,
                   provider_id: str | None = None, model: str = "", base_ref: str = "",
                   branch_name: str = "", worktree_path: str = "", task_id: str | None = None,
                   parent_message_id: str | None = None, iteration_id: str | None = None,
                   run_id: str | None = None) -> dict[str, Any]:
        rid = run_id or new_id("run")
        ts = now_iso()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO runs (id, project_id, task_id, parent_message_id, iteration_id,
                   role_key, adapter, provider_id, model, prompt, execution_policy, write_domains_json,
                   base_ref, branch_name, worktree_path, status, started_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)""",
                (rid, project_id, task_id, parent_message_id, iteration_id, role_key, adapter,
                 provider_id, model, prompt, execution_policy, dumps(write_domains or []),
                 base_ref, branch_name, worktree_path, ts),
            )
        return self.get_run(rid)  # type: ignore[return-value]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _row_to_run(row) if row else None

    def list_runs(self, project_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM runs WHERE project_id = ? ORDER BY started_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        return [_row_to_run(r) for r in rows]

    def update_run(self, run_id: str, **fields: Any) -> dict[str, Any] | None:
        json_map = {"diff_stat": "diff_stat_json", "changed_files": "changed_files_json", "usage": "usage_json"}
        plain = {"status", "summary", "error", "branch_name", "worktree_path", "base_ref", "finished_at"}
        sets, params = [], []
        for k, v in fields.items():
            if k in json_map:
                sets.append(f"{json_map[k]} = ?")
                params.append(dumps(v))
            elif k == "scope_ok":
                sets.append("scope_ok = ?")
                params.append(1 if v else 0)
            elif k in plain:
                sets.append(f"{k} = ?")
                params.append(v)
        if not sets:
            return self.get_run(run_id)
        params.append(run_id)
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id = ?", params)
        return self.get_run(run_id)

    def append_run_event(self, run_id: str, seq: int, kind: str, payload: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO run_events (run_id, seq, kind, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, seq, kind, dumps(payload), now_iso()),
            )

    def list_run_events(self, run_id: str, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, kind, payload_json, created_at FROM run_events WHERE run_id = ? ORDER BY seq ASC LIMIT ?",
                (run_id, limit),
            ).fetchall()
        return [{"seq": r["seq"], "kind": r["kind"], "payload": loads(r["payload_json"], {}),
                 "created_at": r["created_at"]} for r in rows]

    # ---- Deliveries & Usage & KV ----
    def create_delivery(self, *, project_id: str, branch_name: str, base_ref: str,
                        run_id: str | None = None, iteration_id: str | None = None) -> dict[str, Any]:
        did = new_id("deliv")
        ts = now_iso()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO deliveries (id, project_id, run_id, iteration_id, branch_name, base_ref,
                   status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (did, project_id, run_id, iteration_id, branch_name, base_ref, ts, ts),
            )
        return self.get_delivery(did)  # type: ignore[return-value]

    def get_delivery(self, delivery_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM deliveries WHERE id = ?", (delivery_id,)).fetchone()
        return dict(row) if row else None

    def list_deliveries(self, project_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM deliveries WHERE project_id = ? ORDER BY created_at DESC", (project_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def update_delivery(self, delivery_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {"status", "pr_url", "merge_commit"}
        sets = [f"{k} = ?" for k in fields if k in allowed]
        params = [fields[k] for k in fields if k in allowed]
        if not sets:
            return self.get_delivery(delivery_id)
        sets.append("updated_at = ?")
        params.extend([now_iso(), delivery_id])
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE deliveries SET {', '.join(sets)} WHERE id = ?", params)
        return self.get_delivery(delivery_id)

    def record_usage(self, *, project_id: str, run_id: str | None, provider_id: str | None,
                     adapter: str, model: str, input_tokens: int = 0,
                     cached_input_tokens: int = 0, output_tokens: int = 0) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO usage_logs (id, project_id, run_id, provider_id, adapter, model,
                   input_tokens, cached_input_tokens, output_tokens, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (new_id("use"), project_id, run_id, provider_id, adapter, model,
                 int(input_tokens), int(cached_input_tokens), int(output_tokens), now_iso()),
            )

    def usage_summary(self, project_id: str | None = None) -> list[dict[str, Any]]:
        where = "WHERE project_id = ?" if project_id else ""
        params = (project_id,) if project_id else ()
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT adapter, model, COALESCE(provider_id, 'native') AS provider_id,
                    COUNT(*) AS calls, SUM(input_tokens) AS input_tokens,
                    SUM(cached_input_tokens) AS cached_input_tokens, SUM(output_tokens) AS output_tokens
                    FROM usage_logs {where} GROUP BY adapter, model, provider_id""",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def get_kv(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute("SELECT value_json FROM kv WHERE key = ?", (key,)).fetchone()
        return loads(row["value_json"], default) if row else default

    def set_kv(self, key: str, value: Any) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO kv (key, value_json, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
                (key, dumps(value), now_iso()),
            )
