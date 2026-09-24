"""Pinned adapter through the actual Worker, with a scripted Docker boundary.

Only daemon replies and the attached executable are substituted. Files, snapshot,
content, real bounded child, result persistence and cancellation remain real.
This is NOT native Codex, Docker containment or live provider conformance.
"""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import shlex
import sys

import pytest

from goodteam_os.skills import codex_container
from goodteam_os.skills.codex_contract import ADAPTER_VERSION, CODEX_VERSION, CODEX_BINARY_DIGEST, CodexContract
from goodteam_os.skills.codex_runtime import CodexLease, CodexReviewAdmission, PinnedCodexReviewRuntime, seal_input_tree
from goodteam_os.skills.content import ContentSession
from goodteam_os.skills.models import SkillError, canonical_json, content_digest
from goodteam_os.skills.review_api import task_review_details
from goodteam_os.skills.review_results import ReviewResultStore
from goodteam_os.workers import WorkerJobStore
from test_skill_review_execution import ready_job
from test_skill_review_results import report
from test_task_skill_entrypoints import bind, ctx as ctx

POLICY = b'{"defaultAction":"SCMP_ACT_ERRNO","syscalls":[]}'
CID = "c" * 64
PROVIDER_ID = "d" * 64


def contract(**changes):
    return CodexContract(**{"image_id": "sha256:" + "a" * 64, "seccomp_digest": content_digest(POLICY),
        "model": "explicit-test-model", "network_id": "b" * 64, "provider_url": "http://fixture-provider:8080/v1",
        "provider_container_id": PROVIDER_ID, "provider_ipv4": "172.30.0.2", "provider_route_ref": "fixture-no-live-model",
        "provider_policy_digest": content_digest(b"fixture-policy-not-spend-conformance"), **changes})


class AdmissionFixture(CodexReviewAdmission):
    def __init__(self, *, fail_check=False, fail_finish=False, selected=None):
        self.selected = selected or contract()
        self.calls, self.finishes, self.acquires = [], [], []
        self.fail_check, self.fail_finish = fail_check, fail_finish

    def acquire(self, snapshot, *, context_id):
        self.acquires.append(context_id)
        return CodexLease(self.selected, POLICY, ContentSession("codex", ADAPTER_VERSION, CODEX_VERSION,
            CODEX_BINARY_DIGEST, self.selected.model, self.selected.digest, "fixture:" + context_id,
            "scripted-daemon-not-native-conformance", "contract_fixture"))

    def check(self, lease, snapshot, *, context_id, operation):
        self.calls.append((context_id, operation))
        if self.fail_check:
            raise SkillError("fixture_admission_denied", "No live permission.")

    def finish(self, lease, *, context_id, termination_confirmed):
        self.finishes.append((context_id, termination_confirmed))
        if self.fail_finish:
            raise SkillError("fixture_retirement_unknown", "Simulated route-retirement failure.")


class ScriptedEngine:
    """Finite local daemon fixture; no actual engine calls or provider traffic."""
    def __init__(self, spec, *, change=None, reject_stop=False, late_peer=False, output_body="", output_report=None):
        self.spec, self.change = spec, change
        self.output_report = output_report or report()
        self.reject_stop, self.late_peer, self.output_body = reject_stop, late_peer, output_body
        self.operations, self.attaches = [], 0
        self.stage, self.info = None, None
        self.removed = False

    def inspect(self, config, kind, identity):
        self.operations.append(("inspect", kind, identity))
        if kind == "image":
            return {"Id": self.spec.image_id, "Os": "linux", "Architecture": "arm64", "Config": {"Env": ["PATH=/usr/bin:/bin"]}}
        if kind == "network":
            peers = {PROVIDER_ID: {"IPv4Address": self.spec.provider_ipv4 + "/16"}}
            if self.late_peer:
                peers["e" * 64] = {"IPv4Address": "172.30.0.3/16"}
            return {"Id": self.spec.network_id, "Internal": True, "Driver": "bridge", "EnableIPv6": False, "Containers": peers}
        assert kind == "container" and identity in {CID, self.info["Name"].lstrip("/")}
        return deepcopy(self.info)

    def call(self, config, *args):
        self.operations.append(args)
        if args[0] == "create":
            self.stage = Path(args[args.index("--mount") + 1].split("src=", 1)[1].split(",", 1)[0])
            name = args[args.index("--name") + 1]
            self.info = {"Id": CID, "Name": "/" + name, "Image": self.spec.image_id,
                "Config": {"User": "65534:65534", "WorkingDir": "/review", "OpenStdin": True, "Tty": False,
                    "Entrypoint": ["/usr/bin/env"], "Cmd": [*codex_container.INNER_ENV, *self.spec.execution_command()],
                    "Healthcheck": {"Test": ["NONE"]}, "Labels": {codex_container.OWNER_LABEL: name[len("goodteam-"):]}},
                "HostConfig": {"Privileged": False, "ReadonlyRootfs": True, "CapDrop": ["ALL"], "CapAdd": None,
                    "NetworkMode": self.spec.network_id, "IpcMode": "private", "PidsLimit": 64, "Memory": 768*1024*1024,
                    "MemorySwap": 768*1024*1024, "NanoCpus": 1000000000,
                    "Tmpfs": deepcopy(codex_container.TMPFS), "Dns": ["127.0.0.1"], "DnsSearch": ["."],
                    "ExtraHosts": ["fixture-provider:" + self.spec.provider_ipv4], "LogConfig": {"Type": "none", "Config": {}},
                    "Sysctls": {"net.ipv6.conf.all.disable_ipv6": "1"}, "RestartPolicy": {"Name": "no"},
                    "SecurityOpt": ["no-new-privileges=true", "seccomp=" + POLICY.decode()]},
                "Mounts": [{"Type": "bind", "Source": str(self.stage), "Destination": "/review", "RW": False, "Propagation": "rprivate"}],
                "State": {"Running": False, "Pid": 0, "Restarting": False}}
            if self.change:
                self.change(self.info)
            return (CID + "\n").encode()
        if args[0] == "stop":
            if self.reject_stop:
                raise SkillError("codex_engine_unavailable", "Simulated stop permission denial.")
            self.info["State"].update(Running=False, Pid=0)
            return b""
        if args[0] == "rm":
            self.removed = True
            return b""
        if args[0:2] == ("container", "ls"):
            return b"" if self.removed else CID.encode()
        raise AssertionError(args)

    def command(self, config, *args):
        assert args == ("start", "--attach", "--interactive", CID)
        self.attaches += 1
        self.info["State"].update(Running=True, Pid=123)
        # Not a native binary! Real input/resources/child/output under fixture
        # provenance let us check the concrete adapter's composition separately.
        stage = self.stage
        method = next((stage / "methods").iterdir())
        resource = (method / "SKILL.md").read_bytes()
        command = "/bin/bash -c " + shlex.quote("cat -- /review/methods/" + method.name + "/SKILL.md")
        events = [
            {"type": "thread.started", "thread_id": "scripted-native-shape"}, {"type": "turn.started"},
            {"type": "item.started", "item": {"id": "item_0", "type": "command_execution", "command": command,
                "aggregated_output": "", "exit_code": None, "status": "in_progress"}},
            {"type": "item.completed", "item": {"id": "item_0", "type": "command_execution", "command": command,
                "aggregated_output": resource.decode(), "exit_code": 0, "status": "completed"}},
            {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": canonical_json(self.output_report)}},
            {"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0}}]
        raw = ("\n".join(canonical_json(e) for e in events) + "\n").encode()
        # CoreFoundation inserts __CF_USER_TEXT_ENCODING in native macOS children.
        body = ("import sys,os;from pathlib import Path\n"
            "text=sys.stdin.buffer.read()\nassert text\n"
            "assert set(os.environ) <= {'PATH','LANG','LC_CTYPE','__CF_USER_TEXT_ENCODING'}\n"
            "assert Path(" + repr(str(method / "SKILL.md")) + ").read_bytes()==" + repr(resource) + "\n"
            "assert Path(" + repr(str(stage / ".runtime/guard.py")) + ").exists()\n"
            + self.output_body + "\nsys.stdout.buffer.write(" + repr(raw) + ")\n")
        return sys.executable, "-I", "-c", body


@pytest.mark.parametrize("activation,required", [("required", False), ("on_demand", False), ("on_demand", True)])
def test_existing_worker_selects_concrete_adapter_and_persists_exact_native_shaped_reads(ctx, monkeypatch, activation, required):
    job, context, item, _candidate = ready_job(ctx, activation=activation, required=required)
    admission = AdmissionFixture()
    engine = ScriptedEngine(admission.selected)
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: engine)
    workers = WorkerJobStore(ctx.repo, codex_review_admission=admission)
    result = workers.run_job(job["id"])
    assert result["status"] == "review_received", result
    assert engine.attaches == 1 and engine.removed
    assert len(admission.acquires) == 1
    assert admission.finishes == [(admission.acquires[0], True)]
    detail = ReviewResultStore(workers, job["id"]).detail()
    assert detail["runtime_verification"] == "contract_fixture"
    assert detail["stages"]["resource_read_observed"] == "observed"
    assert detail["stages"]["script_execution_observed"] == "unobserved"
    assert detail["resource_reads"][0]["skill_id"] == item["skill_id"]
    assert detail["resource_reads"][0]["version_id"] == item["version_id"]
    assert "USER_BOUNDARY_X42" not in json.dumps(detail)
    assert engine.stage.stat().st_mode & 0o777 == 0o555
    assert engine.stage.parent.stat().st_mode & 0o777 == 0o700
    bind(ctx, None, subject="role:code_review", key="restore-after-concrete-adapter")
    restored = WorkerJobStore(ctx.repo)
    assert restored.run_job(job["id"])["status"] == "review_received"
    code, response = task_review_details(restored, context, job_id=job["id"], include_report=True)
    assert code == 200 and response["report"]["findings"] == []
    assert response["resource_reads"] == detail["resource_reads"]
    assert engine.attaches == 1


def test_missing_or_untyped_admission_cannot_enable_or_even_probe_codex(ctx, monkeypatch):
    job, *_ = ready_job(ctx)
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: pytest.fail("Unadmitted engine probe"))
    assert ctx.workers.run_job(job["id"])["skill_error_code"] == "skill_runtime_not_connected"
    for value in ({"verified": True}, True, "approved.json", object()):
        with pytest.raises(ValueError):
            WorkerJobStore(ctx.repo, codex_review_admission=value)


def test_admission_failure_retires_unstarted_reservation_without_probe(ctx, monkeypatch):
    job, *_ = ready_job(ctx)
    admission = AdmissionFixture(fail_check=True)
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: pytest.fail("Unadmitted engine probe"))
    result = WorkerJobStore(ctx.repo, codex_review_admission=admission).run_job(job["id"])
    assert result["skill_error_code"] == "fixture_admission_denied"
    assert admission.finishes == [(admission.acquires[0], True)]


@pytest.mark.parametrize("kind", ["provider", "outer"])
def test_successful_inner_output_cannot_publish_when_outer_or_provider_cleanup_unknown(ctx, monkeypatch, kind):
    job, context, *_ = ready_job(ctx)
    admission = AdmissionFixture(fail_finish=kind == "provider")
    engine = ScriptedEngine(admission.selected, reject_stop=kind == "outer")
    monkeypatch.setattr(codex_container, "DockerEngine", lambda: engine)
    workers = WorkerJobStore(ctx.repo, codex_review_admission=admission)
    result = workers.run_job(job["id"])
    assert result["skill_error_code"] == "review_termination_unknown", result
    detail = ReviewResultStore(workers, job["id"]).detail()
    assert detail["status"] == "stopping"
    assert not detail["report_available"] and not detail["retry_available"]
    assert workers.run_job(job["id"])["skill_runtime_status"] == "recovery_required"
    assert engine.attaches == 1


@pytest.mark.parametrize("change", [
    {"model": "--other-model"}, {"provider_url": "https://external.example/v1"},
    {"provider_url": "http://user:secret@fixture-provider/v1"}, {"provider_url": "http://fixture-provider/v1?token=secret"},
    {"provider_url": "http://127.0.0.1/v1"}, {"provider_url": "http://[::1]/v1"},
    {"provider_ipv4": "8.8.8.8"}, {"provider_ipv4": "127.0.0.1"}, {"provider_ipv4": "169.254.169.254"},
    {"image_id": "node:latest"}, {"network_id": "bridge"}, {"provider_route_ref": "../secret"},
])
def test_contract_rejects_public_endpoints_credentials_and_unpinned_inputs(change):
    with pytest.raises(SkillError):
        contract(**change)


def test_command_is_generic_exec_with_explicit_disabled_extensions_and_no_auth_sources():
    selected = contract()
    command = selected.command()
    assert command[:2] == ("/runtime/bin/codex", "exec") and command[-1] == "-"
    assert "--ignore-user-config" in command and "--strict-config" in command and "--ignore-rules" in command
    assert "review" not in command and "--resume" not in command
    for option in ("skills.bundled.enabled=false", "orchestrator.skills.enabled=false", "features.plugins=false",
                   "features.hooks=false", "shell_environment_policy.inherit=\"none\"", "web_search=\"disabled\""):
        assert option in command
    assert selected.digest != replace(selected, model="different-model").digest
    assert selected.digest != replace(selected, provider_policy_digest=content_digest(b"new-policy")).digest


def test_stage_sealing_refuses_symlinks_and_preserves_sources_as_data(tmp_path):
    stage = tmp_path / "inputs"
    stage.mkdir()
    source = stage / "sources" / "head" / "SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text("Reviewed source, not an automatically installed method.")
    (stage / "link").symlink_to(source)
    with pytest.raises(SkillError):
        seal_input_tree(stage)
    (stage / "link").unlink()
    seal_input_tree(stage)
    assert source.read_text().startswith("Reviewed source") and source.stat().st_mode & 0o777 == 0o444


@pytest.mark.parametrize("mutation", [
    lambda x: x["HostConfig"].update(Privileged=True),
    lambda x: x["HostConfig"].update(ReadonlyRootfs=False),
    lambda x: x["HostConfig"].update(NetworkMode="host"),
    lambda x: x["HostConfig"].update(Dns=["8.8.8.8"]),
    lambda x: x["HostConfig"].update(ExtraHosts=["metadata:169.254.169.254"]),
    lambda x: x["HostConfig"].update(CapAdd=["SYS_ADMIN"]),
    lambda x: x["HostConfig"].update(LogConfig={"Type": "json-file", "Config": {}}),
    lambda x: x["Config"].update(User="0"),
    lambda x: x["Config"].update(Healthcheck={"Test": ["CMD", "curl"]}),
    lambda x: x["Mounts"][0].update(RW=True),
    lambda x: x["Mounts"].append({"Type": "bind", "Source": "/var/run/docker.sock", "Destination": "/socket", "RW": True}),
])
def test_actual_container_policy_mismatch_prevents_attachment_and_removes_only_owned_container(tmp_path, mutation):
    stage, control = tmp_path / "inputs", tmp_path / "control"
    stage.mkdir()
    control.mkdir()
    selected = contract()
    engine = ScriptedEngine(selected, change=mutation)
    with pytest.raises(SkillError) as error:
        with codex_container.CodexContainer(selected, engine=engine).execution(stage, control=control,
                context_id="review-" + "1"*32, seccomp_bytes=POLICY):
            pytest.fail("Policy mismatch reached attachment")
    assert error.value.code == "codex_container_policy"
    assert engine.removed and engine.attaches == 0
    assert not any(op[0] in {"pull", "build", "run"} for op in engine.operations)


def test_other_network_peer_and_foreign_container_never_authorize_launch_or_deletion(tmp_path):
    for i, options in enumerate(({"late_peer": True}, {"change": lambda info: info["Config"]["Labels"].clear()})):
        stage, control = tmp_path / f"input{i}", tmp_path / f"control{i}"
        stage.mkdir()
        control.mkdir()
        engine = ScriptedEngine(contract(), **options)
        with pytest.raises(SkillError):
            with codex_container.CodexContainer(contract(), engine=engine).execution(stage, control=control,
                    context_id="review-" + "2"*32, seccomp_bytes=POLICY):
                pytest.fail("Invalid ownership reached attachment")
        assert engine.attaches == 0 and not engine.removed


def test_lease_identity_and_existing_context_cannot_change(ctx):
    job, *_ = ready_job(ctx)
    snapshot = ctx.service.locks.find_snapshot(command_key=job["id"])["snapshot"]
    authority = AdmissionFixture()
    runtime = PinnedCodexReviewRuntime(ctx.workers, authority)
    runtime.authorize(snapshot, context_id="review-"+"a"*32, operation="prepare")
    changed = deepcopy(snapshot)
    changed["job_input"]["request"]["task"] += " extra"
    with pytest.raises(SkillError) as error:
        runtime.authorize(changed, context_id="review-"+"a"*32, operation="input")
    assert error.value.code == "codex_context_conflict"
    with pytest.raises(SkillError):
        replace(authority.acquire(snapshot, context_id="review-"+"b"*32), seccomp_bytes=b"different")


def test_first_admission_pins_runtime_policy_for_old_task_contexts(ctx):
    job, *_ = ready_job(ctx)
    snapshot = ctx.service.locks.find_snapshot(command_key=job["id"])["snapshot"]
    authority = AdmissionFixture()
    first = PinnedCodexReviewRuntime(ctx.workers, authority)
    first.authorize(snapshot, context_id="review-"+"a"*32, operation="prepare")
    new_context = PinnedCodexReviewRuntime(ctx.workers, AdmissionFixture())
    new_context.authorize(snapshot, context_id="review-"+"b"*32, operation="prepare")
    changed_route = AdmissionFixture(selected=replace(contract(), provider_policy_digest=content_digest(b"new route policy")))
    with pytest.raises(SkillError) as error:
        PinnedCodexReviewRuntime(ctx.workers, changed_route).authorize(snapshot, context_id="review-"+"c"*32, operation="prepare")
    assert error.value.code == "codex_task_runtime_changed"


def test_container_inspects_compacted_policy_contents_not_client_filename(tmp_path):
    stage, control = tmp_path / "inputs", tmp_path / "control"
    stage.mkdir()
    control.mkdir()
    engine = ScriptedEngine(contract(), change=lambda info: info["HostConfig"].update(SecurityOpt=[
        "no-new-privileges=true", "seccomp=" + str(control / "seccomp.json")]))
    with pytest.raises(SkillError) as error:
        with codex_container.CodexContainer(contract(), engine=engine).execution(stage, control=control,
                context_id="review-" + "3"*32, seccomp_bytes=POLICY):
            pytest.fail("A filename is not proof of the daemon's loaded policy")
    assert error.value.code == "codex_container_policy" and engine.removed
