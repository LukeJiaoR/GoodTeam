"""Concurrent Sub-agent execution pool, SSE event streaming, and completion callbacks."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import threading
from typing import Any, Callable

from goodteam.agents.adapters import AdapterContext, AdapterResult, build_adapter
from goodteam.agents.roles import seed_project_roles
from goodteam.agents.worktree import collect_result, create_worktree, is_git_repo
from goodteam.providers import ProviderStore
from goodteam.schema import new_id, now_iso
from goodteam.skills.injector import cleanup_staged_skills, stage_and_render
from goodteam.store import Store


class SubagentRunner:
    def __init__(
        self,
        store: Store,
        provider_store: ProviderStore,
        *,
        state_dir: str | Path | None = None,
        event_bus: Any = None,
        on_complete: Callable[[dict[str, Any]], None] | None = None,
        max_workers: int = 6,
    ) -> None:
        self.store = store
        self.provider_store = provider_store
        self.state_dir = state_dir
        self.event_bus = event_bus
        self.on_complete = on_complete
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="goodteam-worker")
        self._lock = threading.Lock()
        self._active: dict[str, tuple[Any, Future[None]]] = {}

    def shutdown(self) -> None:
        with self._lock:
            adapters = [item[0] for item in self._active.values()]
        for adapter in adapters:
            adapter.stop()
        self._pool.shutdown(wait=False)

    def _emit(self, project_id: str, event_type: str, data: dict[str, Any]) -> None:
        if self.event_bus is not None:
            self.event_bus.publish(project_id, event_type, data)

    def spawn(
        self,
        project_id: str,
        role_key: str,
        prompt: str,
        *,
        write_domains: list[str] | None = None,
        base_ref: str | None = None,
        task_id: str | None = None,
        parent_message_id: str | None = None,
        iteration_id: str | None = None,
        memory_context: str = "",
    ) -> dict[str, Any]:
        project = self.store.get_project(project_id)
        if not project:
            raise ValueError(f"Unknown project: {project_id}")
        seed_project_roles(self.store, project_id)
        role = self.store.get_role(project_id, role_key)
        if not role:
            raise ValueError(f"Unknown role: {role_key}")

        domains = (
            list(role["write_domains"]) if write_domains is None else list(write_domains)
        ) if role["execution_policy"] != "read_only" else []
        provider = self.provider_store.resolve_for_run(role["adapter"], role["provider_id"])
        model = role["model"] or (provider.get("model", "") if provider else "")
        run_id = new_id("run")

        branch_name, wt_path, resolved_base = "", "", base_ref or project["default_branch"]
        if is_git_repo(project["path"]):
            branch_name, wt_path_obj, resolved_base = create_worktree(
                project["path"], run_id, base_ref=resolved_base, state_dir=self.state_dir
            )
            wt_path = str(wt_path_obj)
        else:
            wt_path = project["path"]

        run = self.store.create_run(
            run_id=run_id,
            project_id=project_id,
            task_id=task_id,
            parent_message_id=parent_message_id,
            iteration_id=iteration_id,
            role_key=role_key,
            adapter=role["adapter"],
            provider_id=provider["id"] if provider else None,
            model=model,
            prompt=prompt,
            execution_policy=role["execution_policy"],
            write_domains=domains,
            base_ref=resolved_base,
            branch_name=branch_name,
            worktree_path=wt_path,
        )
        adapter = build_adapter(role["adapter"])
        future = self._pool.submit(
            self._execute, run_id, project, role, provider, model, domains, memory_context, adapter
        )
        with self._lock:
            self._active[run_id] = (adapter, future)
        return run

    def _execute(
        self,
        run_id: str,
        project: dict[str, Any],
        role: dict[str, Any],
        provider: dict[str, Any] | None,
        model: str,
        domains: list[str],
        memory_context: str,
        adapter: Any,
    ) -> None:
        current_start = self.store.get_run(run_id)
        if current_start is None or current_start["status"] == "stopped":
            with self._lock:
                self._active.pop(run_id, None)
            if current_start and self.on_complete is not None:
                self.on_complete(current_start)
            return

        run = self.store.update_run(run_id, status="running")
        assert run is not None
        self._emit(project["id"], "run_updated", run)
        seq = 0
        seq_lock = threading.Lock()

        def record_event(kind: str, payload: dict[str, Any]) -> None:
            nonlocal seq
            with seq_lock:
                seq += 1
                idx = seq
            self.store.append_run_event(run_id, idx, kind, payload)
            self._emit(project["id"], "run_event", {"run_id": run_id, "seq": idx, "kind": kind, "payload": payload})

        injection = None
        try:
            injection = stage_and_render(self.store, role, run_id, state_dir=self.state_dir)
            prompt_parts = [run["prompt"]]
            if domains:
                prompt_parts.append(f"## Write Scope Constraint\nModify ONLY files inside: {', '.join(domains)}")
            elif role["execution_policy"] == "read_only":
                prompt_parts.append("## Execution Policy\nREAD-ONLY: Do NOT modify or create repository files.")
            if memory_context:
                prompt_parts.append(f"## Relevant Project Memory\n{memory_context}")

            ctx = AdapterContext(
                run_id=run_id,
                role=role,
                prompt="\n\n".join(prompt_parts),
                worktree_path=Path(run["worktree_path"]),
                execution_policy=role["execution_policy"],
                provider=provider,
                model=model,
                reasoning_effort=role["reasoning_effort"],
                staged_skills_dir=injection.staged_dir,
                skill_prompt=injection.prompt_text,
            )
            result: AdapterResult = adapter.run(ctx, record_event)
            current = self.store.get_run(run_id)
            stopped = current is not None and current["status"] == "stopped"

            collected = {"changed_files": [], "diff_stat": {}, "scope_ok": True}
            if run["branch_name"] and Path(run["worktree_path"]).is_dir():
                collected = collect_result(
                    run["worktree_path"],
                    run["base_ref"],
                    write_domains=domains,
                    read_only=(role["execution_policy"] == "read_only"),
                )

            status = "stopped" if stopped else ("completed" if result.exit_code == 0 and not result.error else "failed")
            error = result.error
            if status == "completed" and not collected["scope_ok"]:
                status = "failed"
                error = "Sub-agent modified files outside its allowed scope."
            final_run = self.store.update_run(
                run_id,
                status=status,
                summary=result.summary,
                diff_stat=collected["diff_stat"],
                changed_files=collected["changed_files"],
                scope_ok=collected["scope_ok"],
                error=error,
                usage=result.usage,
                finished_at=now_iso(),
            )
            if result.usage and any(result.usage.values()):
                self.store.record_usage(
                    project_id=project["id"],
                    run_id=run_id,
                    provider_id=provider["id"] if provider else None,
                    adapter=role["adapter"],
                    model=model,
                    input_tokens=result.usage.get("input_tokens", 0),
                    cached_input_tokens=result.usage.get("cached_input_tokens", 0),
                    output_tokens=result.usage.get("output_tokens", 0),
                )
            if (
                final_run
                and status == "completed"
                and collected["scope_ok"]
                and collected["changed_files"]
                and not run.get("iteration_id")
                and run["branch_name"]
            ):
                deliv = self.store.create_delivery(
                    project_id=project["id"],
                    run_id=run_id,
                    branch_name=run["branch_name"],
                    base_ref=run["base_ref"],
                )
                self._emit(project["id"], "delivery_created", deliv)
            if final_run:
                self._emit(project["id"], "run_updated", final_run)
                if self.on_complete is not None:
                    self.on_complete(final_run)
        except Exception as exc:
            err_run = self.store.update_run(run_id, status="failed", error=str(exc)[:400], finished_at=now_iso())
            if err_run:
                self._emit(project["id"], "run_updated", err_run)
                if self.on_complete is not None:
                    self.on_complete(err_run)
        finally:
            if injection is not None:
                cleanup_staged_skills(injection.staged_dir)
            with self._lock:
                self._active.pop(run_id, None)

    def stop(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._active.get(run_id)
        if entry:
            entry[0].stop()
        current = self.store.get_run(run_id)
        if not current:
            return None
        if current["status"] not in {"queued", "running"}:
            return current
        run = self.store.update_run(run_id, status="stopped", finished_at=now_iso())
        if run:
            self._emit(run["project_id"], "run_updated", run)
        return run

    def wait(self, run_id: str, timeout: float | None = 15.0) -> dict[str, Any] | None:
        with self._lock:
            entry = self._active.get(run_id)
        if entry:
            entry[1].result(timeout=timeout)
        return self.store.get_run(run_id)
