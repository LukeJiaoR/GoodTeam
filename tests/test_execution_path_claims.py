from __future__ import annotations

from goodteam_os.execution.path_claims import infer_execution_path_claims


def test_extracts_explicit_repo_paths_from_task_and_context_text():
    result = infer_execution_path_claims(
        task_text="Update `src/goodteam_os/workers.py` and tests/test_workers.py.",
        context_text="Docs live in docs/agent-sandbox-v0.md.",
        expected_artifacts=None,
    )

    assert result.claimed_paths == [
        "src/goodteam_os/workers.py",
        "tests/test_workers.py",
        "docs/agent-sandbox-v0.md",
    ]
    assert result.warnings == []
    assert result.claims[0].source == "task_text"
    assert result.claims[0].confidence == "high"
    assert result.claims[0].reason == "explicit repo-relative path"


def test_normalizes_markdown_punctuation_and_globs_to_directory_claims():
    result = infer_execution_path_claims(
        task_text="Touch (`src/goodteam_os/execution/*.py`) and `docs/review-workbench/`.",
        context_text="",
        expected_artifacts=None,
    )

    assert result.claimed_paths == [
        "src/goodteam_os/execution/",
        "docs/review-workbench/",
    ]


def test_extracts_paths_from_expected_artifacts_and_changed_files():
    result = infer_execution_path_claims(
        task_text="Implement the requested feature.",
        context_text="",
        expected_artifacts={
            "files": [
                {"path": "src/goodteam_os/execution/path_claims.py"},
                {"artifact_path": "tests/test_execution_path_claims.py"},
            ]
        },
        existing_changed_files=[
            "docs/agent-sandbox-v0.md",
            {"path": "src/goodteam_os/execution/orchestrator.py"},
        ],
    )

    assert result.claimed_paths == [
        "src/goodteam_os/execution/path_claims.py",
        "tests/test_execution_path_claims.py",
        "docs/agent-sandbox-v0.md",
        "src/goodteam_os/execution/orchestrator.py",
    ]
    assert {claim.source for claim in result.claims} == {
        "expected_artifacts",
        "existing_changed_files",
    }


def test_maps_worker_runtime_area_to_worker_files():
    result = infer_execution_path_claims(
        task_text="Worker runtime should include Codex adapter evidence.",
        context_text="",
        expected_artifacts=None,
    )

    assert "src/goodteam_os/workers.py" in result.claimed_paths
    assert "tests/test_workers.py" in result.claimed_paths
    assert all(claim.confidence == "medium" for claim in result.claims)


def test_maps_sandbox_cleanup_area_to_sandbox_files():
    result = infer_execution_path_claims(
        task_text="Improve sandbox cleanup recovery warnings.",
        context_text="",
        expected_artifacts=None,
    )

    assert "src/goodteam_os/execution/sandbox.py" in result.claimed_paths
    assert "tests/test_execution_sandbox_integration.py" in result.claimed_paths
    assert result.warnings == []


def test_maps_docs_area_to_docs_directory():
    result = infer_execution_path_claims(
        task_text="Update docs for sandbox evidence.",
        context_text="",
        expected_artifacts=None,
    )

    assert "docs/" in result.claimed_paths
    assert result.warnings == []


def test_broad_area_claims_emit_warning():
    result = infer_execution_path_claims(
        task_text="Update backend code, tests, and docs for this feature.",
        context_text="",
        expected_artifacts=None,
    )

    assert result.claimed_paths == ["src/goodteam_os/", "tests/", "docs/"]
    assert result.warnings == ["path_claims_broad_or_low_confidence"]
    assert all(claim.confidence == "low" for claim in result.claims)


def test_unresolved_claims_emit_warning():
    result = infer_execution_path_claims(
        task_text="Make it better.",
        context_text="No concrete area is named.",
        expected_artifacts=None,
    )

    assert result.claimed_paths == []
    assert result.claims == []
    assert result.warnings == ["path_claims_unresolved"]


def test_rejects_unsafe_absolute_parent_and_url_paths():
    result = infer_execution_path_claims(
        task_text="Ignore /etc/passwd, ../../secret, and https://example.com/context.md.",
        context_text="Keep src/goodteam_os/execution/api.py.",
        expected_artifacts=["/var/tmp/leak.txt", "../outside.md"],
    )

    assert result.claimed_paths == ["src/goodteam_os/execution/api.py"]
    assert "/etc/passwd" not in str(result)
    assert "../../secret" not in str(result)
    assert "https://example.com/context.md" not in str(result)
