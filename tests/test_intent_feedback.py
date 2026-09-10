from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from goodteam_os import web
from goodteam_os.graph import prd_worker_task, rfc_worker_task, takeover_brief_worker_task
from goodteam_os.intent_commit import IntentCommitStore
from goodteam_os.manager_execution import ManagerExecutionError
from goodteam_os.projects import write_project_config
from goodteam_os.state import ApprovalDecision, StateStore
from intent_feedback_helpers import feedback_post_fixture  # noqa: F401
from test_intent_commit import _approval_fixture


RAW_NOTE = "  Preserve Markdown breaks.  \n\n    keep_indentation()\n"


@pytest.fixture
def start_payload(tmp_path):
    target, _ = _approval_fixture(tmp_path)
    return {"project_key": "DEMO", "approval": "prd", **target}


def test_start_http_persists_feedback_before_navigation_without_execution(
    tmp_path, start_payload, monkeypatch, feedback_post,
):
    def forbidden(**_kwargs):
        pytest.fail("Opening feedback must not interpret or execute")

    monkeypatch.setattr(web, "run_manager_interpretation", forbidden)
    monkeypatch.setattr(web, "decide_approval", forbidden)
    status, result = feedback_post(
        "/api/intent-commit/start", {**start_payload, "initial_text": RAW_NOTE},
    )
    assert 200 == status
    intent = result["intent_commit"]
    assert RAW_NOTE == intent["draft_text"]
    assert "discussing" == intent["status"]
    assert None is intent["interpretation"]
    assert intent == IntentCommitStore(tmp_path, "DEMO").get(intent["id"])
    assert ApprovalDecision.PENDING == StateStore(tmp_path).read_project_current("DEMO").approvals["prd"]


def test_repeated_start_preserves_one_draft_and_event_history(tmp_path, start_payload):
    def start(_index):
        return web.start_request_changes_intent(
            workspace=tmp_path, **start_payload, initial_text=RAW_NOTE,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(start, range(8)))
    assert 1 == sum(result["created"] for result in results)
    assert 1 == len({result["intent_commit"]["id"] for result in results})
    assert 1 == len(IntentCommitStore(tmp_path, "DEMO").list_intents())
    assert all(results[0]["intent_commit"] == result["intent_commit"] for result in results)


@pytest.mark.parametrize("initial_text", ["", " \n\t "])
def test_blank_start_remains_an_empty_discussion(tmp_path, start_payload, initial_text):
    result = web.start_request_changes_intent(
        workspace=tmp_path, **start_payload, initial_text=initial_text,
    )
    assert "" == result["intent_commit"]["draft_text"]
    assert "discussing" == result["intent_commit"]["status"]


def test_feedback_can_seed_an_existing_empty_draft(tmp_path, start_payload):
    empty = web.start_request_changes_intent(workspace=tmp_path, **start_payload)
    seeded = web.start_request_changes_intent(
        workspace=tmp_path, **start_payload, initial_text=RAW_NOTE,
    )
    assert False is seeded["created"]
    assert empty["intent_commit"]["id"] == seeded["intent_commit"]["id"]
    assert RAW_NOTE == seeded["intent_commit"]["draft_text"]


@pytest.mark.parametrize("discussed", [False, True])
def test_different_existing_draft_returns_conflict_without_overwrite(
    tmp_path, start_payload, discussed, feedback_post,
):
    opened = web.start_request_changes_intent(
        workspace=tmp_path, **start_payload, initial_text=RAW_NOTE,
    )["intent_commit"]
    store = IntentCommitStore(tmp_path, "DEMO")
    if discussed:
        store.discuss(opened["id"], RAW_NOTE)
    before = store.get(opened["id"])
    status, _result = feedback_post(
        "/api/intent-commit/start", {**start_payload, "initial_text": "Different feedback"},
    )
    assert 409 == status
    assert before == store.get(opened["id"])


def test_stale_start_does_not_store_or_retarget_feedback(tmp_path, start_payload, feedback_post):
    (tmp_path / "prd.md").write_text("# Updated artifact\n", encoding="utf-8")
    status, _result = feedback_post(
        "/api/intent-commit/start", {**start_payload, "initial_text": RAW_NOTE},
    )
    assert 409 == status
    assert [] == IntentCommitStore(tmp_path, "DEMO").list_intents()


def test_failed_interpretation_keeps_accepted_feedback(tmp_path, start_payload, monkeypatch):
    intent = web.start_request_changes_intent(
        workspace=tmp_path, **start_payload, initial_text=RAW_NOTE,
    )["intent_commit"]
    config = tmp_path / "demo.local.yaml"
    config.write_text("key: DEMO\n", encoding="utf-8")
    state = StateStore(tmp_path).read_project_current("DEMO")
    state.source_ref = str(config)
    StateStore(tmp_path).write_project_current(state)

    def fail(**_kwargs):
        raise ManagerExecutionError("Unavailable")

    monkeypatch.setattr(web, "run_manager_interpretation", fail)
    with pytest.raises(web.ProjectApiError):
        web.discuss_request_changes_intent(
            workspace=tmp_path, project_key="DEMO", intent_id=intent["id"], text=RAW_NOTE,
        )
    assert intent == IntentCommitStore(tmp_path, "DEMO").get(intent["id"])


def test_confirm_delivers_raw_feedback_to_revision_worker_once(
    tmp_path: Path, start_payload, monkeypatch,
):
    intent = web.start_request_changes_intent(
        workspace=tmp_path, **start_payload, initial_text=RAW_NOTE,
    )["intent_commit"]
    web.discuss_request_changes_intent(
        workspace=tmp_path, project_key="DEMO", intent_id=intent["id"], text=RAW_NOTE,
    )
    calls = []

    def revise(**kwargs):
        calls.append(kwargs["state"].change_requests["prd"])
        state = kwargs["state"]
        state.next_node = "await_prd_approval"
        return state

    monkeypatch.setattr(web, "should_generate_artifact_with_worker", lambda *_args: False)
    monkeypatch.setattr(web, "run_until_next_gate", revise)
    for _ in range(2):
        web.confirm_request_changes_intent(
            workspace=tmp_path, project_key="DEMO", intent_id=intent["id"],
        )
    assert [RAW_NOTE] == calls


@pytest.mark.parametrize("gate", ["prd", "rfc", "takeover"])
def test_worker_prompt_preserves_feedback_formatting(tmp_path, start_payload, gate):
    state = StateStore(tmp_path).read_project_current("DEMO")
    state.change_requests[gate] = RAW_NOTE
    tasks = {
        "prd": lambda: prd_worker_task(state),
        "rfc": lambda: rfc_worker_task(state),
        "takeover": lambda: takeover_brief_worker_task(state, []),
    }
    assert RAW_NOTE in tasks[gate]()


@pytest.mark.parametrize("feedback", [42, {"text": "Do not coerce"}, "x" * 8001])
def test_invalid_feedback_is_rejected_without_creating_a_draft(
    tmp_path, start_payload, feedback_post, feedback,
):
    status, _result = feedback_post(
        "/api/intent-commit/start", {**start_payload, "initial_text": feedback},
    )
    assert 409 == status
    assert [] == IntentCommitStore(tmp_path, "DEMO").list_intents()


def test_work_projection_restores_untrimmed_feedback(tmp_path, start_payload):
    root = tmp_path / "target"
    root.mkdir()
    write_project_config(tmp_path / "projects", "DEMO", "Demo", repo_path=str(root))
    intent = web.start_request_changes_intent(
        workspace=tmp_path, **start_payload, initial_text=RAW_NOTE,
    )["intent_commit"]
    payload = web.build_project_work_state(tmp_path, "DEMO")
    restored = payload["team_workspace"]["attention_request"]["intent_commit"]
    assert intent["id"] == restored["id"]
    assert RAW_NOTE == restored["draft_text"]
