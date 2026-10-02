"""Basic HTTP review loop with an explicitly model-free native-shaped child."""
from concurrent.futures import ThreadPoolExecutor
import json
import sys
import time
import urllib.request

import pytest

from goodteam_os import cli, service
from goodteam_os.skills.codex_trusted import POLICY_SCHEMA, TrustedCodexReviewAdmission
from goodteam_os.skills.models import SkillError
from goodteam_os.skills.review_results import ReviewResultStore
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.workers import WorkerJobStore
from test_codex_local import ready
from test_skill_review_http import post, server_url
from test_skill_review_results import SOURCE, channel
from test_task_skill_entrypoints import bind, capture, method, ctx as ctx


class FixtureTrustedAdmission(TrustedCodexReviewAdmission):
    verification_kind = "contract_fixture"


def native(ctx, *, body="", malformed=False):
    home = ctx.root / "original-native-home"
    home.mkdir()
    (home / "config.toml").write_text('model_provider="native-managed-fixture"\n[mcp_servers.existing]\ncommand="test-only"\n')
    (home / "auth.json").write_text('MODEL_FREE_LOGIN_MUST_STAY_IN_ORIGINAL_HOME')
    for name in ("config.toml", "auth.json"):
        (home / name).chmod(0o600)
    binary = ctx.root / "trusted-native-shaped-fixture"
    binary.write_text("#!" + sys.executable + "\n# goodteam-model-free-trusted-review-fixture\n"
        "import os,sys,pathlib,time\n"
        "if '--version' in sys.argv:\n print('codex-cli 0.160.0');sys.exit(0)\n"
        "assert sys.argv[1]=='exec' and '--json' in sys.argv and '--ephemeral' in sys.argv\n"
        "assert sys.argv[sys.argv.index('--sandbox')+1]=='read-only'\n"
        "assert '--ignore-user-config' not in sys.argv and '--ignore-rules' not in sys.argv\n"
        "assert '--dangerously-bypass-approvals-and-sandbox' not in sys.argv\n"
        "assert '-c' not in sys.argv and '--config' not in sys.argv\n"
        "original=pathlib.Path(" + repr(str(home)) + ")\n"
        "assert os.environ['CODEX_HOME']==str(original)\n"
        "assert (original/'auth.json').read_text()=='MODEL_FREE_LOGIN_MUST_STAY_IN_ORIGINAL_HOME'\n"
        "assert 'existing' in (original/'config.toml').read_text()\n"
        "assert 'UNRELATED_CREDENTIAL' not in os.environ and 'OPENAI_API_KEY' not in os.environ\n"
        "prompt=sys.stdin.buffer.read()\nassert b'candidate' in prompt\n"
        "assert b'MODEL_FREE_LOGIN' not in prompt\n"
        + body + "\nsys.stdout.buffer.write(" + repr(channel({"invalid": True}) if malformed else channel()) + ")\n")
    binary.chmod(0o700)
    return binary, home


def policy(ctx, host, context, **changes):
    binary, home = host
    value = {"schema_version": POLICY_SCHEMA, "authorization_id": "trusted-native-task", "worker_root": str(ctx.repo),
        "task": context["task"], "codex": str(binary), "codex_home": str(home), "model": "explicit-test-model",
        "max_contexts": 1, "deadline_seconds": 5, "expires_at": int(time.time()) + 3600,
        "trusted_task_inputs": True, "accept_native_managed_configuration": True, "accept_unknown_cost": True,
        "provider_request_limit": None, "usd_limit": None, **changes}
    path = ctx.root / "trusted-native-task-policy.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as response:
        return response.status, json.loads(response.read())


def default_page(ctx, monkeypatch, *, task="default-native-review"):
    """Use the actual default workflow/gate producer, without a seeded lock."""
    from goodteam_os import team_control_plane
    from goodteam_os.graph import dispatch_gate_reviewer_jobs

    assert capture(ctx, task) == {}
    current = ctx.service.team.read()
    ctx.service.team.execute([{"kind": "change_worker_runtime", "worker_id": "code_review-a",
                              "provider": "codex", "model": "explicit-test-model"}],
        actor="user", surface="team_ui", command_key="native-fixture-selection", expected_revision=current["revision"])
    # Readiness metadata cannot launch the fixture/model or grant execution.
    monkeypatch.setattr(team_control_plane, "worker_backend_doctor", lambda _mode: {"ready": True})
    ctx.config.write_text(ctx.config.read_text() + "\nreviewer_profiles:\n  implementation:\n    - correctness-security\n"
        "reviewer_dispatch:\n  mode: parallel\n  phases:\n    implementation: parallel\n"
        "  rollout:\n    enabled: true\n    acknowledge: parallel-reviewer-dispatch-is-advisory\n")
    candidate = ctx.root / "default-candidate.py"
    candidate.write_bytes(SOURCE)
    state = create_initial_state(task, "DEMO", "Read the exact supplied candidate and report findings.")
    state.current_node, state.next_node = "security_review_gate", "await_security_approval"
    state.artifacts["rfc"] = str(candidate)
    StateStore(ctx.workspace).write_project_current(state)
    queued = dispatch_gate_reviewer_jobs(state=state, project_config=ctx.config, approval="security",
                                        artifact_key="rfc", artifact_path=candidate)
    assert len(queued) == 1 and not queued[0].get("task_skill_context")
    return StateStore(ctx.workspace).read_project_current("DEMO"), {"task": {"kind": "direct_task", "project_key": "DEMO", "logical_id": task}}


def test_default_page_run_captures_only_review_receives_report_and_reopens_after_restart(ctx, monkeypatch):
    host = native(ctx)
    before, scope = default_page(ctx, monkeypatch)
    admission = FixtureTrustedAdmission(policy(ctx, host, scope))
    path = "/api/projects/DEMO/runs/" + before.run_id + "/skill-reviews"
    with server_url(ctx, monkeypatch, "product", codex_review_admission=admission) as base:
        initial = get(base, path)[1]
        assert initial["reviews"] == [] and initial["start_available"] is True
        action = {"project_key": "DEMO", "run_id": before.run_id, "approval": "security"}
        code, result = post(base, "/api/reviewer-jobs/run", action)
        assert code == 200 and result["ran_count"] == 1 and result["results"][0]["status"] == "review_received", result
        code, page = get(base, path)
        assert code == 200 and len(page["reviews"]) == 1, page
        assert page["start_available"] is False
        item = page["reviews"][0]
        assert item["task_skill_context"]["task"] == scope["task"]
        assert item["methods"] == [] and item["runtime_verification"] == "contract_fixture"
        code, report = get(base, path + "/" + item["job_id"] + "/report")
        assert code == 200 and report["report"]["evidence"][0]["reference_status"] == "matched_input", report
        assert post(base, "/api/reviewer-jobs/run", action)[1]["ran_count"] == 0
        assert post(base, "/api/reviewer-jobs/run", {**action, "run_id": "different-task"})[0] == 409
    assert StateStore(ctx.workspace).read_project_current("DEMO") == before
    assert before.task_skill_context == {} and len(admission.leases) == 1
    # Other workflow roles are not upgraded into the protected Skill/native path.
    from goodteam_os.skills.task_start import workflow_job_fields
    assert workflow_job_fields(before, role="implementation", command_key="unrelated-role") == {}
    from goodteam_os.skills.review_api import handle_review_get
    assert handle_review_get(ctx.workspace, path + "/" + item["job_id"] + "/report")[0] == 200


def test_default_page_wrong_private_task_permission_does_not_launch_native(ctx, monkeypatch):
    host = native(ctx)
    state, scope = default_page(ctx, monkeypatch)
    scope["task"]["logical_id"] = "another-task"
    admission = FixtureTrustedAdmission(policy(ctx, host, scope))
    with server_url(ctx, monkeypatch, "product", codex_review_admission=admission) as base:
        code, result = post(base, "/api/reviewer-jobs/run", {"project_key": "DEMO", "run_id": state.run_id, "approval": "security"})
        assert code == 409 and result["error"] == "trusted_codex_task_mismatch"
    assert admission.leases == {}


@pytest.mark.parametrize("bad_selection", ["model", "empty_model", "adapter", "multiple_profiles"])
def test_invalid_team_selection_publishes_nothing_and_correcting_it_can_start_the_same_review(ctx, monkeypatch, bad_selection):
    host = native(ctx)
    state, scope = default_page(ctx, monkeypatch)
    admission = FixtureTrustedAdmission(policy(ctx, host, scope))
    original_config = ctx.config.read_text()

    def selection(key, **runtime):
        team = ctx.service.team.read()
        ctx.service.team.execute([{"kind": "change_worker_runtime", "worker_id": "code_review-a", **runtime}],
            actor="user", surface="team_ui", command_key=key, expected_revision=team["revision"])

    if bad_selection == "multiple_profiles":
        ctx.config.write_text(original_config.replace("\n    - correctness-security\n",
            "\n    - correctness-security\n    - architecture-maintainability\n"))
    else:
        selection("invalid-selection", **{"model": "not-authorized" if bad_selection == "model" else ""}
            if bad_selection in {"model", "empty_model"} else {"provider": "fake"})
    before_jobs = WorkerJobStore(ctx.repo).list_jobs()
    with ctx.service.locks._locked() as fd:
        before_registry = ctx.service.locks._registry(fd)
    endpoint = "/api/projects/DEMO/runs/" + state.run_id + "/skill-reviews"
    with server_url(ctx, monkeypatch, "product", codex_review_admission=admission) as base:
        body = {"project_key": "DEMO", "run_id": state.run_id}
        code, rejected = post(base, "/api/reviewer-jobs/run", body)
        assert code == 409 and rejected["error"] == "trusted_codex_reviewer_selection", rejected
        assert admission.leases == {} and WorkerJobStore(ctx.repo).list_jobs() == before_jobs
        with ctx.service.locks._locked() as fd:
            assert ctx.service.locks._registry(fd) == before_registry
        page = get(base, endpoint)[1]
        assert page["reviews"] == [] and page["start_available"] is True
        ctx.config.write_text(original_config)
        selection("corrected-selection", provider="codex", model="explicit-test-model")
        code, started = post(base, "/api/reviewer-jobs/run", body)
        assert code == 200 and started["results"][0]["status"] == "review_received", started
        assert started["ran_count"] == 1 and len(admission.leases) == 1
        assert len(get(base, endpoint)[1]["reviews"]) == 1


@pytest.mark.parametrize("revocation", ["deleted", "permissions", "changed"])
def test_revoked_private_policy_disables_start_but_saved_report_remains_readable(ctx, monkeypatch, revocation):
    host = native(ctx)
    state, scope = default_page(ctx, monkeypatch)
    admission = FixtureTrustedAdmission(policy(ctx, host, scope))
    endpoint = "/api/projects/DEMO/runs/" + state.run_id + "/skill-reviews"
    with server_url(ctx, monkeypatch, "product", codex_review_admission=admission) as base:
        body = {"project_key": "DEMO", "run_id": state.run_id}
        assert post(base, "/api/reviewer-jobs/run", body)[1]["results"][0]["status"] == "review_received"
        original = get(base, endpoint)[1]["reviews"][0]
        if revocation == "deleted":
            admission.path.unlink()
        elif revocation == "permissions":
            admission.path.chmod(0o644)
        else:
            admission.path.write_text("The authorization has been revoked.\n")
        code, page = get(base, endpoint)
        assert code == 200 and page["start_available"] is False, page
        assert page["reviews"][0]["report_digest"] == original["report_digest"]
        for suffix in ("", "/report"):
            code, saved = get(base, endpoint + "/" + original["job_id"] + suffix)
            assert code == 200 and saved["report_available"] is True, saved
            assert saved["report_digest"] == original["report_digest"]
        assert len(admission.leases) == 1


def test_prefixed_team_model_is_canonical_in_snapshot_and_replay_after_bare_model_selection(ctx, monkeypatch):
    host = native(ctx)
    state, scope = default_page(ctx, monkeypatch)

    def select(model, key):
        team = ctx.service.team.read()
        ctx.service.team.execute([{"kind": "change_worker_runtime", "worker_id": "code_review-a", "model": model}],
            actor="user", surface="team_ui", command_key=key, expected_revision=team["revision"])

    select("codex:explicit-test-model", "prefixed-native-selection")
    admission = FixtureTrustedAdmission(policy(ctx, host, scope))
    endpoint = "/api/projects/DEMO/runs/" + state.run_id + "/skill-reviews"
    with server_url(ctx, monkeypatch, "product", codex_review_admission=admission) as base:
        body = {"project_key": "DEMO", "run_id": state.run_id}
        code, started = post(base, "/api/reviewer-jobs/run", body)
        assert code == 200 and started["results"][0]["status"] == "review_received", started
        item = get(base, endpoint)[1]["reviews"][0]
        snapshot = admission_snapshot(ctx, {"id": item["job_id"]})
        assert snapshot["job_input"]["request"]["model"] == "explicit-test-model"
        assert item["runtime"]["model"] == "explicit-test-model"
        code, report = get(base, endpoint + "/" + item["job_id"] + "/report")
        assert code == 200 and report["report_available"]
        select("explicit-test-model", "bare-native-selection")
        code, replay = post(base, "/api/reviewer-jobs/run", body)
        assert code == 200 and replay["ran_count"] == 0, replay
        assert len(get(base, endpoint)[1]["reviews"]) == 1 and len(admission.leases) == 1


def test_default_page_can_cancel_the_exact_native_attempt_while_run_holds_workflow_guard(ctx, monkeypatch):
    host = native(ctx, body="time.sleep(20)")
    state, scope = default_page(ctx, monkeypatch)
    admission = FixtureTrustedAdmission(policy(ctx, host, scope))
    path = "/api/projects/DEMO/runs/" + state.run_id + "/skill-reviews"
    with server_url(ctx, monkeypatch, "product", codex_review_admission=admission) as base, ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(post, base, "/api/reviewer-jobs/run", {"project_key": "DEMO", "run_id": state.run_id})
        end = time.monotonic() + 4
        item = None
        while time.monotonic() < end:
            reviews = get(base, path)[1]["reviews"]
            item = next((review for review in reviews if review["status"] == "running"), None)
            if item:
                break
            time.sleep(0.02)
        assert item is not None
        body = {"snapshot_digest": item["snapshot_ref"]["digest"], "context_digest": item["context_ref"]["digest"]}
        assert post(base, path + "/" + item["job_id"] + "/cancel", body)[0] == 202
        assert future.result(4)[1]["results"][0]["status"] == "failed"
        stopped = get(base, path)[1]["reviews"][0]
        assert stopped["status"] == "cancelled" and not stopped["report_available"]
        assert stopped["runtime_boundaries"]["provider_retirement_supported"] is False
        assert len(admission.leases) == 1


def test_failed_default_page_retry_keeps_original_bytes_and_restart_cannot_refund_budget(ctx, monkeypatch):
    host = native(ctx, malformed=True)
    state, scope = default_page(ctx, monkeypatch)
    path = policy(ctx, host, scope)
    admission = FixtureTrustedAdmission(path)
    endpoint = "/api/projects/DEMO/runs/" + state.run_id + "/skill-reviews"
    with server_url(ctx, monkeypatch, "product", codex_review_admission=admission) as base:
        action = {"project_key": "DEMO", "run_id": state.run_id}
        assert post(base, "/api/reviewer-jobs/run", action)[1]["results"][0]["status"] == "failed"
        first = get(base, endpoint)[1]["reviews"][0]
        assert first["status"] == "invalid" and len(admission.leases) == 1
        (ctx.root / "default-candidate.py").write_text("Replacement input must not reach an explicit retry.\n")
        body = {"snapshot_digest": first["snapshot_ref"]["digest"], "context_digest": first["context_ref"]["digest"]}
        changed, rejected = post(base, endpoint + "/" + first["job_id"] + "/retry", body)
        assert changed == 409 and rejected["error"] == "review_target_changed"
        assert len(admission.leases) == 1
        (ctx.root / "default-candidate.py").write_bytes(SOURCE)
        code, retried = post(base, endpoint + "/" + first["job_id"] + "/retry", body)
        assert code == 200 and retried["created"] and retried["status"] == "failed", retried
        jobs = WorkerJobStore(ctx.repo).list_jobs()
        assert next(job for job in jobs if job["id"] == retried["job_id"])["skill_error_code"] == "trusted_codex_budget_exhausted"
        assert len(admission.leases) == 1
    restarted = FixtureTrustedAdmission(path)
    with pytest.raises(SkillError) as error:
        restarted.acquire(admission_snapshot(ctx, {"id": first["job_id"]}), context_id="review-" + "d" * 32)
    assert error.value.code == "trusted_codex_budget_exhausted" and restarted.leases == {}


def test_http_start_native_result_validation_persistence_and_page_report_read(ctx, monkeypatch):
    host = native(ctx)
    job, context = ready(ctx, task="trusted-review")
    admission = FixtureTrustedAdmission(policy(ctx, host, context))
    before = StateStore(ctx.workspace).read_project_current("DEMO")
    with server_url(ctx, monkeypatch, "product", codex_review_admission=admission) as base:
        code, response = post(base, "/api/reviewer-jobs/run", {"project_key": "DEMO", "run_id": "trusted-review", "approval": "security"})
        assert code == 200 and response["results"][0]["status"] == "review_received", response
        code, page = get(base, "/api/projects/DEMO/runs/trusted-review/skill-reviews")
        assert code == 200 and len(page["reviews"]) == 1
        item = page["reviews"][0]
        assert item["report_available"] and item["runtime_verification"] == "contract_fixture"
        assert item["runtime_boundaries"]["mode"] == "trusted_self_use"
        assert item["runtime_boundaries"]["native_config_authority"] == "native_managed"
        assert item["runtime_boundaries"]["provider_binding_verified"] is False
        assert item["runtime_boundaries"]["automatic_parent_retries"] == 0
        code, detail = get(base, "/api/projects/DEMO/runs/trusted-review/skill-reviews/" + job["id"] + "/report")
        assert code == 200 and detail["report"]["findings"] == [] and detail["advisory"]
        assert detail["report"]["evidence"][0]["reference_status"] == "matched_input"
        assert post(base, "/api/reviewer-jobs/run", {"project_key": "DEMO", "run_id": "trusted-review", "approval": "security"})[1]["ran_count"] == 0
    assert StateStore(ctx.workspace).read_project_current("DEMO") == before
    assert len(admission.leases) == 1
    assert not (ctx.repo / ".goodteam" / "workers" / "jobs" / job["id"] / ".codex-home").exists()
    for root in (ctx.repo, service.service_state_dir()):
        assert not list(root.rglob("auth.json")) and not list(root.rglob("config.toml"))
    restored = FixtureTrustedAdmission(admission.path)
    assert WorkerJobStore(ctx.repo, codex_review_admission=restored).run_job(job["id"])["status"] == "review_received"
    assert restored.leases == {}


@pytest.mark.parametrize("change,code", [
    ({"trusted_task_inputs": False}, "trusted_codex_policy_invalid"),
    ({"accept_native_managed_configuration": False}, "trusted_codex_policy_invalid"),
    ({"accept_unknown_cost": False}, "trusted_codex_policy_invalid"),
    ({"usd_limit": 1}, "trusted_codex_budget_unsupported"),
    ({"provider_request_limit": 1}, "trusted_codex_budget_unsupported"),
    ({"max_contexts": 0}, "trusted_codex_policy_invalid"),
    ({"expires_at": 1}, "trusted_codex_policy_invalid"),
])
def test_private_exposure_policy_is_not_a_config_or_cost_verification_flag(ctx, change, code):
    host = native(ctx)
    _job, context = ready(ctx)
    with pytest.raises(SkillError) as error:
        FixtureTrustedAdmission(policy(ctx, host, context, **change))
    assert error.value.code == code


def test_third_party_goodteam_methods_are_rejected_before_context_or_native_launch(ctx):
    host = native(ctx)
    bind(ctx, method(ctx, "UNTRUSTED_METHOD_MUST_NOT_GAIN_NATIVE_AUTHORITY"), subject="role:code_review")
    job, context = ready(ctx)
    admission = FixtureTrustedAdmission(policy(ctx, host, context))
    result = WorkerJobStore(ctx.repo, codex_review_admission=admission).run_job(job["id"])
    assert result["skill_error_code"] == "trusted_codex_skills_unsupported", result
    assert admission.leases == {} and not (service.service_state_dir() / "codex-trusted-contexts").exists()


def test_invalid_native_report_fails_without_automatic_retry_or_context_refund(ctx):
    host = native(ctx, malformed=True)
    job, context = ready(ctx)
    admission = FixtureTrustedAdmission(policy(ctx, host, context))
    result = WorkerJobStore(ctx.repo, codex_review_admission=admission).run_job(job["id"])
    assert result["status"] == "failed" and len(admission.leases) == 1
    assert not ReviewResultStore(ctx.workers, job["id"]).detail()["report_available"]
    with pytest.raises(SkillError) as error:
        admission.acquire(admission_snapshot(ctx, job), context_id="review-" + "a" * 32)
    assert error.value.code == "trusted_codex_budget_exhausted"


def admission_snapshot(ctx, job):
    from goodteam_os.skills import worker_requests
    return worker_requests._services(ctx.workers)[2].find_snapshot(command_key=job["id"])["snapshot"]


def test_owned_native_cancel_uses_existing_report_lifecycle_without_remote_stop_claim(ctx, monkeypatch):
    host = native(ctx, body="time.sleep(20)")
    job, context = ready(ctx)
    admission = FixtureTrustedAdmission(policy(ctx, host, context))
    workers = WorkerJobStore(ctx.repo, codex_review_admission=admission)
    with server_url(ctx, monkeypatch, "product", codex_review_admission=admission) as base, ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(workers.run_job, job["id"])
        expires = time.monotonic() + 3
        results = ReviewResultStore(ctx.workers, job["id"])
        while time.monotonic() < expires:
            try:
                if results.detail()["status"] == "running":
                    break
            except SkillError:
                pass
            time.sleep(0.02)
        detail = results.detail()
        body = {"snapshot_digest": detail["snapshot_ref"]["digest"], "context_digest": detail["context_ref"]["digest"]}
        assert post(base, "/api/projects/DEMO/runs/local-review/skill-reviews/" + job["id"] + "/cancel", body)[0] == 202
        assert future.result(4)["status"] == "failed"
    detail = results.detail()
    assert detail["status"] == "cancelled" and not detail["report_available"]
    assert detail["runtime_boundaries"]["provider_retirement_supported"] is False


def test_trusted_admission_cannot_enter_the_legacy_copyauth_or_retry_path(ctx):
    host = native(ctx)
    _job, context = ready(ctx)
    admission = FixtureTrustedAdmission(policy(ctx, host, context))
    workers = WorkerJobStore(ctx.repo, codex_review_admission=admission)
    job = workers.create_job(run_id="different-legacy-task", role="implementation", adapter="codex", task="Unauthorized legacy work", context="")
    result = workers.run_job(job["id"])
    assert result["status"] == "failed" and result["error_code"] == "local_codex_task_capture_required"
    assert admission.leases == {}


def test_production_refuses_script_fixture_and_multiple_startup_authorities(ctx):
    host = native(ctx)
    _job, context = ready(ctx)
    path = policy(ctx, host, context)
    with pytest.raises(SkillError) as error:
        TrustedCodexReviewAdmission(path)
    assert error.value.code == "trusted_codex_binary_unavailable"
    with pytest.raises(ValueError, match="Choose one"):
        cli.main(["serve", "--trusted-codex-review-policy", str(path), "--local-codex-policy", str(path)])
