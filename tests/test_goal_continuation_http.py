"""Real HTTP/background service/run/Worker evidence, with synthetic local models."""
from threading import Event




def test_service_ticks_project_and_issue_inputs_when_goal_scan_fails(tmp_path, monkeypatch, caplog):
    from goodteam_os import operator_manager_intake, project_iteration_continuation
    from goodteam_os.goal_continuation import start_goal_service
    from goodteam_os.goals import GoalCorruptState

    monkeypatch.delenv("GOODTEAM_DATABASE_URL", raising=False)
    ledger = tmp_path / "state" / "goals.sqlite3"
    ledger.parent.mkdir()
    ledger.write_bytes(b"not a goal ledger")

    calls = []
    consumed = Event()

    def tick_project(workspace):
        calls.append(("project", workspace))

    def consume_issues(workspace, *, stop):
        calls.append(("issues", workspace, stop))
        stop.set()
        consumed.set()

    monkeypatch.setattr(project_iteration_continuation, "tick_authorized_project_continuations", tick_project)
    monkeypatch.setattr(operator_manager_intake, "consume_manager_inputs", consume_issues)
    stop = start_goal_service(tmp_path)
    try:
        assert consumed.wait(timeout=5), "Goal scan failure blocked Manager input consumption"
    finally:
        stop.set()
        stop.join(timeout=5)

    assert calls == [("project", tmp_path), ("issues", tmp_path, stop)]
    assert any(record.getMessage() == "Goal continuation scan failed"
               and record.exc_info and isinstance(record.exc_info[1], GoalCorruptState)
               for record in caplog.records)
