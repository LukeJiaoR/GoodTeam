"""Exact multi-file source/Worker tests, with no provider or live Runtime claim."""
from contextlib import contextmanager
from copy import deepcopy
import subprocess
import sys

import pytest

from goodteam_os.skills import review_execution
from goodteam_os.skills.models import SkillError, canonical_json
from goodteam_os.skills.review_api import task_review_details
from goodteam_os.skills.review_process import ReviewProcess
from goodteam_os.skills.review_results import ReviewResultStore
from goodteam_os.skills.review_sources import capture_git_sources, captured_git_target, source_request, validate_git_sources
from test_skill_content import FixtureAuthority
from test_skill_review_protocol import direct_request
from test_skill_review_results import channel, report
from test_task_skill_entrypoints import bind, capture, method, ctx as ctx


def git(root, *args):
    return subprocess.check_output(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@localhost", *args], cwd=root).decode().strip()


def commits(root):
    git(root, "init", "-q")
    (root / "a.py").write_text("def add(a, b):\n    return a - b\n")
    (root / "deleted.txt").write_text("retired\n")
    (root / "SKILL.md").write_text("SOURCE_DATA_ONLY\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    base = git(root, "rev-parse", "HEAD")
    (root / "a.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "deleted.txt").unlink()
    (root / "new file.txt").write_text("added\n")
    (root / "SKILL.md").write_text("CHANGED_SOURCE_NOT_AN_ACTIVE_METHOD\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "head")
    return {"kind": "git_change", "base_sha": base, "head_sha": git(root, "rev-parse", "HEAD")}


def ready(ctx):
    selected = method(ctx, "METHOD_LOCKED_V1")
    bind(ctx, selected, subject="role:code_review")
    context = capture(ctx, "git-review", subjects=("role:code_review",))
    target = commits(ctx.repo)
    request = direct_request(context, review_target=target)
    return ctx.workers.create_job(**request), context, selected, request


class GitFixture(FixtureAuthority):
    def __init__(self, root, *, malformed=False):
        super().__init__()
        self.root, self.malformed = root, malformed
        self.prompts, self.records = [], []

    @contextmanager
    def execution(self, record, provider, ref):
        stage = self.root / ("git-process-" + str(len(self.records)))
        self.records.append(deepcopy(record))
        prompt = review_execution.materialize_review_inputs(provider, ref, record, stage)
        self.prompts.append(prompt)
        payload = report(evidence=[{"id": "E1", "path": "head/a.py", "line_start": 2, "line_end": 2,
                                    "quote": "    return a + b", "observation": "The exact new expression adds the parameters."}])
        if self.malformed:
            payload["evidence"][0]["path"] = "base/a.py"  # Same filename, WRONG version.
        body = ("import sys\nfrom pathlib import Path\nassert sys.stdin.buffer.read()\n"
                "assert Path('sources/base/a.py').read_text().endswith('    return a - b\\n')\n"
                "assert Path('sources/head/a.py').read_text().endswith('    return a + b\\n')\n"
                "assert Path('sources/base/deleted.txt').read_text() == 'retired\\n'\n"
                "assert not Path('sources/head/deleted.txt').exists()\n"
                "sys.stdout.buffer.write(" + repr(channel(payload, thread=ref["context_id"])) + ")\n")
        yield ReviewProcess((sys.executable, "-I", "-c", body), stage, {}, 5), prompt

    def check_current(self, provider, ref):
        provider.private_input(ref)


def test_exact_commit_sources_ignore_worktree_and_track_add_delete_and_method_data(ctx):
    target = commits(ctx.repo)
    (ctx.repo / "a.py").write_text("WRONG WORKTREE")
    (ctx.repo / "untracked.txt").write_text("NOT SELECTED")
    payload = capture_git_sources(ctx.repo, target)
    assert len(payload["files"]) == 4
    assert payload["sources"]["base/a.py"].endswith("return a - b\n")
    assert payload["sources"]["head/a.py"].endswith("return a + b\n")
    assert "head/deleted.txt" not in payload["sources"]
    assert "base/new file.txt" not in payload["sources"]
    assert "head/untracked.txt" not in payload["sources"]
    assert payload["sources"]["head/SKILL.md"].startswith("CHANGED_SOURCE")


@pytest.mark.parametrize("value", [None, {}, {"kind": "git_change", "base_sha": "main", "head_sha": "HEAD"},
    {"kind": "git_change", "base_sha": "f" * 40, "head_sha": "a" * 40, "root": "/somewhere"}])
def test_source_request_requires_exact_bounded_controller_root(value):
    with pytest.raises(SkillError):
        source_request(value)


@pytest.mark.parametrize("kind", ["symlink", "binary", "non_utf8", "large", "lfs", "submodule"])
def test_unsupported_changed_file_blocks_entire_snapshot(ctx, kind):
    target = commits(ctx.repo)
    path = ctx.repo / "unsupported"
    if kind == "symlink":
        path.symlink_to("/outside-sentinel")
    elif kind == "binary":
        path.write_bytes(b"a\0b")
    elif kind == "non_utf8":
        path.write_bytes(b"\xff")
    elif kind == "large":
        path.write_bytes(b"x" * (256 * 1024 + 1))
    elif kind == "lfs":
        path.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:" + "a" * 64 + "\nsize 12\n")
    else:
        git(ctx.repo, "update-index", "--add", "--cacheinfo", "160000", target["base_sha"], "unsupported")
    if kind != "submodule":
        git(ctx.repo, "add", "unsupported")
    git(ctx.repo, "commit", "-qm", "unsupported")
    target["head_sha"] = git(ctx.repo, "rev-parse", "HEAD")
    with pytest.raises(SkillError) as error:
        capture_git_sources(ctx.repo, target)
    assert error.value.code in {"review_source_limit", "review_source_unsupported"}


def test_no_checkout_filter_hook_external_diff_or_replace_object(ctx, monkeypatch):
    target = commits(ctx.repo)
    marker = ctx.root / "must-not-run"
    script = ctx.root / "hook"
    script.write_text("#!/bin/sh\ntouch " + str(marker) + "\nexit 1\n")
    script.chmod(0o700)
    git(ctx.repo, "config", "core.fsmonitor", str(script))
    git(ctx.repo, "config", "diff.external", str(script))
    git(ctx.repo, "config", "core.hooksPath", str(ctx.root))
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", str(script))
    monkeypatch.setenv("GIT_DIR", str(ctx.root / "wrong"))
    actual = capture_git_sources(ctx.repo, target)
    assert actual["request"] == target
    assert not marker.exists()


def test_worker_captures_before_runtime_and_replays_without_reopening_repository(ctx, monkeypatch):
    job, _context, _selected, request = ready(ctx)
    snapshot = ctx.service.locks.find_snapshot(command_key=job["id"])["snapshot"]
    target, sources = captured_git_target(snapshot)
    assert target["base_sha"] == request["review_target"]["base_sha"]
    assert len(sources) == 6
    def no_recapture(*_args):
        raise AssertionError("Replay must not re-read source Git objects")
    monkeypatch.setattr("goodteam_os.skills.review_sources.capture_git_sources", no_recapture)
    (ctx.repo / ".git").rename(ctx.repo / "gone-git")
    assert ctx.workers.create_job(**request)["id"] == job["id"]
    assert captured_git_target(snapshot) == (target, sources)


def test_subprocess_report_uses_exact_versions_and_api_can_reopen_after_source_changes(ctx, monkeypatch):
    job, context, selected, _request = ready(ctx)
    runtime = GitFixture(ctx.root)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    (ctx.repo / "a.py").write_text("DIFFERENT SOURCE")
    result = ctx.workers.run_job(job["id"])
    assert result["status"] == "review_received", result
    code, detail = task_review_details(ctx.workers, context, job_id=job["id"], include_report=True)
    assert code == 200
    assert detail["target"]["kind"] == "git_change"
    assert detail["report"]["evidence"][0]["path"] == "head/a.py"
    assert detail["methods"][0]["version_id"] == selected["version_id"]
    assert str(ctx.repo) not in canonical_json(detail)
    assert len(runtime.records) == 1
    (ctx.repo / ".git").rename(ctx.repo / "gone-git")
    assert ReviewResultStore(ctx.workers, job["id"]).detail(include_report=True)["report_available"]
    assert ctx.workers.run_job(job["id"])["status"] == "review_received"
    assert len(runtime.records) == 1


def test_wrong_source_side_cannot_form_valid_report(ctx, monkeypatch):
    job, context, *_ = ready(ctx)
    runtime = GitFixture(ctx.root, malformed=True)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    result = ctx.workers.run_job(job["id"])
    assert result["skill_error_code"] == "review_report_source_mismatch"
    code, detail = task_review_details(ctx.workers, context, job_id=job["id"], include_report=True)
    assert code == 409
    assert not detail["execution"]["report_available"]


@pytest.mark.parametrize("mutation", ["bytes", "missing", "unrelated", "version", "duplicate", "range"])
def test_corrupt_source_snapshot_rejected_without_recapture(ctx, mutation):
    target = commits(ctx.repo)
    value = capture_git_sources(ctx.repo, target)
    if mutation == "bytes":
        value["sources"]["head/a.py"] += "changed"
    if mutation == "missing":
        del value["sources"]["base/a.py"]
    if mutation == "unrelated":
        value["sources"]["head/extra"] = "not selected"
    if mutation == "version":
        value["request"]["head_sha"] = "f" * 40
    if mutation == "duplicate":
        value["files"].append(value["files"][0])
    if mutation == "range":
        value["files"][0]["head"]["size"] = -1
    with pytest.raises(SkillError):
        validate_git_sources(value, target)


def test_explicit_http_retry_uses_new_context_original_method_and_source_after_role_edit(ctx, monkeypatch):
    from goodteam_os.state import StateStore, create_initial_state
    from goodteam_os.web import handle_project_api_get, handle_project_api_post

    job, context, selected, _request = ready(ctx)
    state = create_initial_state("git-review", "DEMO", "Review an exact change")
    state.task_skill_context = context
    states = StateStore(ctx.workspace)
    states.write_project_current(state)
    before = states.read_project_current("DEMO")
    runtime = GitFixture(ctx.root, malformed=True)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    assert ctx.workers.run_job(job["id"])["status"] == "failed"
    first = ReviewResultStore(ctx.workers, job["id"]).detail()
    second_method = method(ctx, "METHOD_NEW_V2_NOT_FOR_OLD_TASK", key="new-method")
    bind(ctx, second_method, subject="role:code_review", key="edit-after-failure")
    (ctx.repo / "a.py").write_text("WRONG NEW WORKTREE")
    (ctx.repo / ".git").rename(ctx.repo / "gone-git")
    runtime.malformed = False
    path = "/api/projects/DEMO/runs/git-review/skill-reviews/" + job["id"] + "/retry"
    body = {"snapshot_digest": first["snapshot_ref"]["digest"], "context_digest": first["context_ref"]["digest"]}
    code, response = handle_project_api_post(ctx.workspace, path, body)
    assert code == 200, response
    assert response["status"] == "review_received"
    assert response["job_id"] != job["id"]
    assert response["created"] is True
    retry = ReviewResultStore(ctx.workers, response["job_id"]).detail(include_report=True)
    assert retry["methods"] == first["methods"]
    assert retry["methods"][0]["version_id"] == selected["version_id"]
    assert retry["context_ref"] != first["context_ref"]
    assert retry["runtime"]["session_id"] != first["runtime"]["session_id"]
    assert retry["target"] == first["target"]
    assert retry["task_skill_context"] == context
    assert b"METHOD_NEW_V2_NOT_FOR_OLD_TASK" not in runtime.prompts[-1]
    assert b"METHOD_LOCKED_V1" in runtime.prompts[-1]
    assert len(runtime.records) == 2
    assert handle_project_api_post(ctx.workspace, path, body)[1]["job_id"] == response["job_id"]
    assert len(runtime.records) == 2  # Duplicate POST recovers the SAME retry outcome.
    assert states.read_project_current("DEMO") == before
    assert handle_project_api_get(ctx.workspace, path[:-6] + "/report")[0] == 409  # Old failure is retained.


@pytest.mark.parametrize("mode", ["running", "stopping", "received", "stale", "foreign", "extra", "historical", "revoked"])
def test_retry_rejects_unconfirmed_or_wrong_task_input(ctx, monkeypatch, mode):
    from goodteam_os.skills.content import SkillContextProvider
    from goodteam_os.state import StateStore, create_initial_state
    from goodteam_os.web import handle_project_api_post

    job, context, selected, _request = ready(ctx)
    state = create_initial_state("git-review", "DEMO", "Review")
    state.task_skill_context = context
    states = StateStore(ctx.workspace)
    states.write_project_current(state)
    runtime = GitFixture(ctx.root, malformed=mode != "received")
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    results = ReviewResultStore(ctx.workers, job["id"])
    if mode in {"running", "stopping"}:
        provider = SkillContextProvider(ctx.workers, job["id"], authority=runtime)
        results.begin(provider, provider.prepare("unknown-live-context"))
        results.mark_running()
        if mode == "stopping":
            results.mark_stopping()
        # Produces a protected *failed mirror*, but does NOT confirm termination.
        ctx.workers.run_job(job["id"])
    else:
        ctx.workers.run_job(job["id"])
    detail = results.detail()
    before = len(ctx.workers.list_jobs())
    launches = len(runtime.records)
    path = "/api/projects/DEMO/runs/git-review/skill-reviews/" + job["id"] + "/retry"
    body = {"snapshot_digest": detail["snapshot_ref"]["digest"], "context_digest": detail["context_ref"]["digest"]}
    if mode == "stale":
        body["context_digest"] = "sha256:" + "f" * 64
    elif mode == "foreign":
        path = path.replace(job["id"], "job-skill-" + "f" * 64)
    elif mode == "extra":
        body["model"] = "unapproved-model"
    elif mode == "historical":
        states.write_project_current(create_initial_state("new-task", "DEMO", "Other task"))
    elif mode == "revoked":
        from goodteam_os.skills.worker_requests import _services

        availability = _services(ctx.workers)[3]
        availability.revoke(selected["skill_id"], selected["version_id"], actor="user", reason="safety",
                            command_key="revoke-before-retry", expected_revision=availability.revision())
    code, response = handle_project_api_post(ctx.workspace, path, body)
    assert code in {404, 409}, response
    assert len(ctx.workers.list_jobs()) == before
    assert len(runtime.records) == launches


def test_cli_selects_explicit_git_pair_without_a_new_runtime(tmp_path, monkeypatch, capsys):
    from goodteam_os import cli

    captured = []
    monkeypatch.setattr(cli, "dispatch_tracks", lambda **kw: captured.append(kw) or {"all_ok": False})
    assert cli.main(["dispatch", "--project-root", str(tmp_path), "--track", "code_review:codex:Review this change",
                     "--review-base", "a" * 40, "--review-head", "b" * 40, "--task-id", "bounded-review"]) == 1
    assert captured[0]["tracks"][0]["review_target"] == {"kind": "git_change", "base_sha": "a" * 40, "head_sha": "b" * 40}
    assert captured[0]["logical_task_id"] == "bounded-review"


def test_direct_dispatch_captures_git_pair_and_does_not_run_unverified_runtime(ctx, monkeypatch):
    from goodteam_os.dispatch import dispatch_tracks
    from test_task_skill_entrypoints import forbid_runtime

    selected = method(ctx)
    bind(ctx, selected, subject="role:code_review")
    target = commits(ctx.repo)
    calls = forbid_runtime(monkeypatch)
    result = dispatch_tracks(project_root=ctx.repo, tracks=[{"role": "code_review", "adapter": "codex", "task": "Review",
                             "review_target": target}], projects_dir=ctx.workspace / "projects", allow_unsafe=True,
                             logical_task_id="direct-git")
    assert not result["all_ok"]
    original = ctx.service.locks.find_snapshot(command_key=ctx.workers.list_jobs()[0]["id"])["snapshot"]
    assert captured_git_target(original)[0]["head_sha"] == target["head_sha"]
    assert calls == []


def test_task_transition_lock_rejects_retry_without_creating_a_job(ctx, monkeypatch):
    from goodteam_os.state import StateStore, create_initial_state
    from goodteam_os.web import handle_project_api_post
    from goodteam_os.workflow_mutations import workflow_guard

    job, context, *_ = ready(ctx)
    state = create_initial_state("git-review", "DEMO", "Review")
    state.task_skill_context = context
    StateStore(ctx.workspace).write_project_current(state)
    runtime = GitFixture(ctx.root, malformed=True)
    monkeypatch.setattr(review_execution, "selected_review_runtime", lambda *_: runtime)
    ctx.workers.run_job(job["id"])
    detail = ReviewResultStore(ctx.workers, job["id"]).detail()
    path = "/api/projects/DEMO/runs/git-review/skill-reviews/" + job["id"] + "/retry"
    body = {"snapshot_digest": detail["snapshot_ref"]["digest"], "context_digest": detail["context_ref"]["digest"]}
    with workflow_guard(ctx.workspace, "DEMO"):
        code, payload = handle_project_api_post(ctx.workspace, path, body)
    assert code == 409 and payload["error"] == "review_retry_busy"
    assert len(ctx.workers.list_jobs()) == 1
    assert len(runtime.records) == 1


def test_rename_retains_both_sides_as_add_delete_and_mode_changes(ctx):
    target = commits(ctx.repo)
    (ctx.repo / "a.py").rename(ctx.repo / "renamed.py")
    (ctx.repo / "SKILL.md").chmod(0o755)
    git(ctx.repo, "add", ".")
    git(ctx.repo, "commit", "-qm", "rename and executable mode")
    target = {"kind": "git_change", "base_sha": target["head_sha"], "head_sha": git(ctx.repo, "rev-parse", "HEAD")}
    payload = capture_git_sources(ctx.repo, target)
    entries = {item["path"]: item for item in payload["files"]}
    assert entries["a.py"]["change"] == "D"
    assert entries["renamed.py"]["change"] == "A"
    assert payload["sources"]["base/a.py"] == payload["sources"]["head/renamed.py"]
    assert entries["SKILL.md"]["base"]["mode"] == "100644"
    assert entries["SKILL.md"]["head"]["mode"] == "100755"


def test_submodule_cannot_be_hidden_by_repo_diff_configuration(ctx):
    target = commits(ctx.repo)
    git(ctx.repo, "config", "diff.ignoreSubmodules", "all")
    git(ctx.repo, "update-index", "--add", "--cacheinfo", "160000", target["base_sha"], "submodule")
    git(ctx.repo, "commit", "-qm", "gitlink")
    target["head_sha"] = git(ctx.repo, "rev-parse", "HEAD")
    with pytest.raises(SkillError, match="Symlink, submodule"):
        capture_git_sources(ctx.repo, target)
