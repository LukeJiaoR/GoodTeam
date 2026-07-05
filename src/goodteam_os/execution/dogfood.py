from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from goodteam_os.execution.github import GitHubBinding, GitHubIssueNotFound, GitHubIssueSnapshot
from goodteam_os.execution.idempotency import execution_id_from_task, task_id_from_github_issue
from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.models import ExecutionRunStatus
from goodteam_os.execution.orchestrator import ExecutionOrchestrator


P0_1_DOGFOOD_REPO = "LukeJiaoR/GoodTeam"
P0_1_DOGFOOD_ISSUE_NUMBER = 225
P0_1_DOGFOOD_TITLE = "P0-1F: First Runnable Dogfood Spec and Regression Tests"
P0_1_DOGFOOD_URL = "https://github.com/LukeJiaoR/GoodTeam/issues/225"


@dataclass(frozen=True)
class DogfoodResult:
    project_root: Path
    repo: str
    issue_number: int
    adapter: str
    task_id: str
    execution_id: str
    worker_job_id: str
    run_status: str
    execution_dir: Path
    task_path: Path
    run_path: Path
    events_path: Path
    replay: dict[str, Any]

    @property
    def event_transitions(self) -> list[str]:
        return [
            str(event.payload.get("transition", event.event_type.value))
            for event in self.replay["events"]
        ]

    def to_cli_text(self) -> str:
        lines = [
            "P0-1 dogfood first run",
            f"repo: {self.repo}",
            f"issue: #{self.issue_number}",
            f"adapter: {self.adapter}",
            f"task_id: {self.task_id}",
            f"execution_id: {self.execution_id}",
            f"worker_job_id: {self.worker_job_id}",
            f"run_status: {self.run_status}",
            f"execution_dir: {self.execution_dir}",
            f"task_json: {self.task_path}",
            f"run_json: {self.run_path}",
            f"events_jsonl: {self.events_path}",
            "ledger_replay:",
            f"  task: {'present' if self.replay['task'] is not None else 'missing'}",
            f"  run: {'present' if self.replay['run'] is not None else 'missing'}",
            f"  events: {len(self.replay['events'])}",
            f"  transitions: {', '.join(self.event_transitions)}",
            "next_step: set GOODTEAM_DOGFOOD_ADAPTER=codex, claude, or gemini only",
            "  after separately validating that real adapter path and credentials.",
        ]
        return "\n".join(lines)


class LocalDogfoodGitHubClient:
    def __init__(self, snapshot: GitHubIssueSnapshot | None = None):
        self.snapshot = snapshot or p0_1_dogfood_issue_snapshot()
        self.fetches: list[tuple[str, int]] = []

    def fetch_issue(self, repo: str, issue_number: int) -> GitHubIssueSnapshot:
        self.fetches.append((repo, issue_number))
        if repo.strip().lower() != self.snapshot.repo.lower() or issue_number != self.snapshot.issue_number:
            raise GitHubIssueNotFound(repo, issue_number)
        return self.snapshot

    def create_pr(self, **_kwargs: object) -> GitHubBinding:
        raise RuntimeError("P0-1 dogfood does not create GitHub pull requests")


def p0_1_dogfood_issue_snapshot() -> GitHubIssueSnapshot:
    return GitHubIssueSnapshot(
        repo=P0_1_DOGFOOD_REPO,
        issue_number=P0_1_DOGFOOD_ISSUE_NUMBER,
        title=P0_1_DOGFOOD_TITLE,
        body=(
            "Local dogfood fixture for the first runnable P0-1 execution path. "
            "This fixture intentionally avoids external network access."
        ),
        url=P0_1_DOGFOOD_URL,
        labels=["p0", "dogfood", "execution"],
    )


def run_p0_1_dogfood(
    *,
    project_root: Path,
    repo: str = P0_1_DOGFOOD_REPO,
    issue_number: int = P0_1_DOGFOOD_ISSUE_NUMBER,
    adapter: str = "noop",
    role: str = "implementation",
    github_client: LocalDogfoodGitHubClient | None = None,
    execute_worker: bool = True,
) -> DogfoodResult:
    root = Path(project_root)
    client = github_client or LocalDogfoodGitHubClient()
    orchestrator = ExecutionOrchestrator(root, client)
    run = orchestrator.intake_github_issue(
        repo=repo,
        issue_number=issue_number,
        adapter=adapter,
        role=role,
    )
    if execute_worker and run.status is ExecutionRunStatus.QUEUED:
        run = orchestrator.run_execution(run.execution_id)

    ledger = ExecutionLedger(root)
    replay = ledger.replay(run.execution_id)
    execution_dir = root / ".goodteam" / "executions" / run.execution_id
    return DogfoodResult(
        project_root=root,
        repo=repo,
        issue_number=issue_number,
        adapter=str(run.adapter),
        task_id=run.task_id,
        execution_id=run.execution_id,
        worker_job_id=run.worker_job_id,
        run_status=str(run.status.value),
        execution_dir=execution_dir,
        task_path=execution_dir / "task.json",
        run_path=execution_dir / "run.json",
        events_path=execution_dir / "events.jsonl",
        replay=replay,
    )


def expected_p0_1_ids() -> tuple[str, str]:
    task_id = task_id_from_github_issue(P0_1_DOGFOOD_REPO, P0_1_DOGFOOD_ISSUE_NUMBER)
    return task_id, execution_id_from_task(task_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the offline P0-1 dogfood execution path.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--adapter", default="noop")
    parser.add_argument("--role", default="implementation")
    parser.add_argument("--no-run-worker", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = run_p0_1_dogfood(
            project_root=args.project_root,
            adapter=args.adapter,
            role=args.role,
            execute_worker=not args.no_run_worker,
        )
    except Exception as exc:
        print(f"p0_1_dogfood failed: {exc}", file=sys.stderr)
        return 1

    print(result.to_cli_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
