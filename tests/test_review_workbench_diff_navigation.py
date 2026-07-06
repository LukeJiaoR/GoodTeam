from __future__ import annotations

import json
from pathlib import Path

from goodteam_os.execution.ledger import ExecutionLedger
from goodteam_os.execution.review_workbench_browser import render_review_workbench_html
from goodteam_os.execution.review_workbench_diff import parse_unified_diff
from tests.review_workbench_helpers import full_lifecycle as _full_lifecycle
from tests.review_workbench_helpers import memory_event_files as _memory_event_files
from tests.review_workbench_helpers import warning_codes as _warning_codes


def test_unified_diff_parser_extracts_files_and_hunks():
    files = parse_unified_diff(_sample_patch())

    assert files[0]["path"] == "src/goodteam_os/execution/api.py"
    assert files[0]["old_path"] == "src/goodteam_os/execution/api.py"
    assert files[0]["new_path"] == "src/goodteam_os/execution/api.py"
    assert files[0]["hunks"][0]["header"] == "@@ -10,3 +10,4 @@"
    assert files[1]["path"] == "docs/review-workbench/browser-mvp.md"
    assert files[1]["hunks"][0]["old_start"] == 1
    assert files[1]["hunks"][0]["new_start"] == 1


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


def test_workbench_diff_includes_file_anchors_and_hunks_when_patch_exists(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    api.persist_pr_diff(execution_id)
    _write_patch(tmp_path, execution_id, _sample_patch())

    diff = api.get_review_workbench(execution_id)["diff"]

    assert diff["available"] is True
    assert diff["reason"] == ""
    assert diff["patch_state"] == "available"
    assert diff["changed_files"][0]["anchor"] == "file-src-goodteam-os-execution-api-py"
    assert diff["changed_files"][0]["rationale_refs"] == []
    assert diff["changed_files"][0]["rationale_status"] == "not_mapped"
    assert diff["changed_files"][0]["hunks"][0]["lines"][1]["kind"] == "delete"


def test_browser_html_includes_file_navigation_links(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    api.persist_pr_diff(execution_id)
    _write_patch(tmp_path, execution_id, _sample_patch())

    html = render_review_workbench_html(api.get_review_workbench(execution_id))

    assert '<nav class="diff-file-nav"' in html
    assert 'href="#file-src-goodteam-os-execution-api-py"' in html
    assert "src/goodteam_os/execution/api.py" in html


def test_browser_html_includes_changed_line_css_classes(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    api.persist_pr_diff(execution_id)
    _write_patch(tmp_path, execution_id, _sample_patch())

    html = render_review_workbench_html(api.get_review_workbench(execution_id))

    assert "diff-line-add" in html
    assert "diff-line-delete" in html
    assert "diff-line-context" in html
    assert "+    return rich_diff" in html
    assert "-    return diff" in html


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
    html = render_review_workbench_html(workbench)
    assert "patch_missing" in html
    assert 'href="#file-src-goodteam-os-execution-api-py"' in html


def test_malformed_patch_does_not_crash_and_emits_warning(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    api.persist_pr_diff(execution_id)
    _write_patch(tmp_path, execution_id, "@@ not a valid hunk\n+floating addition")

    workbench = api.get_review_workbench(execution_id)

    assert workbench["diff"]["available"] is True
    assert workbench["diff"]["patch_state"] == "parse_failed"
    assert workbench["diff"]["changed_files"][0]["hunks"] == []
    assert _warning_codes(workbench) >= {"patch_parse_failed"}
    html = render_review_workbench_html(workbench)
    assert "patch_parse_failed" in html


def test_get_workbench_diff_navigation_is_read_only(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    api.persist_pr_diff(execution_id)
    _write_patch(tmp_path, execution_id, _sample_patch())
    ledger = ExecutionLedger(tmp_path)
    before_events = ledger.read_events(execution_id)
    before_memory = _memory_event_files(tmp_path)

    api.get_review_workbench(execution_id)
    api.get_review_workbench(execution_id)

    assert ledger.read_events(execution_id) == before_events
    assert _memory_event_files(tmp_path) == before_memory


def test_static_export_includes_file_navigation_and_highlighted_lines(tmp_path):
    api, execution_id = _full_lifecycle(tmp_path)
    api.persist_pr_diff(execution_id)
    _write_patch(tmp_path, execution_id, _sample_patch())

    result = api.export_review_workbench_html(execution_id)

    html = Path(result["html_path"]).read_text(encoding="utf-8")
    payload = json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))
    assert payload["diff"]["changed_files"][0]["anchor"] == "file-src-goodteam-os-execution-api-py"
    assert '<nav class="diff-file-nav"' in html
    assert "diff-line-add" in html
    assert "diff-line-delete" in html


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
