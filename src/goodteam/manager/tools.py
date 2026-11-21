"""Dynamic tools exposed to the Manager Codex app-server session."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from goodteam.agents.roles import seed_project_roles
from goodteam.agents.worktree import WorktreeError, create_iteration_branch, merge_branch
from goodteam.integrations import github as gh_client
from goodteam.integrations import memory as mem_store
from goodteam.skills.injector import SkillInjection, stage_and_render
from goodteam.skills.installer import ensure_builtins, read_skill_resource
from goodteam.skills.models import SkillError
from goodteam.store import Store


DEFAULT_MANAGER_SETTINGS: dict[str, Any] = {
    "model": "",
    "reasoning_effort": "high",
    "additional_prompt": "",
    "provider_id": None,
    "skill_bindings": [{"skill_id": "builtin:project-memory", "mode": "required"}],
}


def get_manager_settings(project: dict[str, Any]) -> dict[str, Any]:
    raw = (project.get("settings") or {}).get("manager") or {}
    return {
        "model": str(raw.get("model") or ""),
        "reasoning_effort": str(raw.get("reasoning_effort") or "high"),
        "additional_prompt": str(raw.get("additional_prompt") or ""),
        "provider_id": raw.get("provider_id") or None,
        "skill_bindings": list(raw.get("skill_bindings") if raw.get("skill_bindings") is not None
                               else DEFAULT_MANAGER_SETTINGS["skill_bindings"]),
    }


def manager_skill_injection(
    store: Store,
    project: dict[str, Any],
    *,
    state_dir: str | Path | None = None,
) -> SkillInjection:
    ensure_builtins(store)
    settings = get_manager_settings(project)
    synthetic = {
        "key": "manager",
        "name": "Manager",
        "skill_bindings": settings["skill_bindings"],
    }
    return stage_and_render(store, synthetic, f"manager-{project['id']}", state_dir=state_dir)


def _spec(name: str, description: str, properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        },
    }


class ManagerToolset:
    def __init__(
        self,
        store: Store,
        runner: Any,
        project_id: str,
        *,
        state_dir: str | Path | None = None,
        parent_message_id: str | None = None,
        base_ref_override: str | None = None,
        iteration_id: str | None = None,
    ) -> None:
        self.store = store
        self.runner = runner
        self.project_id = project_id
        self.state_dir = state_dir
        self.parent_message_id = parent_message_id
        self.base_ref_override = base_ref_override
        self.iteration_id = iteration_id
        self.spawned_run_ids: list[str] = []
        self._injection: SkillInjection | None = None

    def instructions(self) -> str:
        project = self.store.get_project(self.project_id)
        if not project:
            raise ValueError(f"Unknown project: {self.project_id}")
        seed_project_roles(self.store, self.project_id)
        roles = self.store.list_roles(self.project_id)
        role_lines = [
            f"- `{r['key']}` ({r['name']}): policy={r['execution_policy']}, adapter={r['adapter']}, "
            f"write_domains={r['write_domains'] or ['*'] if r['execution_policy']=='workspace_write' else 'none'}, "
            f"skills={[b['skill_id'] for b in r['skill_bindings']]} — {r['description']}"
            for r in roles
        ]
        self._injection = manager_skill_injection(self.store, project, state_dir=self.state_dir)
        settings = get_manager_settings(project)
        parts = [
            "You are GoodTeam's Manager orchestrating a team of specialized Sub-agents.",
            "Reply concisely in the user's language. Discuss requirements, inspect backlog/memory, and delegate concrete implementation/research/review work using `spawn_agent` rather than doing coding work yourself.",
            "Sub-agents automatically run in isolated git worktrees. Humans review diffs and explicitly trigger merges to main or PRs; never claim code is merged to main unless a delivery shows merged.",
            "### Available Project Roles\n" + "\n".join(role_lines),
        ]
        if self.iteration_id:
            parts.append(
                f"### Active Autonomous Iteration\n"
                f"Iteration ID: `{self.iteration_id}`, Integration branch: `{self.base_ref_override}`.\n"
                "Spawned sub-agents branch from this integration branch. After inspecting completed runs, call `integrate_run` "
                "to merge verified work into this integration branch, update tasks/memory, and continue."
            )
        if self._injection.prompt_text:
            parts.append(self._injection.prompt_text)
        if settings["additional_prompt"]:
            parts.append("### Project Manager Instructions\n" + settings["additional_prompt"])
        return "\n\n".join(parts)

    def specs(self) -> list[dict[str, Any]]:
        s = {"type": "string"}
        return [
            _spec("spawn_agent", "Launch a scoped Sub-agent immediately in an isolated git worktree.", {
                "role": s,
                "task": s,
                "write_domains": {"type": "array", "items": s},
                "base_ref": s,
                "task_id": s,
                "attach_memory": {"type": "boolean"},
            }, ("role", "task")),
            _spec("list_runs", "List recent Sub-agent runs for this project.", {"limit": {"type": "integer"}}),
            _spec("get_run", "Inspect detailed status, diff stats, summary, and recent events of a Sub-agent run.", {"run_id": s}, ("run_id",)),
            _spec("stop_run", "Stop a running Sub-agent.", {"run_id": s}, ("run_id",)),
            _spec("manage_tasks", "List, create, or update Backlog tasks.", {
                "action": {"type": "string", "enum": ["list", "create", "update"]},
                "task_id": s, "title": s, "description": s,
                "status": {"type": "string", "enum": ["todo", "in_progress", "review", "done", "blocked"]},
                "assigned_role": s, "priority": {"type": "integer"},
            }, ("action",)),
            _spec("read_memory", "List, read, or search project Markdown memory under .goodteam/memory/.", {
                "filename": s, "query": s,
            }),
            _spec("write_memory", "Persist project architecture, decisions, or lessons to .goodteam/memory/*.md.", {
                "filename": s, "content": s,
                "mode": {"type": "string", "enum": ["append", "replace"]},
            }, ("filename", "content")),
            _spec("github_issues", "List or inspect GitHub issues for this repository via gh CLI.", {
                "action": {"type": "string", "enum": ["list", "get"]},
                "number": {"type": "integer"},
                "state": {"type": "string", "enum": ["open", "closed", "all"]},
                "limit": {"type": "integer"},
            }, ("action",)),
            _spec("skill_resource", "Read a resource file from a bound Manager Skill package.", {
                "skill_id": s, "path": s,
            }, ("skill_id", "path")),
            _spec("integrate_run", "Merge a completed, scope-verified run branch into the active iteration branch (never main).", {
                "run_id": s, "iteration_id": s,
            }, ("run_id", "iteration_id")),
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        project = self.store.get_project(self.project_id)
        if not project:
            raise ValueError(f"Project {self.project_id} not found.")
        if name == "spawn_agent":
            return self._spawn(project, arguments)
        if name == "list_runs":
            limit = max(1, min(int(arguments.get("limit") or 20), 100))
            return {"runs": self.store.list_runs(self.project_id, limit=limit)}
        if name == "get_run":
            run = self.store.get_run(str(arguments["run_id"]))
            if not run or run["project_id"] != self.project_id:
                raise ValueError("Run not found in this project.")
            events = self.store.list_run_events(run["id"], limit=50)[-15:]
            return {"run": run, "recent_events": events}
        if name == "stop_run":
            run = self.store.get_run(str(arguments["run_id"]))
            if not run or run["project_id"] != self.project_id:
                raise ValueError("Run not found in this project.")
            return {"run": self.runner.stop(run["id"])}
        if name == "manage_tasks":
            return self._tasks(arguments)
        if name == "read_memory":
            return self._read_memory(project, arguments)
        if name == "write_memory":
            return mem_store.write_file(
                project["path"],
                str(arguments["filename"]),
                str(arguments["content"]),
                mode=str(arguments.get("mode") or "append"),
            )
        if name == "github_issues":
            return self._github_issues(project, arguments)
        if name == "skill_resource":
            sid = str(arguments["skill_id"])
            rec = self.store.get_skill(sid)
            if not rec:
                raise SkillError("skill_not_found", f"Unknown skill: {sid}", 404)
            return {"skill_id": sid, "path": str(arguments["path"]), "content": read_skill_resource(rec, str(arguments["path"]))}
        if name == "integrate_run":
            return self._integrate(project, arguments)
        raise ValueError(f"Unknown tool: {name}")

    def _spawn(self, project: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        attach_mem = args.get("attach_memory", True)
        mem_ctx = mem_store.summary_for_prompt(project["path"]) if attach_mem else ""
        base_ref = args.get("base_ref") or self.base_ref_override
        run = self.runner.spawn(
            self.project_id,
            str(args["role"]),
            str(args["task"]),
            write_domains=args.get("write_domains"),
            base_ref=base_ref,
            task_id=args.get("task_id"),
            parent_message_id=self.parent_message_id,
            iteration_id=self.iteration_id,
            memory_context=mem_ctx,
        )
        self.spawned_run_ids.append(run["id"])
        return {
            "run_id": run["id"],
            "role_key": run["role_key"],
            "adapter": run["adapter"],
            "branch_name": run["branch_name"],
            "base_ref": run["base_ref"],
            "status": run["status"],
        }

    def _tasks(self, args: dict[str, Any]) -> dict[str, Any]:
        action = args["action"]
        bus = getattr(self.runner, "event_bus", None)
        if action == "list":
            return {"tasks": self.store.list_tasks(self.project_id)}
        if action == "create":
            task = self.store.create_task(
                project_id=self.project_id,
                title=str(args.get("title") or "").strip()[:200],
                description=str(args.get("description") or ""),
                status=str(args.get("status") or "todo"),
                assigned_role=str(args.get("assigned_role") or ""),
                priority=int(args.get("priority") or 0),
            )
            if bus is not None:
                bus.publish(self.project_id, "task_updated", task)
            return {"task": task}
        if action == "update":
            tid = str(args.get("task_id") or "")
            existing = self.store.get_task(tid)
            if not existing or existing["project_id"] != self.project_id:
                raise ValueError("Task not found in this project.")
            fields = {k: args[k] for k in ("title", "description", "status", "assigned_role", "priority") if k in args}
            updated = self.store.update_task(tid, **fields)
            if updated and bus is not None:
                bus.publish(self.project_id, "task_updated", updated)
            return {"task": updated}
        raise ValueError(f"Unknown task action: {action}")

    def _read_memory(self, project: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        if args.get("query"):
            return {"hits": mem_store.search(project["path"], str(args["query"]))}
        if args.get("filename"):
            name = str(args["filename"])
            return {"filename": name, "content": mem_store.read_file(project["path"], name)}
        return {"files": mem_store.list_files(project["path"])}

    def _github_issues(self, project: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        action = args["action"]
        if action == "list":
            return {"issues": gh_client.list_issues(
                project["path"], state=str(args.get("state") or "open"), limit=int(args.get("limit") or 20)
            )}
        if action == "get":
            return {"issue": gh_client.get_issue(project["path"], int(args["number"]))}
        raise ValueError(f"Unknown github_issues action: {action}")

    def _integrate(self, project: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        run = self.store.get_run(str(args["run_id"]))
        if not run or run["project_id"] != self.project_id:
            raise ValueError("Run not found in this project.")
        if run["status"] != "completed" or not run["scope_ok"]:
            raise WorktreeError("Only completed runs within allowed scope can be integrated.")
        if not run["branch_name"]:
            raise WorktreeError("Run has no branch to integrate.")
        iter_id = str(args["iteration_id"])
        iter_branch = create_iteration_branch(project["path"], iter_id, base_ref=project["default_branch"])
        commit = merge_branch(project["path"], run["branch_name"], iter_branch, message=f"Integrate {run['id']} ({run['role_key']})")
        return {"iteration_branch": iter_branch, "merge_commit": commit, "run_id": run["id"]}
