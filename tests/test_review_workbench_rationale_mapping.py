from __future__ import annotations

import json
from pathlib import Path

from goodteam_os.execution.pr_diff_contract import artifact_json_path, artifact_patch_path
from tests.review_workbench_helpers import (
    full_lifecycle as _full_lifecycle,
    warning_codes as _warning_codes,
)


def test_worker_result_exact_file_path_maps_to_changed_file(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    api.persist_pr_diff(execution_id)
    _write_worker_file(
        tmp_path,
        api.ledger_store.read_run(execution_id).worker_job_id,
        "result.md",
        "Implemented src/goodteam_os/execution/api.py so review workbench reads persisted diffs.",
    )

    file = api.get_review_workbench(execution_id)["diff"]["changed_files"][0]

    assert file["rationale_status"] == "mapped"
    assert file["rationale_refs"] == [
        {
            "kind": "worker_result",
            "title": "Worker result",
            "path": _worker_file_path(tmp_path, api.ledger_store.read_run(execution_id).worker_job_id, "result.md"),
            "excerpt": "Implemented src/goodteam_os/execution/api.py so review workbench reads persisted diffs.",
            "confidence": "direct",
        }
    ]


def test_prompt_snapshot_exact_file_path_maps_to_changed_file(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    api.persist_pr_diff(execution_id)
    worker_job_id = api.ledger_store.read_run(execution_id).worker_job_id
    _write_worker_file(
        tmp_path,
        worker_job_id,
        "context.md",
        "Regression target: tests/test_review_workbench_diff_artifact.py must stay browser-visible.",
    )

    file = api.get_review_workbench(execution_id)["diff"]["changed_files"][1]

    assert file["rationale_status"] == "mapped"
    assert file["rationale_refs"] == [
        {
            "kind": "prompt_snapshot",
            "name": "context",
            "title": "Prompt snapshot: context",
            "path": _worker_file_path(tmp_path, worker_job_id, "context.md"),
            "excerpt": "Regression target: tests/test_review_workbench_diff_artifact.py must stay browser-visible.",
            "confidence": "direct",
        }
    ]


def test_unmatched_file_stays_not_mapped_and_partial_warning_is_emitted(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    api.persist_pr_diff(execution_id)
    _write_worker_file(
        tmp_path,
        api.ledger_store.read_run(execution_id).worker_job_id,
        "result.md",
        "Implemented src/goodteam_os/execution/api.py only.",
    )

    workbench = api.get_review_workbench(execution_id)
    files = workbench["diff"]["changed_files"]

    assert files[0]["rationale_status"] == "mapped"
    assert files[1]["rationale_status"] == "not_mapped"
    assert files[1]["rationale_refs"] == []
    assert _warning_codes(workbench) >= {"rationale_mapping_partial"}


def test_all_unmatched_files_emit_mapping_missing_warning(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    api.persist_pr_diff(execution_id)
    _write_worker_file(
        tmp_path,
        api.ledger_store.read_run(execution_id).worker_job_id,
        "result.md",
        "Implemented the review workbench surface without naming individual files.",
    )

    workbench = api.get_review_workbench(execution_id)

    assert {file["rationale_status"] for file in workbench["diff"]["changed_files"]} == {"not_mapped"}
    assert _warning_codes(workbench) >= {"rationale_mapping_missing"}


def test_missing_rationale_artifacts_mark_each_file_without_synthetic_refs(tmp_path):
    execution_id = "exec_missing_rationale_mapping"
    _write_diff_artifact(
        tmp_path,
        execution_id,
        [
            {
                "path": "src/goodteam_os/execution/api.py",
                "status": "modified",
                "additions": 1,
                "deletions": 0,
                "patch_excerpt": "+change",
            }
        ],
    )

    from goodteam_os.execution.api import ExecutionAPI

    workbench = ExecutionAPI(tmp_path).get_review_workbench(execution_id)
    file = workbench["diff"]["changed_files"][0]

    assert file["rationale_status"] == "rationale_artifact_missing"
    assert file["rationale_refs"] == []
    assert _warning_codes(workbench) >= {"rationale_artifact_missing"}
    assert "rationale_mapping_missing" not in _warning_codes(workbench)


def test_rationale_excerpts_are_bounded_and_deterministic(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    api.persist_pr_diff(execution_id)
    worker_job_id = api.ledger_store.read_run(execution_id).worker_job_id
    before = "A" * 300
    after = "B" * 1200
    _write_worker_file(
        tmp_path,
        worker_job_id,
        "result.md",
        f"{before} src/goodteam_os/execution/api.py {after}",
    )

    first = api.get_review_workbench(execution_id)["diff"]["changed_files"][0]["rationale_refs"][0]["excerpt"]
    second = api.get_review_workbench(execution_id)["diff"]["changed_files"][0]["rationale_refs"][0]["excerpt"]

    assert first == second
    assert "src/goodteam_os/execution/api.py" in first
    assert len(first) <= 800
    assert "B" * 900 not in first


def _write_worker_file(tmp_path: Path, worker_job_id: str, filename: str, text: str) -> None:
    path = tmp_path / ".goodteam" / "workers" / "jobs" / worker_job_id / filename
    path.write_text(text, encoding="utf-8")


def _worker_file_path(tmp_path: Path, worker_job_id: str, filename: str) -> str:
    return str(Path(".goodteam") / "workers" / "jobs" / worker_job_id / filename)


def _write_diff_artifact(tmp_path: Path, execution_id: str, changed_files: list[dict[str, object]]) -> None:
    json_path = artifact_json_path(tmp_path, execution_id)
    patch_path = artifact_patch_path(tmp_path, execution_id)
    json_path.parent.mkdir(parents=True)
    patch_path.write_text("", encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "schema_version": "execution_pr_diff.v1",
                "execution_id": execution_id,
                "repo": "LukeJiaoR/GoodTeam",
                "pr_number": 2160,
                "branch_name": "codex/test",
                "base_ref": "main",
                "head_sha": "head-sha",
                "patch_path": f".goodteam/executions/{execution_id}/artifacts/pr-diff.patch",
                "changed_files": changed_files,
            }
        ),
        encoding="utf-8",
    )
