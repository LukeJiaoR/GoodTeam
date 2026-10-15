"""Protected report projection, stale-read and HTML injection boundaries."""
from copy import deepcopy
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import quote

import pytest

from goodteam_os import cli
from goodteam_os.execution.api import ExecutionAPI
from goodteam_os.execution.candidate_validation import CandidateValidationService
from goodteam_os.execution.review_workbench_browser import render_review_workbench_html
from goodteam_os.execution.review_workbench_renderer import render_review_workbench
from goodteam_os.execution.review_workbench_report import retained_report
from goodteam_os.skills.review_results import ReviewResultStore
from goodteam_os.workers import WorkerJobStore
from test_automatic_review_entry import entry as entry_fixture, admitted, validate_args  # noqa: F401
from test_review_workbench_browser_mvp import _capture_dashboard_requests
from test_task_input_snapshot import ctx  # noqa: F401


@pytest.fixture
def reviewed(request, capsys):
    c = request.getfixturevalue("entry_fixture")
    assert cli.main(validate_args(c), codex_review_admission=admitted(c, findings=bool(getattr(request, "param", False)))) == 0
    capsys.readouterr()
    c.displayed = CandidateValidationService(c.project).evidence(c.execution_id)
    return c


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_complete_report_is_read_only_and_bound_to_displayed_candidate(reviewed, monkeypatch):
    c = reviewed
    monkeypatch.setattr(WorkerJobStore, "run_job", lambda *_: pytest.fail("Read dispatched a Worker"))
    before = files(c.workspace.parent)
    for _ in range(2):
        workbench = ExecutionAPI(c.project).get_review_workbench(c.execution_id)
        value = workbench["candidate_review_report"]
        assert value["available"] and value["head_sha"] == c.head and value["base_sha"] == c.base
        assert value["validation_receipt_id"] == c.displayed["receipt"]["receipt_id"]
        assert value["review_receipt_id"] == c.displayed["review"]["receipt_id"]
        assert value["report_digest"] == c.displayed["review"]["provenance"]["report_digest"]
        assert value["runtime_verification"] == "contract_fixture"
        encoded = json.dumps(value)
        assert str(c.workspace) not in encoded and "OLD_CONDITION" not in encoded
        assert "prompt" not in value and "raw_report" not in value
        for rendered in (render_review_workbench(workbench), render_review_workbench_html(workbench)):
            assert "Assessed the original requirement" in rendered
            assert "head/feature.py" in rendered and "Exact candidate source inspected" in rendered
            assert "No findings recorded" in rendered
            assert "not an independently observed test run" in rendered
    assert files(c.workspace.parent) == before and c.calls.read_text() == "review\n"


def test_existing_http_routes_and_static_export_include_report(reviewed, monkeypatch):
    c = reviewed
    request = _capture_dashboard_requests(c.workspace, monkeypatch)
    query = "?project_root=" + quote(str(c.project))
    status, _headers, body = request("GET", f"/api/executions/{c.execution_id}/review-workbench" + query)
    assert status == 200 and json.loads(body)["candidate_review_report"]["available"]
    status, _headers, html = request("GET", f"/review-workbench/{c.execution_id}" + query)
    assert status == 200 and 'id="candidate-review-report"' in html
    assert "Assessed the original requirement" in html
    exported = ExecutionAPI(c.project).export_review_workbench_html(c.execution_id)
    document = Path(exported["html_path"]).read_text()
    assert "Assessed the original requirement" in document and "fetch(" not in document
    assert c.calls.read_text() == "review\n"


@pytest.mark.parametrize("reviewed", [True], indirect=True)
def test_actionable_finding_detail_and_source_are_available_with_blocked_fix(reviewed):
    c = reviewed
    workbench = ExecutionAPI(c.project).get_review_workbench(c.execution_id)
    value = workbench["candidate_review_report"]
    assert value["available"] and value["decision"] == "request_changes"
    assert value["report"]["findings"][0]["detail"] == "Requires correction"
    for rendered in (render_review_workbench(workbench), render_review_workbench_html(workbench)):
        assert "Requires correction" in rendered and "Preserve OLD_CONDITION" in rendered
        assert "Original condition unresolved" in rendered and "head/feature.py" in rendered
    assert c.calls.read_text() == "review\n"


@pytest.mark.parametrize("mutation", ["missing", "corrupt"])
def test_unverified_retention_returns_no_report_or_exception_text(reviewed, mutation):
    c = reviewed
    job = c.displayed["review"]["reviewer"]
    path = c.operator / "skills" / "review-results" / job / "outcome.json"
    if mutation == "missing":
        path.unlink()
    else:
        path.write_text("PRIVATE CORRUPT CONTENT")
    value = retained_report(c.project, c.execution_id, c.displayed)
    assert value == {"available": False, "reason": "report_unavailable"}
    assert not ExecutionAPI(c.project).get_review_workbench(c.execution_id)["candidate_review_report"]["available"]
    assert c.calls.read_text() == "review\n"


def test_new_check_for_same_head_cannot_borrow_old_report(reviewed):
    c = reviewed
    CandidateValidationService(c.project).validate(c.execution_id, operation_id="new-check", head_sha=c.head,
                                                   argv=c.argv, criteria=[0, 1])
    result = retained_report(c.project, c.execution_id, c.displayed)
    assert result == {"available": False, "reason": "displayed_review_changed"}
    current = ExecutionAPI(c.project).get_review_workbench(c.execution_id)["candidate_review_report"]
    assert current == {"available": False, "reason": "machine_review_missing"}
    assert c.calls.read_text() == "review\n"


def test_dirty_candidate_does_not_show_report_as_current(reviewed):
    c = reviewed
    (c.project / "feature.py").write_text("VALUE = 'changed'\n")
    assert not retained_report(c.project, c.execution_id, c.displayed)["available"]
    workbench = ExecutionAPI(c.project).get_review_workbench(c.execution_id)
    assert workbench["candidate_review_report"] == {"available": False, "reason": "candidate_not_current"}
    assert workbench["evidence"]["candidate_validation"]["acceptance_status"] == "unknown"


@pytest.mark.parametrize("change", ["digest", "source"])
def test_report_read_cannot_mix_provenance_or_moving_source(reviewed, monkeypatch, change):
    c = reviewed
    original = ReviewResultStore.detail
    reads = []
    def moving(self, **kwargs):
        detail = original(self, **kwargs)
        reads.append(self.job_id)
        if len(reads) == 2:
            if change == "digest":
                detail["report_digest"] = "sha256:" + "0" * 64
            else:
                (c.project / "feature.py").write_text("VALUE = 'changed'\n")
        return detail
    monkeypatch.setattr(ReviewResultStore, "detail", moving)
    assert retained_report(c.project, c.execution_id, c.displayed) == {"available": False, "reason": "report_unavailable"}
    assert c.calls.read_text() == "review\n"


def test_inconclusive_report_preserves_unfinished_checks_without_acceptance(request, capsys, monkeypatch):
    import test_automatic_review_entry as fixture
    c = request.getfixturevalue("entry_fixture")
    original = fixture.report
    def incomplete(**kwargs):
        value = original(**kwargs)
        value["checks"][1].update(status="not_run", evidence_ids=[], detail="This condition was not checked.")
        return value
    monkeypatch.setattr(fixture, "report", incomplete)
    assert cli.main(validate_args(c), codex_review_admission=admitted(c)) == 0
    capsys.readouterr()
    value = ExecutionAPI(c.project).get_review_workbench(c.execution_id)["candidate_review_report"]
    assert value["available"] and value["decision"] == "inconclusive"
    assert value["report"]["findings"] == []
    assert value["report"]["checks"][1]["status"] == "not_run"


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_untrusted_report_text_stays_inert_in_html_and_embedded_json(reviewed):
    c = reviewed
    workbench = ExecutionAPI(c.project).get_review_workbench(c.execution_id)
    value = deepcopy(workbench["candidate_review_report"])
    attack = '</script><img src=x onerror=alert(1)>\n<strong>not markup</strong>'
    value["report"]["summary"] = attack
    value["report"]["checks"][0]["detail"] = attack
    value["report"]["evidence"][0].update(quote=attack, observation=attack, path=attack)
    value["report"]["findings"] = [{"id": "F1", "severity": "critical", "title": attack,
        "detail": attack, "recommendation": attack, "evidence_ids": ["E1"]}]
    workbench["candidate_review_report"] = value
    html = render_review_workbench_html(workbench)
    parser = Tags()
    parser.feed(html)
    assert not [tag for tag, _attrs in parser.tags if tag in {"img", "iframe"}]
    report_parser = Tags()
    report_parser.feed(html.split('id="candidate-review-report"', 1)[1].split('</section>', 1)[0])
    assert not [tag for tag, _attrs in report_parser.tags if tag in {"button", "form", "script"}]
    assert [attrs for tag, attrs in parser.tags if tag == "script"] == [{"id": "review-workbench-data", "type": "application/json"}]
    assert "&lt;img" in html and '<a href="#review-evidence-E1">E1</a>' in html
    assert not [attrs for tag, attrs in parser.tags if tag == "a" and attrs.get("href", "").startswith(("file:", "javascript:"))]
    embedded = html.split('<script id="review-workbench-data" type="application/json">', 1)[1].split('</script>', 1)[0]
    assert json.loads(embedded)["candidate_review_report"]["report"]["summary"] == attack
