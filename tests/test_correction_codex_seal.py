"""Command contract and actual offline host sealing, never live Codex."""
import json
import os
import subprocess

import pytest

from goodteam_os.execution.correction_codex import OUTPUT_SCHEMA, admit_output, correction_prompt, proposed_command
from goodteam_os.execution.correction_input import correction_runtime_capability
from goodteam_os.execution.correction_seal import seal_stopped_candidate, _Objects
from goodteam_os.execution.correction_store import CorrectionAttemptStore
from goodteam_os.execution.task_snapshot import TaskInputError
from goodteam_os.skills.models import SkillError
from goodteam_os.memory_storage import MemoryStorageError
from test_correction_worker import worker_context as worker_fixture, prepare  # noqa: F401
from test_task_input_snapshot import ctx  # noqa: F401
from test_candidate_validation import git


def channel(value, **changes):
    body = {"schema_version": OUTPUT_SCHEMA, "input": value.reference,
            "base_sha": value.to_dict()["candidate"]["head_sha"], "summary": "Edited existing source; needs independent recheck."}
    body.update(changes)
    return b"\n".join(json.dumps(event).encode() for event in (
        {"type": "thread.started", "thread_id": "offline-fixture"}, {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(body)}},
        {"type": "turn.completed"})) + b"\n"


@pytest.fixture
def prepared(request):
    c = request.getfixturevalue("worker_fixture")
    job = prepare(c)["job_id"]
    c.authority = CorrectionAttemptStore(c.project, job)
    c.value = c.authority.load()
    c.worktree = c.authority.workspace(c.value)
    c.output = channel(c.value)
    # The fixture alone supplies this real offline process. It edits no Git
    # metadata; the production Codex admission function is never changed in code.
    c.script.write_text("import sys\nfrom pathlib import Path\nsys.stdin.read()\n"
                       "Path('feature.py').write_bytes(b\"VALUE = 'fixed'\\r\\n\")\n"
                       f"sys.stdout.buffer.write({c.output!r})\n")
    return c


def returned(c):
    result = c.service.run(c.execution_id, c.value.job_id, acknowledge_external_cost=True)
    assert result["status"] == "completed"
    return c


def test_command_recipe_is_unactivated_version_scoped_and_does_not_inherit_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-do-not-inherit")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "99")
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: pytest.fail("Recipe launched a process"))
    spec = proposed_command(binary="/opt/codex", home="/home/owner/.codex", workspace=tmp_path, model="gpt-5.4", version="0.159.2")
    assert spec["available"] is False and spec["sandbox_confinement"] == "unverified"
    assert "workspace-write" in spec["command"] and "never" in spec["command"]
    assert not any("bypass" in arg or arg in {"--full-auto", "--add-dir", "--resume"} for arg in spec["command"])
    assert "synthetic-do-not-inherit" not in repr(spec) and "GIT_CONFIG_COUNT" not in spec["environment"]
    for changes in ({"version": "0.160.0"}, {"model": "--unsafe"}, {"home": str(tmp_path / ".codex")}, {"binary": "codex"}):
        args = dict(binary="/opt/codex", home="/home/owner/.codex", workspace=tmp_path, model="gpt-5.4", version="0.159.2")
        args.update(changes)
        with pytest.raises(TaskInputError):
            proposed_command(**args)


def test_edit_only_prompt_preserves_original_contract_and_complete_feedback(prepared):
    text = correction_prompt(prepared.authority)
    assert "OLD_CONDITION" in text and "Fix VALUE" in text and "Complete Retained Correction Feedback" in text
    assert "Do not commit, stage, or write Git metadata" in text and "Commit a clean candidate there" not in text
    assert "Commit a clean candidate there" in prepared.value.render()


def test_host_seals_exact_stopped_attempt_without_touching_original_index(prepared):
    c = prepared
    original_index = (c.project / ".git" / "index").read_bytes()
    returned(c)
    assert git(c.worktree, "rev-parse", "HEAD") == c.head
    sealed = seal_stopped_candidate(c.authority)
    head = sealed["candidate"]["head_sha"]
    assert head != c.head and git(c.worktree, "rev-parse", "HEAD^") == c.head
    assert sealed["acceptance_status"] == "unknown"
    assert sealed["binding"]["input"] == c.value.reference
    assert seal_stopped_candidate(c.authority) == sealed
    assert git(c.project, "rev-parse", "HEAD") == c.head
    assert (c.project / ".git" / "index").read_bytes() == original_index
    assert (c.project / "feature.py").read_text() == "VALUE = 'original'\n"
    assert subprocess.check_output(["git", "show", head + ":feature.py"], cwd=c.project) == b"VALUE = 'fixed'\r\n"
    checked = c.validation.validate(c.execution_id, operation_id="sealed-check", head_sha=head,
        argv=c.argv, criteria=[0], correction_worker=c.value.job_id)
    assert checked["observation"]["status"] == "passed"


@pytest.mark.parametrize("mutation", ["ignored", "symlink", "hardlink", "fifo", "index", "head", "output"])
def test_unsafe_or_mismatched_candidate_never_changes_attempt_ref(prepared, mutation):
    c = returned(prepared)
    if mutation == "ignored":
        (c.worktree / ".goodteam").mkdir()
        (c.worktree / ".goodteam" / "hidden").write_text("ignored")
    elif mutation in {"symlink", "hardlink", "fifo"}:
        (c.worktree / "feature.py").unlink()
        if mutation == "symlink":
            (c.worktree / "feature.py").symlink_to(c.project / "feature.py")
        elif mutation == "hardlink":
            os.link(c.project / "feature.py", c.worktree / "feature.py")
        else:
            os.mkfifo(c.worktree / "feature.py")
    elif mutation == "index":
        git(c.worktree, "add", "feature.py")
    elif mutation == "head":
        git(c.worktree, "commit", "--allow-empty", "-qm", "Unadmitted head")
    else:
        (c.service.workers.root / c.value.job_id / "result.md").write_text("Different output")
    before = git(c.worktree, "rev-parse", "HEAD")
    with pytest.raises((TaskInputError, SkillError, OSError, MemoryStorageError)):
        seal_stopped_candidate(c.authority)
    assert git(c.worktree, "rev-parse", "HEAD") == before
    assert not (c.authority.root / "seal-result.json").exists()


@pytest.mark.parametrize("missing", ["run-started.json", "dispatch.json", "host-return.json", "run-result.json"])
def test_incomplete_return_chain_cannot_seal(prepared, missing):
    c = returned(prepared)
    (c.authority.root / missing).unlink()
    with pytest.raises(TaskInputError):
        seal_stopped_candidate(c.authority)
    assert git(c.worktree, "rev-parse", "HEAD") == c.head


def test_nontermination_cannot_produce_sealable_outcome(prepared, monkeypatch):
    c = prepared
    import goodteam_os.execution.candidate_process as process
    original = process._terminate
    def unconfirmed(child):
        original(child)
        raise SkillError("review_termination_unknown", "Offline injected failure")
    monkeypatch.setattr(process, "_terminate", unconfirmed)
    with pytest.raises(ValueError):
        c.service.run(c.execution_id, c.value.job_id, acknowledge_external_cost=True)
    with pytest.raises(TaskInputError):
        seal_stopped_candidate(c.authority)
    assert not (c.authority.root / "host-return.json").exists()


@pytest.mark.parametrize("stage", ["commit-tree", "update-ref", "read-tree"])
def test_interrupted_seal_never_retries_or_claims_success(prepared, monkeypatch, stage):
    c = returned(prepared)
    (c.worktree / "new.py").write_bytes(b"new source\n")
    original = _Objects.write
    def interrupted(self, *args, **kwargs):
        if args[0] == stage:
            raise OSError("Offline interruption")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(_Objects, "write", interrupted)
    with pytest.raises(MemoryStorageError):
        seal_stopped_candidate(c.authority)
    monkeypatch.setattr(_Objects, "write", original)
    with pytest.raises(TaskInputError, match="outcome_unknown"):
        seal_stopped_candidate(c.authority)
    assert not (c.authority.root / "seal-result.json").exists()
    assert git(c.project, "rev-parse", "HEAD") == c.head


def test_noop_and_changed_output_identity_are_not_candidates(prepared):
    c = returned(prepared)
    (c.worktree / "feature.py").write_text("VALUE = 'original'\n")
    with pytest.raises(TaskInputError, match="no_progress"):
        seal_stopped_candidate(c.authority)
    for changes in ({"base_sha": "0" * 40}, {"input": {}}, {"candidate_sha": c.head}, {"summary": ""}):
        with pytest.raises(TaskInputError):
            admit_output(channel(c.value, **changes), c.value)


def test_hook_filter_and_fsmonitor_never_run(prepared):
    c = returned(prepared)
    (c.worktree / "new.py").write_bytes(b"new raw source\r\n")
    sentinel = c.workspace / "forbidden-hook"
    hook = c.project / ".git" / "hooks" / "post-commit"
    hook.write_text(f"#!/bin/sh\ntouch '{sentinel}'\n")
    hook.chmod(0o755)
    reference_hook = hook.with_name("reference-transaction")
    reference_hook.write_bytes(hook.read_bytes())
    reference_hook.chmod(0o755)
    git(c.project, "config", "core.fsmonitor", str(hook))
    git(c.project, "config", "filter.evil.clean", f"touch '{sentinel}'")
    (c.project / ".git" / "info" / "attributes").write_text("*.py filter=evil\n")
    assert seal_stopped_candidate(c.authority)["candidate"]["head_sha"] != c.head
    assert not sentinel.exists()


def test_production_admission_remains_closed():
    for adapter in ("codex", "claude", "pi", "gemini"):
        assert correction_runtime_capability(adapter) == {
            "available": False, "status": "blocked", "reason": "correction_verified_runtime_unavailable"}
    # Optional typed native composition is tested separately; default readiness
    # never becomes a global Codex grant merely because dispatch is implemented.


def test_source_moves_during_seal_before_ref_write(prepared, monkeypatch):
    c = returned(prepared)
    original = _Objects.write
    def move(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if args[0] == "commit-tree":
            (c.worktree / "feature.py").write_text("VALUE = 'moved'\n")
        return result
    monkeypatch.setattr(_Objects, "write", move)
    with pytest.raises(TaskInputError, match="source_changed"):
        seal_stopped_candidate(c.authority)
    assert git(c.worktree, "rev-parse", "HEAD") == c.head
    assert not (c.authority.root / "seal-result.json").exists()


def test_ref_compare_and_swap_does_not_overwrite_moved_branch(prepared, monkeypatch):
    c = returned(prepared)
    (c.worktree / "new.py").write_bytes(b"new source\n")
    other = git(c.project, "commit-tree", c.value.to_dict()["candidate"]["tree_sha"], "-p", c.head, "-m", "Other host commit")
    original = _Objects.write
    def move(self, *args, **kwargs):
        if args[0] == "update-ref":
            git(c.worktree, "update-ref", "refs/heads/sandbox/" + c.value.job_id, other)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(_Objects, "write", move)
    with pytest.raises(TaskInputError, match="git_failed"):
        seal_stopped_candidate(c.authority)
    assert git(c.worktree, "rev-parse", "HEAD") == other
    assert git(c.project, "rev-parse", "HEAD") == c.head
    assert not (c.authority.root / "seal-result.json").exists()


def test_git_host_plumbing_bounds_output_and_owns_cleanup(tmp_path):
    git(tmp_path, "init", "-q")
    helper = tmp_path / "oversized-git"
    helper.write_text("#!/usr/bin/python3\nimport sys\nsys.stdout.write('x' * 100000)\n")
    helper.chmod(0o755)
    objects = _Objects(tmp_path)
    objects.binary = str(helper)  # Internal test-only executable replacement.
    with pytest.raises(TaskInputError, match="git_failed"):
        objects.write("mktree")


@pytest.mark.parametrize("content", [None, b"\x00\x01raw\r\n\xff"])
def test_tracked_deletion_and_binary_bytes_are_not_filtered(prepared, content):
    c = returned(prepared)
    path = c.worktree / "feature.py"
    if content is None:
        path.unlink()
    else:
        path.write_bytes(content)
    sealed = seal_stopped_candidate(c.authority)
    head = sealed["candidate"]["head_sha"]
    if content is None:
        assert "feature.py" not in git(c.worktree, "ls-tree", "-r", "--name-only", head).splitlines()
    else:
        assert subprocess.check_output(["git", "show", head + ":feature.py"], cwd=c.project) == content


def test_raw_tree_nested_paths_and_modes(tmp_path):
    from goodteam_os.execution.correction_seal import _tree
    git(tmp_path, "init", "-q")
    objects = _Objects(tmp_path)
    tree = _tree(objects, {"dir/tool.py": ("100755", b"run\r\n"), "dir.txt": ("100644", b"text")})
    assert objects.read("cat-file", "blob", tree + ":dir/tool.py") == b"run\r\n"
    assert "100755 blob" in git(tmp_path, "ls-tree", "-r", tree)


def test_completed_replay_rejects_new_ignored_files(prepared):
    c = returned(prepared)
    sealed = seal_stopped_candidate(c.authority)
    (c.worktree / ".goodteam").mkdir()
    (c.worktree / ".goodteam" / "ignored").write_text("not admitted")
    with pytest.raises(TaskInputError, match="untracked"):
        seal_stopped_candidate(c.authority)
    assert git(c.worktree, "rev-parse", "HEAD") == sealed["candidate"]["head_sha"]
