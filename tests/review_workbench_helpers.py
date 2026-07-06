from __future__ import annotations

from pathlib import Path

from goodteam_os.execution import GitHubBinding, GitHubIssueNotFound, GitHubIssueSnapshot
from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.models import LedgerEvent


class FakeClock:
    def __init__(self):
        self.timestamps = [f"2026-07-06T02:{minute:02d}:00Z" for minute in range(30, 50)]
        self.index = 0

    def __call__(self) -> str:
        if self.index >= len(self.timestamps):
            return self.timestamps[-1]
        value = self.timestamps[self.index]
        self.index += 1
        return value


class FakeGitHubClient:
    def __init__(self):
        self.issues = {("lukejiaor/goodteam", 216): snapshot()}
        self.diff_payload: dict[str, object] | None = _diff_payload()

    def fetch_issue(self, repo: str, issue_number: int) -> GitHubIssueSnapshot:
        try:
            return self.issues[(repo.strip().lower(), issue_number)]
        except KeyError as exc:
            raise GitHubIssueNotFound(repo, issue_number) from exc

    def create_or_update_branch(self, **kwargs) -> dict[str, object]:
        return dict(kwargs)

    def commit_files(self, **kwargs) -> dict[str, object]:
        return {**kwargs, "commit_sha": "commit-sha-216"}

    def create_pr(self, **kwargs) -> GitHubBinding:
        return GitHubBinding(
            repo=str(kwargs["repo"]),
            issue_number=int(kwargs["issue_number"] or 0),
            pr_number=2160,
            branch_name=str(kwargs["branch_name"]),
            execution_id=str(kwargs["execution_id"]),
        )

    def fetch_checks(self, repo: str, ref: str) -> dict[str, object]:
        return {
            "check_runs": [
                {"name": "unit", "status": "completed", "conclusion": "success"},
                {"name": "lint", "status": "completed", "conclusion": "success"},
            ]
        }

    def fetch_pr_reviews(self, repo: str, pr_number: int) -> list[dict[str, object]]:
        return [
            {
                "id": 7001,
                "state": "APPROVED",
                "body": "Evidence-backed review is inspectable.",
                "submitted_at": "2026-07-06T03:00:00Z",
                "user": {"login": "reviewer-a"},
            }
        ]

    def fetch_pr_comments(self, repo: str, pr_number: int) -> list[dict[str, object]]:
        return [
            {
                "id": 8001,
                "body": "Workbench can show the canonical manifest.",
                "created_at": "2026-07-06T03:01:00Z",
                "user": {"login": "reviewer-b"},
            }
        ]

    def fetch_pr(self, repo: str, pr_number: int) -> dict[str, object]:
        return {
            "number": pr_number,
            "merged": True,
            "merged_at": "2026-07-06T03:10:00Z",
            "merge_commit_sha": "merge-sha-216",
            "merged_by": {"login": "LukeJiaoR"},
        }

    def fetch_pr_diff(self, repo: str, pr_number: int) -> dict[str, object] | None:
        return self.diff_payload


def snapshot() -> GitHubIssueSnapshot:
    return GitHubIssueSnapshot(
        repo="LukeJiaoR/GoodTeam",
        issue_number=216,
        title="P0-2: Review Workbench MVP",
        body="Build an evidence-backed workbench for agent execution review.",
        url="https://github.com/LukeJiaoR/GoodTeam/issues/216",
        labels=["p0", "review"],
    )


def _diff_payload() -> dict[str, object]:
    return {
        "base_ref": "main",
        "head_sha": "commit-sha-216",
        "patch": "\n".join(
            [
                "diff --git a/src/goodteam_os/execution/api.py b/src/goodteam_os/execution/api.py",
                "--- a/src/goodteam_os/execution/api.py",
                "+++ b/src/goodteam_os/execution/api.py",
                "@@ -1,3 +1,4 @@",
                "+def persist_pr_diff():",
                "+    return True",
            ]
        ),
        "changed_files": [
            {
                "path": "src/goodteam_os/execution/api.py",
                "status": "modified",
                "additions": 10,
                "deletions": 2,
                "patch_excerpt": "+def persist_pr_diff():",
            },
            {
                "path": "tests/test_review_workbench_diff_artifact.py",
                "status": "added",
                "additions": 120,
                "deletions": 0,
                "patch_excerpt": "+def test_completed_pr_execution_can_persist_diff_artifact",
            },
        ],
    }


def api(tmp_path: Path) -> ExecutionAPI:
    return ExecutionAPI(tmp_path, github_client=FakeGitHubClient(), clock=FakeClock())


def full_lifecycle(tmp_path: Path) -> tuple[ExecutionAPI, str]:
    execution_api = api(tmp_path)
    intake = execution_api.intake_github_issue(
        repo="LukeJiaoR/GoodTeam",
        issue_number=216,
        adapter="fake",
    )
    execution_id = intake["execution_id"]
    execution_api.run_execution(execution_id)
    execution_api.create_or_reuse_pr(execution_id)
    execution_api.observe_ci(execution_id)
    execution_api.sync_reviews(execution_id)
    execution_api.sync_merge(execution_id)
    return execution_api, execution_id


def warning_codes(workbench: dict[str, object]) -> set[str]:
    warnings = workbench["warnings"]
    assert isinstance(warnings, list)
    return {str(item["code"]) for item in warnings}


def memory_event_files(tmp_path: Path) -> dict[str, str]:
    root = tmp_path / ".goodteam" / "memory"
    if not root.is_dir():
        return {}
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("events.jsonl"))
    }


def failed_event(execution_id: str) -> LedgerEvent:
    return LedgerEvent(
        event_id=f"{execution_id}:0001:failed:execution_failed",
        execution_id=execution_id,
        task_id="task_failed",
        event_type="failed",
        actor="orchestrator",
        payload={"transition": "execution_failed", "failure_class": "worker_failed"},
        created_at="2026-07-06T02:31:00Z",
    )
