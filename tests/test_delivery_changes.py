import os
from pathlib import Path
import subprocess

import pytest

from goodteam_os.delivery_changes import qa_report_status, retain_delivery_changes
from goodteam_os.delivery_outcomes import DeliveryArtifactError, build_delivery_summary, read_delivery_artifact
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.workflow_workspace import collect_workflow_workspace, prepare_workflow_workspace, workspace_for_workflow


@pytest.fixture
def retained(tmp_path, monkeypatch):
    monkeypatch.setenv("GOODTEAM_HOME", str(tmp_path / "service"))
    source = tmp_path / "source"
    source.mkdir()
    environment = {**os.environ, "GIT_AUTHOR_NAME": "Tests", "GIT_AUTHOR_EMAIL": "tests@goodteam.invalid",
                   "GIT_COMMITTER_NAME": "Tests", "GIT_COMMITTER_EMAIL": "tests@goodteam.invalid"}

    def git(*args):
        return subprocess.check_output(["git", "-c", "core.fsmonitor=false", *args], cwd=source, env=environment)

    git("init", "-q")
    (source / "base.txt").write_text("baseline\n")
    git("add", ".")
    git("commit", "-qm", "initial")
    state = create_initial_state("delivery-run", "DEMO", "Add a regression")
    state.repository_baseline = prepare_workflow_workspace(source, project_key=state.project_key, run_id=state.run_id)
    root = workspace_for_workflow(source, state.repository_baseline, project_key=state.project_key, run_id=state.run_id)
    content = b"// retained bytes\r\nconst html = '<img src=x>';\n"
    (root / "regression.js").write_bytes(content)
    result = collect_workflow_workspace(source, state.repository_baseline, project_key=state.project_key, run_id=state.run_id)
    # Delivery must read the collected commit, not mutable files or reported paths.
    (root / "regression.js").write_text("later workspace changes\n")
    retain_delivery_changes(tmp_path, state, result, worker_result={"role": "implementation", "artifacts": ["/etc/passwd"]})
    StateStore(tmp_path).write_project_current(state)
    return state, root, result, content


def test_delivery_retains_exact_source_patch_and_archive_binding(tmp_path, retained):
    from hashlib import sha256

    state, root, result, content = retained
    artifacts = build_delivery_summary(tmp_path, state=state)["artifacts"]
    source = next(item for item in artifacts if item["kind"] == "source")
    patch = next(item for item in artifacts if item["kind"] == "patch")
    def read(item):
        return read_delivery_artifact(tmp_path, project_key=state.project_key, run_id=state.run_id,
                                      artifact_key=item["key"], version=item["version"])

    value = read(source)
    assert value["content"].encode() == content
    assert value["version"] == "sha256:" + sha256(content).hexdigest()
    assert value["baseline_commit"] == result["baseline_commit"]
    assert value["result_commit"] == result["result_commit"]
    expected_patch = subprocess.check_output(["git", "-c", "core.fsmonitor=false", "diff", "--binary", "--full-index",
        "--no-ext-diff", "--no-textconv", "--no-renames", result["baseline_commit"], result["result_commit"], "--"], cwd=root)
    assert read(patch)["content"].encode() == expected_patch
    assert source["aliases"] == ["regression.js", "./regression.js", str(root / "regression.js"),
                                 str(Path(state.repository_baseline["source_root"]) / "regression.js")]
    assert all(item.get("relative_path") != "/etc/passwd" for item in artifacts)
    from goodteam_os.web import build_approval_evidence_snapshot

    snapshot = build_approval_evidence_snapshot(state=state, workspace=tmp_path, project_key="DEMO")
    assert {(item["key"], item["version"]) for item in snapshot["delivery_refs"]} == {
        (item["key"], item["version"]) for item in artifacts}
    StateStore(tmp_path).archive_project_current("DEMO", reason="rejected")
    StateStore(tmp_path).write_project_current(create_initial_state("next-run", "DEMO", "Next"))
    assert read(source)["content"].encode() == content


@pytest.mark.parametrize("alteration", ["bytes", "capability", "symlink"])
def test_retained_source_rejects_changed_bytes_unreadable_resource_and_links(tmp_path, retained, alteration):
    state, _, _, _ = retained
    item = next(item for item in build_delivery_summary(tmp_path, state=state)["artifacts"] if item["kind"] == "source")
    descriptor = state.resources[item["key"]]
    path = Path(descriptor.path)
    if alteration == "bytes":
        path.write_text("changed\n")
    elif alteration == "capability":
        descriptor.capabilities = []
    else:
        outside = tmp_path / "outside.txt"
        outside.write_text("not a delivery\n")
        path.unlink()
        path.symlink_to(outside)
    StateStore(tmp_path).write_project_current(state)
    with pytest.raises(DeliveryArtifactError):
        read_delivery_artifact(tmp_path, project_key="DEMO", run_id=state.run_id,
                               artifact_key=item["key"], version=item["version"])


@pytest.mark.parametrize("name,content", [
    (".env", "DO_NOT_EXPOSE"),
    ("credentials.json", "DO_NOT_EXPOSE"),
    ("config.py", 'API_KEY="private-fixture-key"'),
])
def test_exact_source_and_patch_preserve_sensitive_preview_exclusions(tmp_path, retained, name, content):
    state, root, _, _ = retained
    (root / name).write_text(content)
    source = Path(state.repository_baseline["source_root"])
    result = collect_workflow_workspace(source, state.repository_baseline, project_key=state.project_key, run_id=state.run_id)
    retain_delivery_changes(tmp_path, state, result)
    excluded = [item for item in state.delivery_items.values()
                if item["kind"] == "patch" or item["relative_path"] == name]
    assert len(excluded) == 2
    assert all(item["status"] == "unavailable" and item["unavailable_reason"] == "sensitive_content" for item in excluded)
    assert all(key not in state.resources for key, item in state.delivery_items.items() if item in excluded)


@pytest.mark.parametrize("content,expected", [
    ("Status: blocked\nE2E command exited 1, zero tests.\n", "blocked"),
    ("Status: passed\ncommand: pytest; exit 0\n", "passed"),
    ("执行已完成，但浏览器验收受阻。BLOCKED，exit 1。", "unknown"),
    ("Status: passed\nStatus: blocked\n", "unknown"),
])
def test_qa_outcome_uses_only_unambiguous_explicit_contract(content, expected):
    assert qa_report_status(content) == expected


def test_qa_raw_commands_survive_completed_process_without_inventing_pass(tmp_path, retained):
    from goodteam_os.graph import format_release_evidence_lines
    from goodteam_os.workers import WorkerJobStore

    state, _, result, _ = retained
    store = WorkerJobStore(Path(state.repository_baseline["source_root"]))
    job = store.create_job(run_id=state.run_id, project_key=state.project_key, role="qa", adapter="fake",
        task="Fixture only; never run", context="", workflow_workspace=state.repository_baseline)
    report = store.root / job["id"] / "result.md"
    original = "浏览器验收受阻，BLOCKED。\ncommand: npm run test:e2e --prefix frontend -- regression.spec.js\nexit: 1; tests: 0\n"
    report.write_text(original)
    worker_result = {"role": "qa", "status": "completed", "job_id": "not-the-host-job", "result_path": "/untrusted/result.md"}
    retain_delivery_changes(tmp_path, state, result, worker_store=store, worker_result=worker_result, worker_job=job)
    StateStore(tmp_path).write_project_current(state)
    item = next(item for item in build_delivery_summary(tmp_path, state=state)["artifacts"] if item["kind"] == "qa")
    assert item["qa_status"] == "unknown"
    assert item["process_status"] == "completed"
    assert item["job_id"] == job["id"]
    value = read_delivery_artifact(tmp_path, project_key="DEMO", run_id=state.run_id,
                                  artifact_key=item["key"], version=item["version"])
    assert value["content"] == original
    assert state.blockers == []
    release = "\n".join(format_release_evidence_lines(state, [{**worker_result, "result_path": str(report)}]))
    assert "BLOCKED" in release and "exit: 1; tests: 0" in release
    assert item["version"] in release and result["result_commit"] in release
