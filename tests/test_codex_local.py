"""Self-use production composition with an explicitly model-free CLI fixture.

The executable emits native-shaped events and consumes stdin. It is not Codex,
containment or provider evidence. The same authority/Worker/HTTP paths are used.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sys
import time

import pytest

from goodteam_os import cli, service
from goodteam_os.skills import codex_local
from goodteam_os.skills.codex_local import DISABLED_FEATURES, LocalCodexReviewAdmission, inspect_local_codex
from goodteam_os.skills.models import SkillError, content_digest
from goodteam_os.skills.review_results import ReviewResultStore
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.workers import WorkerJobStore
from test_skill_review_http import server_url, post
from test_skill_review_protocol import direct_request
from test_skill_review_results import SOURCE, channel
from test_task_skill_entrypoints import bind, capture, method, ctx as ctx


class FixtureLocalAdmission(LocalCodexReviewAdmission):
    """Explicit model-free contract authority, never native config admission.

    Production construction is closed. This test-only subclass exercises the
    candidate's quota/capture/cancel mechanics with a clearly marked executable
    and cannot accept the actual native binary or claim local_cli verification.
    """
    verification_kind = "contract_fixture"

    def _require_effective_configuration(self):
        with self.codex.open("rb") as stream:
            assert stream.readline() == ("#!" + sys.executable + "\n").encode()
            assert stream.readline() == b"# goodteam-model-free-local-config-fixture\n"


def local_host(ctx, *, body="", output=None):
    home = ctx.root / "existing-login"
    home.mkdir(exist_ok=True)
    (home / "config.toml").write_text('model_provider="existing"\n[model_providers.existing]\n'
                                   'name="Existing test route"\nbase_url="https://provider.invalid/v1"\n'
                                   'wire_api="responses"\nrequires_openai_auth=true\n')
    (home / "config.toml").chmod(0o600)
    (home / "auth.json").write_text('{"test_secret":"DO_NOT_COPY_OR_EXPOSE"}')
    (home / "auth.json").chmod(0o600)
    executable = ctx.root / "fixture-codex"
    raw = output if output is not None else channel()
    gates = "".join(key + " stable false\n" for key in DISABLED_FEATURES) + "skip_host_skill_discovery stable true\n"
    executable.write_text("#!" + sys.executable + "\n# goodteam-model-free-local-config-fixture\nimport sys,os\n"
                          "if '--version' in sys.argv:\n print('codex-cli 0.160.0');sys.exit(0)\n"
                          "if 'features' in sys.argv and 'list' in sys.argv:\n print(" + repr(gates) + ");sys.exit(0)\n"
                          "assert '--ephemeral' in sys.argv and '--strict-config' not in sys.argv\n"
                          "assert '--ignore-user-config' not in sys.argv and '--ignore-rules' not in sys.argv\n"
                          "assert 'features.shell_tool=false' in sys.argv\n"
                          "assert 'model_providers.existing.request_max_retries=0' in sys.argv\n"
                          "assert 'DO_NOT_COPY_OR_EXPOSE' not in ' '.join(sys.argv)\n"
                          "assert 'UNRELATED_CREDENTIAL' not in os.environ\n"
                          "assert 'OPENAI_API_KEY' not in os.environ\n"
                          "prompt=sys.stdin.buffer.read()\nassert prompt and b'candidate' in prompt\n"
                          "assert b'DO_NOT_COPY_OR_EXPOSE' not in prompt\n"
                          + body + "\nsys.stdout.buffer.write(" + repr(raw) + ")\nsys.stdout.buffer.flush()\n")
    executable.chmod(0o700)
    return executable, home


def test_unsupported_version_fails_before_reading_selected_configuration(ctx, monkeypatch):
    executable, home = local_host(ctx)
    executable.write_text(executable.read_text().replace("codex-cli 0.160.0", "codex-cli 0.159.2"))

    def forbidden(*_args, **_kwargs):
        pytest.fail("An unsupported binary must not inspect configuration or login metadata")

    monkeypatch.setattr(codex_local, "_selection", forbidden)
    with pytest.raises(SkillError) as failure:
        inspect_local_codex(executable, home, "explicit-test-model")
    assert failure.value.code == "local_codex_version_unsupported"


def test_unsafe_binary_fails_before_executing_or_reading_configuration(ctx, monkeypatch):
    executable, home = local_host(ctx)
    executable.chmod(0o722)

    def forbidden(*_args, **_kwargs):
        pytest.fail("An unsafe executable must not run or inspect configuration")

    monkeypatch.setattr(codex_local, "_selection", forbidden)
    monkeypatch.setattr(codex_local.subprocess, "run", forbidden)
    with pytest.raises(SkillError) as failure:
        inspect_local_codex(executable, home, "explicit-test-model")
    assert failure.value.code == "local_codex_binary_unsafe"


@pytest.mark.parametrize("command", ["--version", "features"])
def test_binary_drift_during_inspection_cannot_return_a_selection(ctx, monkeypatch, command):
    executable, home = local_host(ctx)
    run = codex_local.subprocess.run
    select = codex_local._selection
    selections = []

    def capture(*args, **kwargs):
        result = run(*args, **kwargs)
        if command in args[0]:
            executable.write_text(executable.read_text() + "\n# Changed during metadata inspection\n")
        return result

    def selection(*args, **kwargs):
        selections.append(args)
        return select(*args, **kwargs)

    monkeypatch.setattr(codex_local.subprocess, "run", capture)
    monkeypatch.setattr(codex_local, "_selection", selection)
    with pytest.raises(SkillError) as failure:
        inspect_local_codex(executable, home, "explicit-test-model")
    assert failure.value.code == "local_codex_selection_changed"
    assert len(selections) == (0 if command == "--version" else 1)


def ready(ctx, *, task="local-review", model="explicit-test-model", **changes):
    context = capture(ctx, task, subjects=("role:code_review",), protocol_only_review=True)
    candidate = ctx.root / (task + ".py")
    candidate.write_bytes(SOURCE)
    job = ctx.workers.create_job(**direct_request(context, model=model, artifact_key="rfc",
        artifact_path=str(candidate), artifact_version=content_digest(SOURCE), profile_id="correctness-security",
        phase="implementation", approval="security", dispatch_group_id="local-group", **changes))
    state = create_initial_state(task, "DEMO", "Review captured source")
    state.task_skill_context = context
    state.current_node, state.next_node = "security_review_gate", "await_security_approval"
    state.artifacts["rfc"] = str(candidate)
    StateStore(ctx.workspace).write_project_current(state)
    return job, context


def policy(ctx, host, context, **changes):
    executable, home = host
    inspected = inspect_local_codex(executable, home, "explicit-test-model")
    p = {"schema_version": "goodteam.codex-local-policy.v1", "authorization_id": "local-authorization",
         "worker_root": str(ctx.repo), "task": context["task"], "codex": str(executable), "codex_home": str(home),
         "model": "explicit-test-model", "selection_digest": inspected["selection_digest"], "max_contexts": 1,
         "deadline_seconds": 5, "expires_at": int(time.time()) + 3600, "accept_unknown_cost": True,
         "provider_request_limit": None, "usd_limit": None, **changes}
    path = ctx.root / "local-authorization.json"
    path.write_text(json.dumps(p))
    path.chmod(0o600)
    return path


@pytest.mark.parametrize("extra_layer", [
    "managed_same_provider_alias", "required_provider_alias", "system_provider",
    "profile_provider", "cloud_mcp", "project_mcp", "packaged_mcp", "unknown_future_layer",
])
def test_unbound_native_layers_and_drift_cannot_activate_production(ctx, monkeypatch, extra_layer):
    """Adversarial layer fixtures are not a native-layer conformance witness.

    User-config/feature metadata can stay green while another layer changes.
    The production gate must reject that ambiguity, even before any metadata
    subprocess/MCP transport, and must not consume a context on either check.
    """
    marker = ctx.root / "unauthorized-native-or-mcp-start"
    host = local_host(ctx, body="open(" + repr(str(marker)) + ",'w').write('started')")
    _job, context = ready(ctx)
    path = policy(ctx, host, context)
    candidate_digest = json.loads(path.read_text())["selection_digest"]
    observed = inspect_local_codex(*host, "explicit-test-model")
    assert observed["selection_digest"] == candidate_digest
    assert observed["admission_ready"] is False and observed["effective_config_binding"] == "unverified"
    user_bytes = (host[1] / "config.toml").read_bytes()
    layer = ctx.root / "extra-native-layer.json"
    layer.write_text(json.dumps({"type": extra_layer, "provider": "existing", "required_model_provider": "managed",
                                "base_url": "https://other-provider.invalid/v1",
                                "mcp_servers": {"inherited_unknown_server": {"command": "must-not-start"}}}))
    def forbidden(*_args, **_kwargs):
        pytest.fail("Unverified layered configuration cannot reach any native child or transport")
    monkeypatch.setattr("goodteam_os.skills.codex_local.subprocess.run", forbidden)
    for revision in (1, 2):
        layer.write_text(layer.read_text().replace('other-provider', 'changed-provider') + "\n" * revision)
        with pytest.raises(SkillError) as failure:
            LocalCodexReviewAdmission(path)
        assert failure.value.code == "local_codex_effective_config_unverified"
        assert (host[1] / "config.toml").read_bytes() == user_bytes
        assert json.loads(path.read_text())["selection_digest"] == candidate_digest
        assert not marker.exists()
        assert not (service.service_state_dir() / "codex-local-budgets").exists()
        assert not (service.service_state_dir() / "codex-local-task-bindings").exists()


def snapshot(ctx, job):
    from goodteam_os.skills.content import SkillContextProvider
    return SkillContextProvider(ctx.workers, job["id"])._validated()[3]


def test_worker_local_execution_reports_actual_boundaries_and_replays_without_new_context(ctx, monkeypatch):
    host = local_host(ctx)
    job, context = ready(ctx)
    admission = FixtureLocalAdmission(policy(ctx, host, context))
    workers = WorkerJobStore(ctx.repo, codex_review_admission=admission)
    monkeypatch.setenv("UNRELATED_CREDENTIAL", "do-not-inherit")
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-change-native-login-selection")
    before = StateStore(ctx.workspace).read_project_current("DEMO")
    original_auth = (host[1] / "auth.json").read_bytes()
    result = workers.run_job(job["id"])
    assert result["status"] == "review_received", result
    detail = ReviewResultStore(workers, job["id"]).detail(include_report=True)
    assert detail["runtime_verification"] == "contract_fixture"
    assert detail["runtime_boundaries"]["usd_limit"] is None
    assert detail["runtime_boundaries"]["provider_request_limit"] is None
    assert detail["runtime_boundaries"]["context_limit"] == 1
    assert detail["runtime_boundaries"]["strict_skill_binding"] is False
    assert detail["runtime_boundaries"]["provider_retirement_supported"] is False
    assert detail["stages"]["resource_read_observed"] == "unobserved"
    assert len(admission.leases) == 1 and detail["report_available"]
    assert (host[1] / "auth.json").read_bytes() == original_auth
    operator = service.service_state_dir()
    assert not list(operator.rglob("auth.json"))
    assert "DO_NOT_COPY_OR_EXPOSE" not in json.dumps(detail)
    assert StateStore(ctx.workspace).read_project_current("DEMO") == before
    fresh = FixtureLocalAdmission(admission.path)
    assert WorkerJobStore(ctx.repo, codex_review_admission=fresh).run_job(job["id"])["status"] == "review_received"
    assert fresh.leases == {}


def test_local_startup_authority_runs_through_real_product_http_and_rejects_stale_run(ctx, monkeypatch):
    host = local_host(ctx)
    job, context = ready(ctx)
    admission = FixtureLocalAdmission(policy(ctx, host, context))
    before = StateStore(ctx.workspace).read_project_current("DEMO")
    with server_url(ctx, monkeypatch, "product", codex_review_admission=admission) as base:
        request = {"project_key": "DEMO", "run_id": "local-review", "approval": "security"}
        assert post(base, "/api/reviewer-jobs/run", {**request, "run_id": "old-run"})[0] == 409
        assert admission.leases == {}
        code, payload = post(base, "/api/reviewer-jobs/run", request)
        assert code == 200 and payload["results"][0]["status"] == "review_received", payload
        assert payload["approval_unchanged"] and payload["advisory_only"]
        assert post(base, "/api/reviewer-jobs/run", request)[1]["ran_count"] == 0
    assert len(admission.leases) == 1
    assert StateStore(ctx.workspace).read_project_current("DEMO") == before
    assert ReviewResultStore(ctx.workers, job["id"]).detail()["runtime_verification"] == "contract_fixture"


@pytest.mark.parametrize("changes,code", [
    ({"accept_unknown_cost": False}, "local_codex_policy_invalid"),
    ({"usd_limit": 1}, "local_codex_budget_unsupported"),
    ({"provider_request_limit": 1}, "local_codex_budget_unsupported"),
    ({"max_contexts": 0}, "local_codex_policy_invalid"),
    ({"max_contexts": True}, "local_codex_policy_invalid"),
    ({"deadline_seconds": 121}, "local_codex_policy_invalid"),
    ({"expires_at": 1}, "local_codex_policy_invalid"),
])
def test_policy_refuses_unknown_exposure_and_unenforceable_hard_ceilings(ctx, changes, code):
    host = local_host(ctx)
    _job, context = ready(ctx)
    with pytest.raises(SkillError) as failure:
        FixtureLocalAdmission(policy(ctx, host, context, **changes))
    assert failure.value.code == code


def test_local_authority_cannot_borrow_a_strict_skill_task_or_its_provider_context(ctx):
    host = local_host(ctx)
    bind(ctx, method(ctx, "USER_METHOD_MUST_NOT_RUN"), subject="role:code_review")
    job, context = ready(ctx)
    admission = FixtureLocalAdmission(policy(ctx, host, context))
    result = WorkerJobStore(ctx.repo, codex_review_admission=admission).run_job(job["id"])
    assert result["skill_error_code"] == "local_codex_skills_unsupported", result
    assert admission.leases == {}
    assert not (service.service_state_dir() / "codex-local-budgets").exists()


@pytest.mark.parametrize("gate", ["shell_tool", "code_mode", "daemon_auto_start", "missing_shell_tool"])
def test_effective_native_gates_fail_before_context_allocation(ctx, gate):
    executable, home = local_host(ctx)
    name = "shell_tool" if gate == "missing_shell_tool" else gate
    replacement = "absent_shell_tool stable false" if gate == "missing_shell_tool" else name + " stable true"
    executable.write_text(executable.read_text().replace(name + " stable false", replacement))
    with pytest.raises(SkillError) as failure:
        inspect_local_codex(executable, home, "explicit-test-model")
    assert failure.value.code == "local_codex_features_unavailable"
    assert not (service.service_state_dir() / "codex-local-budgets").exists()


def test_legacy_user_config_is_preserved_without_strict_config_or_secret_copy(ctx):
    executable, home = local_host(ctx)
    original = (home / "config.toml").read_bytes()
    (home / "config.toml").write_bytes(b"disable_response_storage=true\n" + original)
    job, context = ready(ctx)
    admission = FixtureLocalAdmission(policy(ctx, (executable, home), context))
    result = WorkerJobStore(ctx.repo, codex_review_admission=admission).run_job(job["id"])
    assert result["status"] == "review_received", result
    assert (home / "config.toml").read_bytes() == b"disable_response_storage=true\n" + original
    assert not list(service.service_state_dir().rglob("config.toml"))


@pytest.mark.parametrize("field,value", [("model", "other-model"), ("task", "other-task"), ("session_policy", "resume")])
def test_wrong_model_task_or_resumed_session_does_not_consume_authorized_context(ctx, field, value):
    host = local_host(ctx)
    job, context = ready(ctx)
    admission = FixtureLocalAdmission(policy(ctx, host, context))
    original = snapshot(ctx, job)
    changed = deepcopy(original)
    if field == "task":
        changed["task"]["logical_id"] = value
    else:
        changed["job_input"]["request"][field] = value
    with pytest.raises(SkillError, match="exact task/model"):
        admission.acquire(changed, context_id="review-" + "a" * 32)
    assert admission.leases == {}


def test_context_consumption_survives_restart_and_concurrent_authorities(ctx):
    host = local_host(ctx)
    job, context = ready(ctx)
    path = policy(ctx, host, context)
    authorities = [FixtureLocalAdmission(path), FixtureLocalAdmission(path)]
    original = snapshot(ctx, job)
    def acquire(index):
        try:
            return authorities[index].acquire(original, context_id="review-" + str(index) * 32)
        except SkillError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(acquire, (0, 1)))
    assert results.count("local_codex_budget_exhausted") == 1
    restarted = FixtureLocalAdmission(path)
    with pytest.raises(SkillError, match="budget is exhausted"):
        restarted.acquire(original, context_id="review-" + "e" * 32)


@pytest.mark.parametrize("kind", ["policy", "provider", "binary"])
def test_live_authority_change_fails_before_reuse(ctx, kind):
    host = local_host(ctx)
    job, context = ready(ctx)
    path = policy(ctx, host, context)
    admission = FixtureLocalAdmission(path)
    original = snapshot(ctx, job)
    lease = admission.acquire(original, context_id="review-" + "a" * 32)
    if kind == "policy":
        path.write_bytes(path.read_bytes() + b" ")
    elif kind == "provider":
        config = host[1] / "config.toml"
        config.write_text(config.read_text().replace("provider.invalid", "other.invalid"))
    else:
        host[0].write_text(host[0].read_text() + "\n# changed executable\n")
    with pytest.raises(SkillError) as failure:
        admission.check(lease, original, context_id="review-" + "a" * 32, operation="input")
    assert failure.value.code in {"local_codex_authorization_revoked", "local_codex_selection_changed"}


def test_timeout_is_terminal_and_cannot_automatically_spend_again(ctx):
    host = local_host(ctx, body="import time;time.sleep(5)")
    job, context = ready(ctx)
    admission = FixtureLocalAdmission(policy(ctx, host, context, deadline_seconds=1))
    workers = WorkerJobStore(ctx.repo, codex_review_admission=admission)
    result = workers.run_job(job["id"])
    assert result["skill_error_code"] == "review_timeout", result
    assert ReviewResultStore(workers, job["id"]).detail()["status"] == "failed"
    assert workers.run_job(job["id"])["skill_error_code"] == "review_timeout"
    assert len(admission.leases) == 1


def test_cli_production_policy_fails_closed_before_native_process_or_listening(ctx, monkeypatch):
    host = local_host(ctx)
    _job, context = ready(ctx)
    path = policy(ctx, host, context)
    calls = []
    monkeypatch.setattr(cli, "serve", lambda **kwargs: calls.append(kwargs))
    def forbidden(*_args, **_kwargs):
        pytest.fail("An unbound production authority cannot start any native process")
    monkeypatch.setattr("goodteam_os.skills.codex_local.subprocess.run", forbidden)
    with pytest.raises(SkillError) as failure:
        cli.main(["serve", "--workspace", str(ctx.workspace), "--local-codex-policy", str(path)])
    assert failure.value.code == "local_codex_effective_config_unverified"
    assert calls == []
    assert not (service.service_state_dir() / "codex-local-budgets").exists()


def test_cli_embedding_retains_explicit_model_free_fixture_authority(ctx, monkeypatch):
    host = local_host(ctx)
    _job, context = ready(ctx)
    path = policy(ctx, host, context)
    fixture = FixtureLocalAdmission(path)
    calls = []
    monkeypatch.setattr(cli, "serve", lambda **kwargs: calls.append(kwargs))
    assert cli.main(["serve", "--workspace", str(ctx.workspace)], codex_review_admission=fixture) == 0
    assert calls[0]["codex_review_admission"] is fixture
    assert fixture.verification_kind == "contract_fixture" and fixture.leases == {}
    with pytest.raises(ValueError, match="one Reviewer"):
        cli.main(["serve", "--local-codex-policy", str(path)],
                 codex_review_admission=FixtureLocalAdmission(path))


def test_native_provider_bearer_stays_in_existing_configuration(ctx):
    host = local_host(ctx)
    config = host[1] / "config.toml"
    config.write_text(config.read_text() + 'experimental_bearer_token="DO_NOT_COPY_OR_EXPOSE"\n')
    job, context = ready(ctx)
    path = policy(ctx, host, context)
    admission = FixtureLocalAdmission(path)
    result = WorkerJobStore(ctx.repo, codex_review_admission=admission).run_job(job["id"])
    assert result["status"] == "review_received", result
    assert "DO_NOT_COPY_OR_EXPOSE" not in path.read_text()
    assert "DO_NOT_COPY_OR_EXPOSE" not in json.dumps(admission.selection)
    assert "DO_NOT_COPY_OR_EXPOSE" not in json.dumps(result)


def test_custom_empty_is_still_blocked_instead_of_becoming_protocol_only(ctx):
    host = local_host(ctx)
    current = ctx.service.team.read()
    binding = current["skill_bindings"]["role:code_review"]
    ctx.service.team.execute(
        [{"kind": "set_role_skills", "subject": "role:code_review", "mode": "custom", "bindings": [],
          "expected_binding_revision": binding["binding_revision"]}], actor="user", surface="team_ui",
        command_key="unconfigured-custom-method", expected_revision=current["revision"])
    job, context = ready(ctx)
    admission = FixtureLocalAdmission(policy(ctx, host, context))
    result = WorkerJobStore(ctx.repo, codex_review_admission=admission).run_job(job["id"])
    assert result["skill_error_code"] == "methods_blocked"
    assert not admission.leases


def test_text_only_execution_rejects_tool_events_as_a_successful_report(ctx):
    events = channel().splitlines()
    event = {"type": "item.completed", "item": {"id": "unexpected-tool", "type": "file_change", "changes": []}}
    raw = b"\n".join([*events[:2], json.dumps(event).encode(), *events[2:]]) + b"\n"
    host = local_host(ctx, output=raw)
    job, context = ready(ctx)
    admission = FixtureLocalAdmission(policy(ctx, host, context))
    workers = WorkerJobStore(ctx.repo, codex_review_admission=admission)
    result = workers.run_job(job["id"])
    assert result["skill_error_code"] == "local_codex_tool_observed", result
    assert not ReviewResultStore(workers, job["id"]).detail()["report_available"]


def test_retry_uses_original_sources_and_a_new_explicitly_budgeted_context(ctx, monkeypatch):
    from test_skill_review_results import report

    marker = ctx.root / "fixture-next-report"
    good = channel()
    bad = channel(report(summary=""))
    body = ("from pathlib import Path\n"
            "sys.stdout.buffer.write(" + repr(good) + " if Path(" + repr(str(marker)) + ").exists() else "
            + repr(bad) + ")\nsys.stdout.buffer.flush();sys.exit(0)\n")
    host = local_host(ctx, body=body)
    job, context = ready(ctx)
    admission = FixtureLocalAdmission(policy(ctx, host, context, max_contexts=2))
    workers = WorkerJobStore(ctx.repo, codex_review_admission=admission)
    result = workers.run_job(job["id"])
    assert result["status"] == "failed"
    first = ReviewResultStore(workers, job["id"]).detail()
    assert first["status"] == "invalid" and first["retry_available"]
    marker.touch()
    path = f"/api/projects/DEMO/runs/local-review/skill-reviews/{job['id']}/retry"
    request = {"snapshot_digest": first["snapshot_ref"]["digest"], "context_digest": first["context_ref"]["digest"]}
    before = StateStore(ctx.workspace).read_project_current("DEMO")
    with server_url(ctx, monkeypatch, "product", codex_review_admission=admission) as base:
        code, retry = post(base, path, request)
        assert code == 200 and retry["status"] == "review_received", retry
        second = ReviewResultStore(workers, retry["job_id"]).detail()
        assert second["target"] == first["target"]
        assert second["runtime"]["session_id"] != first["runtime"]["session_id"]
        assert second["runtime"]["config_digest"] == first["runtime"]["config_digest"]
        assert post(base, path, request)[1]["created"] is False
    assert len(admission.leases) == 2
    assert StateStore(ctx.workspace).read_project_current("DEMO") == before


def test_local_cancel_reaps_real_child_without_refunding_context_or_claiming_remote_retirement(ctx, monkeypatch):
    marker = ctx.root / "fixture-running-pid"
    body = ("from pathlib import Path\nimport time\nPath(" + repr(str(marker))
            + ").write_text(str(os.getpid()))\nwhile True: time.sleep(.01)\n")
    host = local_host(ctx, body=body)
    job, context = ready(ctx)
    admission = FixtureLocalAdmission(policy(ctx, host, context, deadline_seconds=10))
    workers = WorkerJobStore(ctx.repo, codex_review_admission=admission)
    with server_url(ctx, monkeypatch, "product", codex_review_admission=admission) as base:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(workers.run_job, job["id"])
            end = time.monotonic() + 5
            while not marker.exists() and not future.done() and time.monotonic() < end:
                time.sleep(.01)
            assert marker.exists()
            store = ReviewResultStore(workers, job["id"])
            detail = store.detail()
            path = f"/api/projects/DEMO/runs/local-review/skill-reviews/{job['id']}/cancel"
            request = {"snapshot_digest": detail["snapshot_ref"]["digest"], "context_digest": detail["context_ref"]["digest"]}
            assert post(base, path, request)[0] == 202
            result = future.result(timeout=3)
            assert result["skill_error_code"] == "review_cancelled", result
            assert store.detail()["status"] == "cancelled"
            assert store.detail()["runtime_boundaries"]["provider_retirement_supported"] is False
            fresh = FixtureLocalAdmission(admission.path)
            with pytest.raises(SkillError, match="budget is exhausted"):
                fresh.acquire(snapshot(ctx, job), context_id="review-" + "d" * 32)


def test_renewed_authorization_cannot_change_an_old_tasks_provider_or_binary(ctx):
    host = local_host(ctx)
    job, context = ready(ctx)
    path = policy(ctx, host, context)
    admission = FixtureLocalAdmission(path)
    original = snapshot(ctx, job)
    admission.acquire(original, context_id="review-" + "a" * 32)
    config = host[1] / "config.toml"
    config.write_text(config.read_text().replace("provider.invalid", "other.invalid"))
    new_path = policy(ctx, host, context, authorization_id="explicit-second-authorization")
    changed = FixtureLocalAdmission(new_path)
    with pytest.raises(SkillError) as failure:
        changed.acquire(original, context_id="review-" + "b" * 32)
    assert failure.value.code == "local_codex_task_runtime_changed"
    assert not changed.leases


def test_legacy_worker_cannot_ignore_a_composed_local_budget(ctx):
    host = local_host(ctx)
    _job, context = ready(ctx)
    admission = FixtureLocalAdmission(policy(ctx, host, context))
    workers = WorkerJobStore(ctx.repo, codex_review_admission=admission)
    job = workers.create_job(run_id="legacy-unrelated", role="implementation", adapter="codex", task="Unbound work", context="")
    result = workers.run_job(job["id"])
    assert result["error_code"] == "local_codex_task_capture_required"
    assert result["invocation_evidence"]["external_invocation"] is False
