"""Bounded multi-round autonomous iteration controller (goodteam/iter-* branch)."""
from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Any

from goodteam.agents.worktree import branch_diff, create_iteration_branch, is_git_repo
from goodteam.integrations import memory as mem_store
from goodteam.schema import new_id
from goodteam.store import Store


@dataclass
class _AutopilotState:
    project_id: str
    iteration_id: str
    branch_name: str
    objective: str
    max_rounds: int
    current_round: int = 0
    status: str = "running"
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "running": self.status == "running",
            "iteration_id": self.iteration_id,
            "branch_name": self.branch_name,
            "objective": self.objective,
            "round": self.current_round,
            "max_rounds": self.max_rounds,
            "status": self.status,
        }


class AutopilotController:
    def __init__(self, store: Store, runner: Any, manager_hub: Any, *, event_bus: Any = None) -> None:
        self.store = store
        self.runner = runner
        self.manager_hub = manager_hub
        self.event_bus = event_bus
        self._lock = threading.Lock()
        self._states: dict[str, _AutopilotState] = {}

    def _emit(self, project_id: str, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_bus is not None:
            self.event_bus.publish(project_id, event_type, payload)

    def status(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            st = self._states.get(project_id)
            if st is not None:
                return st.to_dict()
        proj = self.store.get_project(project_id)
        return {
            "project_id": project_id,
            "running": False,
            "iteration_id": "",
            "branch_name": "",
            "objective": "",
            "round": 0,
            "max_rounds": proj["autopilot_max_rounds"] if proj else 5,
            "status": "idle",
        }

    def start(self, project_id: str, objective: str, *, max_rounds: int = 5) -> dict[str, Any]:
        project = self.store.get_project(project_id)
        if not project:
            raise ValueError(f"Unknown project: {project_id}")
        rounds = max(1, min(int(max_rounds), 20))
        with self._lock:
            existing = self._states.get(project_id)
            if existing and existing.status in {"running", "stopping"}:
                raise ValueError("Autopilot is already running for this project.")
            iter_id = new_id("iter").removeprefix("iter-")
            branch_name = ""
            if is_git_repo(project["path"]):
                branch_name = create_iteration_branch(project["path"], iter_id, base_ref=project["default_branch"])
            st = _AutopilotState(
                project_id=project_id,
                iteration_id=iter_id,
                branch_name=branch_name,
                objective=objective.strip(),
                max_rounds=rounds,
            )
            self._states[project_id] = st

        self.store.update_project(project_id, autopilot_enabled=True, autopilot_max_rounds=rounds)
        self._emit(project_id, "autopilot_updated", st.to_dict())
        thread = threading.Thread(target=self._loop, args=(st, project), daemon=True, name=f"goodteam-auto-{project_id}")
        st.thread = thread
        thread.start()
        return st.to_dict()

    def stop(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            st = self._states.get(project_id)
        if st and st.status == "running":
            st.stop_event.set()
            st.status = "stopping"
            self.manager_hub.session(project_id).stop()
            for run in self.store.list_runs(project_id, limit=50):
                if run.get("iteration_id") == st.iteration_id and run["status"] in {"queued", "running"}:
                    self.runner.stop(run["id"])
            self._emit(project_id, "autopilot_updated", st.to_dict())
        return self.status(project_id)

    def wait(self, project_id: str, timeout: float | None = 10.0) -> dict[str, Any]:
        with self._lock:
            st = self._states.get(project_id)
            thread = st.thread if st else None
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        return self.status(project_id)

    def _loop(self, st: _AutopilotState, project: dict[str, Any]) -> None:
        session = self.manager_hub.session(st.project_id)
        final_status = "running"
        try:
            for r in range(1, st.max_rounds + 1):
                if st.stop_event.is_set():
                    final_status = "stopped"
                    break
                st.current_round = r
                self._emit(st.project_id, "autopilot_updated", st.to_dict())
                tasks = self.store.list_tasks(st.project_id)
                open_tasks = [t for t in tasks if t["status"] != "done"][:20]
                mem_ctx = mem_store.summary_for_prompt(project["path"], max_chars=2500)
                round_prompt = (
                    f"[Autopilot Round {r}/{st.max_rounds}]\n"
                    f"Objective: {st.objective}\n"
                    f"Open Backlog Tasks: {open_tasks}\n"
                    f"Memory Summary:\n{mem_ctx or '(none)'}\n\n"
                    "Spawn appropriate Sub-agent(s) if work remains, or include `[AUTOPILOT_DONE]` if all objective work is complete, "
                    "or `[NEEDS_HUMAN]` if blocked on a human decision."
                )
                res = session.send(
                    round_prompt,
                    actor="system",
                    base_ref_override=st.branch_name or None,
                    iteration_id=st.iteration_id,
                    sync=True,
                )
                reply_text = str((res.get("assistant_message") or {}).get("content") or "")
                spawned = list(res.get("spawned_run_ids") or [])
                if spawned and not st.stop_event.is_set():
                    finished = [self.runner.wait(rid, timeout=600) for rid in spawned]
                    summary_lines = [
                        f"- {item['id']} ({item['role_key']}): status={item['status']}, scope_ok={item['scope_ok']}, "
                        f"files={item['changed_files']}, summary={item['summary'][:400]}"
                        for item in finished if item
                    ]
                    if not st.stop_event.is_set():
                        review_res = session.send(
                            f"[Autopilot Round {r} Results]\n" + "\n".join(summary_lines) +
                            "\n\nInspect results, call `integrate_run` for verified write runs onto the iteration branch, "
                            "update tasks/memory, and state `[AUTOPILOT_DONE]` if finished or `[NEEDS_HUMAN]` if blocked.",
                            actor="system",
                            base_ref_override=st.branch_name or None,
                            iteration_id=st.iteration_id,
                            sync=True,
                        )
                        reply_text = str((review_res.get("assistant_message") or {}).get("content") or "")
                if st.stop_event.is_set():
                    final_status = "stopped"
                    break
                if "[AUTOPILOT_DONE]" in reply_text:
                    final_status = "completed"
                    break
                if "[NEEDS_HUMAN]" in reply_text:
                    final_status = "needs_human"
                    break
            else:
                final_status = "stopped" if st.stop_event.is_set() else "max_rounds_reached"
        except Exception:
            final_status = "failed"
        finally:
            if st.stop_event.is_set() and final_status in {"running", "stopping"}:
                final_status = "stopped"
            try:
                if st.branch_name and is_git_repo(project["path"]):
                    diff = branch_diff(project["path"], project["default_branch"], st.branch_name)
                    if diff["changed_files"]:
                        deliv = self.store.create_delivery(
                            project_id=st.project_id,
                            iteration_id=st.iteration_id,
                            branch_name=st.branch_name,
                            base_ref=project["default_branch"],
                        )
                        self._emit(st.project_id, "delivery_created", deliv)
                self.store.update_project(st.project_id, autopilot_enabled=False)
            except Exception:
                pass
            with self._lock:
                st.status = final_status
            self._emit(st.project_id, "autopilot_updated", st.to_dict())
