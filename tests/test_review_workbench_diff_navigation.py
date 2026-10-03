from __future__ import annotations

from pathlib import Path

from goodteam_os.execution.review_workbench_diff import parse_unified_diff
from tests.review_workbench_helpers import full_lifecycle as _full_lifecycle
from tests.review_workbench_helpers import warning_codes as _warning_codes


def test_unified_diff_parser_tracks_changed_line_numbers():
    files = parse_unified_diff(_sample_patch())
    lines = files[0]["hunks"][0]["lines"]

    assert lines == [
        {"kind": "context", "old_line": 10, "new_line": 10, "text": " def get_review_workbench():"},
        {"kind": "delete", "old_line": 11, "new_line": None, "text": "-    return diff"},
        {"kind": "add", "old_line": None, "new_line": 11, "text": "+    return rich_diff"},
        {"kind": "context", "old_line": 12, "new_line": 12, "text": " "},
        {"kind": "add", "old_line": None, "new_line": 13, "text": "+# navigation"},
    ]


def test_missing_patch_still_shows_files_and_patch_missing_state(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    result = api.persist_pr_diff(execution_id)
    Path(result["patch_path"]).unlink()

    workbench = api.get_review_workbench(execution_id)

    assert workbench["diff"]["available"] is True
    assert workbench["diff"]["patch_state"] == "missing"
    assert workbench["diff"]["reason"] == ""
    assert workbench["diff"]["changed_files"][0]["hunks"] == []
    assert _warning_codes(workbench) >= {"patch_missing"}


def test_malformed_patch_does_not_crash_and_emits_warning(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    api.persist_pr_diff(execution_id)
    _write_patch(tmp_path, execution_id, "@@ not a valid hunk\n+floating addition")

    workbench = api.get_review_workbench(execution_id)

    assert workbench["diff"]["available"] is True
    assert workbench["diff"]["patch_state"] == "parse_failed"
    assert workbench["diff"]["changed_files"][0]["hunks"] == []
    assert _warning_codes(workbench) >= {"patch_parse_failed"}


def _write_patch(tmp_path: Path, execution_id: str, patch: str) -> None:
    patch_path = tmp_path / ".goodteam" / "executions" / execution_id / "artifacts" / "pr-diff.patch"
    patch_path.write_text(patch, encoding="utf-8")


def _sample_patch() -> str:
    return "\n".join(
        [
            "diff --git a/src/goodteam_os/execution/api.py b/src/goodteam_os/execution/api.py",
            "--- a/src/goodteam_os/execution/api.py",
            "+++ b/src/goodteam_os/execution/api.py",
            "@@ -10,3 +10,4 @@",
            " def get_review_workbench():",
            "-    return diff",
            "+    return rich_diff",
            " ",
            "+# navigation",
            "diff --git a/docs/review-workbench/browser-mvp.md b/docs/review-workbench/browser-mvp.md",
            "--- a/docs/review-workbench/browser-mvp.md",
            "+++ b/docs/review-workbench/browser-mvp.md",
            "@@ -1 +1,2 @@",
            " # Browser Review Workbench MVP",
            "+File navigation is available.",
        ]
    )
