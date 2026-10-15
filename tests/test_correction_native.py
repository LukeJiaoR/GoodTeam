"""Typed native-path contract fixtures, actual offline process/Git, no Codex call."""
from pathlib import Path
import os
import shutil
import time

import pytest

from goodteam_os.execution.automatic_review import AutomaticCandidateReviewService
from goodteam_os.execution.candidate_validation import _read, _write
from goodteam_os.execution.correction_input import correction_runtime_capability
from goodteam_os.execution.correction_loop import CorrectionLoopService
from goodteam_os.execution.correction_native import POLICY_SCHEMA, TrustedCodexCorrectionAdmission
from goodteam_os.execution.correction_store import CorrectionAttemptStore
from goodteam_os.execution.correction_worker import CorrectionWorkerService
from goodteam_os.execution.task_snapshot import TaskInputError, canonical_bytes
from goodteam_os.skills.codex_local import _binary_digest
from goodteam_os.workers import WorkerJobStore
from test_automatic_candidate_review import automatic as automatic_fixture  # noqa: F401
from test_correction_codex_seal import channel
from test_candidate_validation import git
from test_task_input_snapshot import ctx  # noqa: F401


class OfflineNative(TrustedCodexCorrectionAdmission):
    """Only this test subclass substitutes an offline Python command/version."""
    verification_kind = "contract_fixture"

    def __init__(self, policy_path, script):
        self.script = script
        super().__init__(policy_path)

    def _probe_version(self):
        pass

    def command(self):
        selected = super().command()
        selected["command"] = (str(self.binary), "-I", str(self.script))
        return selected


@pytest.fixture
def native(request, monkeypatch):
    c = request.getfixturevalue("automatic_fixture")
    # Real controller/Reviewer fixture establishes exact feedback and prepares
    # the attempt while default production capability still rejects dispatch.
    blocked = c.auto.advance(c.execution_id, acknowledge_external_cost=True)
    assert blocked["reason"] == "correction_verified_runtime_unavailable"
    c.job_id = CorrectionLoopService(c.project).evidence(c.execution_id)["worker_job_id"]
    c.authority = CorrectionAttemptStore(c.project, c.job_id)
    c.value = c.authority.load()
    c.worktree = c.authority.workspace(c.value)
    c.home = c.workspace / "native-home"
    c.home.mkdir(mode=0o700)
    for name in ("auth.json", "config.toml"):
        path = c.home / name
        path.write_text("offline fixture, not a credential")
        path.chmod(0o600)
    c.native_script = c.workspace / "native-fixture.py"
    c.native_script.write_text("import sys\nfrom pathlib import Path\nprompt=sys.stdin.read()\n"
        "assert 'OLD_CONDITION' in prompt and 'Complete Retained Correction Feedback' in prompt\n"
        "assert 'Do not commit, stage, or write Git metadata' in prompt\n"
        "assert 'Commit a clean candidate there' not in prompt\n"
        f"Path({str(c.effects)!r}).open('a').write('call\\n')\n"
        "Path('feature.py').write_text(\"VALUE = 'fixed'\\n\")\n"
        f"sys.stdout.buffer.write({channel(c.value)!r})\n")
    # The VM's installed interpreter is owned by a container image UID; use
    # an owned copy solely for this authority fixture, never relax production.
    binary = c.workspace / "offline-python"
    shutil.copyfile(Path("/usr/bin/python3").resolve(), binary)
    binary.chmod(0o700)
    c.policy = {"schema_version": POLICY_SCHEMA, "project_root": str(c.project), "input": c.value.reference,
        "workspace": str(c.worktree), "codex": str(binary), "codex_home": str(c.home), "version": "0.159.2",
        "binary_digest": _binary_digest(binary), "model": "offline-native-model", "expires_at": int(time.time()) + 300,
        "max_seconds": 5, "max_invocations": 1, "trusted_task_inputs": True,
        "accept_native_managed_configuration": True, "accept_unknown_cost": True,
        "provider_request_limit": None, "usd_limit": None}
    c.policy_path = c.workspace / "correction-policy.json"
    c.policy_path.write_bytes(canonical_bytes(c.policy))
    c.policy_path.chmod(0o600)
    c.admission = OfflineNative(c.policy_path, c.native_script)
    c.service = CorrectionWorkerService(c.project, codex_correction_admission=c.admission)
    # All legacy paths are forbidden, rather than merely replaced by a mock fix.
    monkeypatch.setattr(WorkerJobStore, "_run_cli_backend", lambda *_: pytest.fail("Legacy correction dispatch"))
    monkeypatch.setattr(WorkerJobStore, "_worker_env", lambda *_: pytest.fail("Credential projection"))
    return c


def run(c):
    return c.service.run(c.execution_id, c.job_id, acknowledge_external_cost=True)


def test_typed_dispatch_seal_original_check_and_new_head_review(native):
    c = native
    original_index = (c.project / ".git" / "index").read_bytes()
    result = AutomaticCandidateReviewService(c.project, codex_correction_admission=c.admission).advance(
        c.execution_id, acknowledge_external_cost=True)
    assert result["status"] == "reviewed", result
    assert c.effects.read_text() == "call\n" and len(c.runtime.records) == 2
    sealed = c.authority.sealed_candidate(c.value)
    assert result["review"]["candidate"] == sealed and sealed["head_sha"] != c.head
    assert result["review"]["base_sha"] == c.base
    assert c.runtime.records[-1]["target"]["head_sha"] == sealed["head_sha"]
    assert git(c.project, "rev-parse", "HEAD") == c.head
    assert (c.project / ".git" / "index").read_bytes() == original_index
    binding = c.authority.native_binding(c.value)
    assert binding["runtime_verification"] == "contract_fixture" and binding["sandbox_confinement_verified"] is False
    assert correction_runtime_capability("codex")["available"] is False
    c.policy_path.unlink()  # Completed read/replay requires no fresh native grant.
    assert AutomaticCandidateReviewService(c.project).advance(c.execution_id)["status"] == "reviewed"
    assert c.effects.read_text() == "call\n" and len(c.runtime.records) == 2


def test_missing_acknowledgement_or_direct_worker_cannot_dispatch(native):
    c = native
    with pytest.raises(TaskInputError, match="authorization_required"):
        c.service.run(c.execution_id, c.job_id)
    with pytest.raises(TaskInputError, match="authorization_required"):
        c.service.workers.run_job(c.job_id)
    assert not c.effects.exists() and not (c.authority.root / "run-started.json").exists()


@pytest.mark.parametrize("changes", [
    {"version": "0.160.0"}, {"max_invocations": 2}, {"max_invocations": True},
    {"max_seconds": 601}, {"expires_at": 0}, {"usd_limit": 1}, {"provider_request_limit": 1},
    {"accept_native_managed_configuration": False}, {"accept_unknown_cost": False},
    {"binary_digest": "sha256:" + "0" * 64}, {"model": "--unsafe"},
])
def test_invalid_policy_never_dispatches(native, changes):
    c = native
    c.policy_path.write_bytes(canonical_bytes({**c.policy, **changes}))
    with pytest.raises(TaskInputError):
        OfflineNative(c.policy_path, c.native_script)
    assert not c.effects.exists()


@pytest.mark.parametrize("mutation", ["input", "workspace", "budget", "policy", "fork"])
def test_exact_attempt_authority_rejects_drift(native, mutation, monkeypatch):
    c = native
    if mutation in {"input", "workspace", "budget"}:
        changes = {"input": {**c.value.reference, "payload_digest": "0" * 64}} if mutation == "input" else (
            {"workspace": str(c.project)} if mutation == "workspace" else {"max_seconds": 31})
        c.policy_path.write_bytes(canonical_bytes({**c.policy, **changes}))
        c.service = CorrectionWorkerService(c.project, codex_correction_admission=OfflineNative(c.policy_path, c.native_script))
    elif mutation == "policy":
        c.policy_path.write_bytes(canonical_bytes({**c.policy, "expires_at": c.policy["expires_at"] + 1}))
    else:
        monkeypatch.setattr("goodteam_os.execution.correction_native.os.getpid", lambda: -1)
    with pytest.raises(TaskInputError):
        run(c)
    assert not c.effects.exists() and not (c.authority.root / "run-started.json").exists()


def test_revocation_between_dispatch_marker_and_spawn_blocks_child(native, monkeypatch):
    c = native
    original = c.service.workers._input_receipt
    def revoke(*args, **kwargs):
        receipt = original(*args, **kwargs)
        if kwargs["stage"] == "transport_attempt":
            c.policy_path.unlink()
        return receipt
    monkeypatch.setattr(c.service.workers, "_input_receipt", revoke)
    with pytest.raises((TaskInputError, ValueError)):
        run(c)
    assert not c.effects.exists() and not (c.authority.root / "host-return.json").exists()
    with pytest.raises(TaskInputError, match="outcome_unknown"):
        run(c)


@pytest.mark.parametrize("missing", ["native-runtime-required.json", "authorization.json", "seal-result.json"])
def test_validation_cannot_lose_native_or_seal_proof(native, missing):
    c = native
    result = run(c)
    head = result["candidate"]["head_sha"]
    (c.authority.root / missing).unlink()
    before = set(c.authority.execution_root.glob("validation-*.json"))
    with pytest.raises(TaskInputError):
        c.validation.validate(c.execution_id, operation_id="bad-proof", head_sha=head,
            argv=c.argv, criteria=[0, 1], correction_worker=c.job_id)
    assert set(c.authority.execution_root.glob("validation-*.json")) == before
    assert c.effects.read_text() == "call\n"


def test_completed_return_can_resume_seal_without_runtime_admission(native, monkeypatch):
    c = native
    original = c.service.finalize
    monkeypatch.setattr(c.service, "finalize", lambda *_: (_ for _ in ()).throw(TaskInputError("interrupted_before_seal")))
    with pytest.raises(TaskInputError, match="interrupted_before_seal"):
        run(c)
    assert _read(c.authority.root / "run-result.json")["status"] == "completed"
    assert not (c.authority.root / "seal-started.json").exists()
    c.policy_path.unlink()
    monkeypatch.setattr(c.service, "finalize", original)
    resumed = AutomaticCandidateReviewService(c.project).advance(c.execution_id)
    assert resumed["status"] == "reviewed", resumed
    assert c.effects.read_text() == "call\n" and len(c.runtime.records) == 2


def test_foreign_clean_commit_cannot_replace_host_sealed_candidate(native):
    c = native
    result = run(c)
    git(c.worktree, "commit", "--allow-empty", "-qm", "Unadmitted clean commit")
    head = git(c.worktree, "rev-parse", "HEAD")
    assert head != result["candidate"]["head_sha"]
    with pytest.raises(TaskInputError):
        c.validation.validate(c.execution_id, operation_id="foreign", head_sha=head,
            argv=c.argv, criteria=[0, 1], correction_worker=c.job_id)


def test_configuration_or_reviewer_object_is_not_correction_authority(tmp_path):
    for value in ({"available": True}, object()):
        with pytest.raises(TaskInputError, match="native_authority_invalid"):
            CorrectionWorkerService(tmp_path, codex_correction_admission=value)


def test_special_executable_rejected_without_opening_a_blocking_stream(native):
    c = native
    fifo = c.workspace / "not-an-executable"
    os.mkfifo(fifo)
    c.policy_path.write_bytes(canonical_bytes({**c.policy, "codex": str(fifo)}))
    with pytest.raises(TaskInputError, match="binary_unsupported"):
        OfflineNative(c.policy_path, c.native_script)


def test_native_transport_enforces_policy_timeout_and_never_retries(native, monkeypatch):
    c = native
    c.native_script.write_text("import sys,time\nfrom pathlib import Path\nsys.stdin.read()\n"
        f"Path({str(c.effects)!r}).open('a').write('call\\n')\ntime.sleep(5)\n")
    c.policy_path.write_bytes(canonical_bytes({**c.policy, "max_seconds": 1}))
    admission = OfflineNative(c.policy_path, c.native_script)
    c.service = CorrectionWorkerService(c.project, codex_correction_admission=admission)
    original = c.service.workers._run_cli_worker
    # Even an internal caller requesting a larger timeout cannot expand policy.
    def oversized(*args, **kwargs):
        kwargs["timeout_seconds"] = 30
        return original(*args, **kwargs)
    monkeypatch.setattr(c.service.workers, "_run_cli_worker", oversized)
    result = run(c)
    assert result["status"] == "failed" and result["candidate"] is None
    assert c.effects.read_text() == "call\n"
    assert run(c)["status"] == "failed" and c.effects.read_text() == "call\n"
    assert not (c.authority.root / "seal-started.json").exists()


def test_policy_revocation_while_child_runs_leaves_unknown_after_cleanup(native, monkeypatch):
    c = native
    c.native_script.write_text("import sys,time\nfrom pathlib import Path\nsys.stdin.read()\n"
        f"Path({str(c.effects)!r}).write_text('started')\ntime.sleep(20)\n")
    original = c.admission.check
    def revoke(authority, value):
        if c.effects.exists():
            c.policy_path.unlink(missing_ok=True)
        return original(authority, value)
    monkeypatch.setattr(c.admission, "check", revoke)
    with pytest.raises(ValueError):
        run(c)
    assert c.effects.read_text() == "started"
    assert not (c.authority.root / "host-return.json").exists()
    assert not (c.authority.root / "seal-started.json").exists()


def test_completed_check_replay_still_requires_exact_native_seal(native):
    c = native
    head = run(c)["candidate"]["head_sha"]
    request = dict(operation_id="native-check", head_sha=head, argv=c.argv, criteria=[0, 1], correction_worker=c.job_id)
    checked = c.validation.validate(c.execution_id, **request)
    assert checked["observation"]["status"] == "passed"
    (c.authority.root / "seal-result.json").unlink()
    with pytest.raises(TaskInputError, match="seal_required"):
        c.validation.validate(c.execution_id, **request)


def test_native_authority_cannot_upgrade_original_read_only_execution(native):
    c = native
    path = c.authority.execution_root / "job-ready.json"
    ready = _read(path)
    ready["request"]["execution_policy"] = "read_only"
    _write(path, ready)
    with pytest.raises(TaskInputError, match="execution_policy_unsupported"):
        run(c)
    assert not c.effects.exists() and not (c.authority.root / "run-started.json").exists()
