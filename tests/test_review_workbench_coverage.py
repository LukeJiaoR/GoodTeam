"""Existing protected API receipts -> visible read-only coverage; no provider."""
from copy import deepcopy
from dataclasses import replace
from html.parser import HTMLParser
import sys

import pytest

from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.review_workbench_browser import render_review_workbench_html
from goodteam_os.execution.review_workbench_coverage import acceptance_coverage_lines, check_metadata_lines
from goodteam_os.execution.review_workbench_renderer import render_review_workbench
from goodteam_os.execution.task_snapshot import execution_key
from test_candidate_validation import candidate as candidate_fixture, validate  # noqa: F401
from test_task_input_snapshot import ctx  # noqa: F401


@pytest.fixture(name="candidate")
def coverage_candidate(request):
    return request.getfixturevalue("candidate_fixture")


class VisibleText(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.skip = 0
        self.parts = []
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag in {"script", "style"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    @property
    def text(self):
        return " ".join(self.parts)


def review(c):
    return ExecutionAPI(c.project).get_review_workbench(c.execution_id)


def coverage(c):
    return "\n".join(acceptance_coverage_lines(review(c)["evidence"]))


def test_actual_api_duplicate_criteria_receipt_head_and_process_metadata(candidate, tmp_path):
    c = candidate
    result = validate(c)
    value = review(c)
    for text in (render_review_workbench(value), VisibleText(render_review_workbench_html(value)).text):
        assert "Criterion 1 (CLI index 0): preserve OLD_CONDITION" in text
        assert "Criterion 2 (CLI index 1): preserve OLD_CONDITION" in text
        assert "Check passed; acceptance unknown" in text
        assert "Not checked (no current check mapped); acceptance unknown" in text
        assert result["receipt_id"] in text and c.head in text
        assert "Process exit code: 0" in text
        assert "Check duration:" in text and "seconds (host wall clock)" in text
        assert "Check command: python" in text
        assert "operator supplied" in text and "capture does not establish user approval" in text
    rows = value["evidence"]["task_input"]["acceptance_coverage"]
    assert rows[0]["criterion_id"] != rows[1]["criterion_id"]
    assert all(row["criterion_id"] in coverage(c) for row in rows)
    for mode in ("static", "interactive"):
        (tmp_path / f"coverage-{mode}.html").write_text(render_review_workbench_html(
            value, decision_actions_enabled=mode == "interactive"))


def test_project_contract_edits_do_not_relabel_protected_coverage(candidate):
    c = candidate
    validate(c)
    ledger = c.orchestrator.ledger
    task = ledger.read_task(c.execution_id)
    ledger.create_task(replace(task, task_contract=replace(task.task_contract,
                       acceptance_criteria=["MUTABLE_REPLACEMENT"])))
    text = coverage(c)
    assert "MUTABLE_REPLACEMENT" not in text and text.count("preserve OLD_CONDITION") == 2


def test_missing_capture_never_falls_back_to_visible_ledger_criteria(candidate):
    c = candidate
    validate(c)
    (c.store.root / execution_key(c.execution_id) / "snapshot.json").unlink()
    text = coverage(c)
    assert "Original captured criteria unavailable" in text
    assert "OLD_CONDITION" not in text and "Check passed" not in text


def test_latest_check_replaces_mapping_instead_of_accumulating_old_success(candidate):
    c = candidate
    old = validate(c)
    new = validate(c, operation_id="new-check", criteria=[1], argv=[sys.executable, "-c", "raise SystemExit(2)"])
    text = coverage(c)
    assert old["receipt_id"] not in text and new["receipt_id"] in text
    assert "Check failed; acceptance unknown" in text and "Check passed" not in text
    assert "Not checked (no current check mapped)" in text
    assert "Process exit code: 2" in VisibleText(render_review_workbench_html(review(c))).text


def test_stale_candidate_and_mixed_snapshot_receipt_stay_unknown(candidate):
    c = candidate
    validate(c)
    (c.project / "feature.py").write_text("DIRTY = True\n")
    value = review(c)
    assert "Check result unknown" in "\n".join(acceptance_coverage_lines(value["evidence"]))
    value["evidence"]["candidate_validation"]["check_status"] = "passed"
    value["evidence"]["candidate_validation"]["criteria"][0]["check_status"] = "passed"
    value["evidence"]["candidate_validation"]["receipt"]["snapshot_digest"] = "f" * 64
    text = "\n".join(acceptance_coverage_lines(value["evidence"]))
    assert "Check passed" not in text and "no matching current receipt" in text


def test_partial_capture_history_preserves_text_without_claiming_check(candidate):
    c = candidate
    validate(c)
    (c.store.root / execution_key(c.execution_id) / "receipts.json").unlink()
    text = coverage(c)
    assert "Input history incomplete" in text and "OLD_CONDITION" in text
    assert "Check result unknown" in text and "Check passed" not in text


def test_no_check_legacy_and_empty_capture_have_distinct_messages(candidate):
    text = coverage(candidate)
    assert text.count("Not checked (no current check mapped)") == 2
    assert "task_input_not_captured" in "\n".join(acceptance_coverage_lines({}))
    evidence = review(candidate)["evidence"]
    evidence["task_input"]["acceptance_coverage"] = []
    assert "No acceptance criteria were captured" in "\n".join(acceptance_coverage_lines(evidence))


def test_coverage_source_text_is_escaped_and_creates_no_action(candidate):
    value = review(candidate)
    value["evidence"]["task_input"]["acceptance_coverage"][0]["text"] = '<img src=x onerror="alert(1)">'
    html = render_review_workbench_html(value)
    assert '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;' in html
    assert 'img' not in VisibleText(html).tags
    assert "fetch(" not in html  # Static export has no action script.


@pytest.mark.parametrize("start,end", [("broken", "broken"), ("2026-01-01T00:00:02Z", "2026-01-01T00:00:01Z"),
                                      ("2026-01-01T00:00:00", "2026-01-01T00:00:01")])
def test_safe_metadata_omits_arguments_paths_logs_and_invalid_duration(start, end):
    value = {"available": True, "command_name": "/private/PARENT_SENTINEL/python", "argv": ["ARG_SENTINEL"],
             "environment": {"SECRET": "ENV_SENTINEL"}, "receipt": {"receipt_id": "validation:example",
             "observation": {"exit_code": None, "started_at": start, "finished_at": end, "stdout": "LOG_SENTINEL"}}}
    before = deepcopy(value)
    text = "\n".join(check_metadata_lines(value))
    assert "Check command: python" in text and "Process exit code: unknown" in text
    assert "Check duration: unknown" in text
    assert not any(word in text for word in ("PARENT_SENTINEL", "ARG_SENTINEL", "ENV_SENTINEL", "LOG_SENTINEL"))
    assert value == before
