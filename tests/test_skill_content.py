"""S5 contracts use real pinned packages, but no real Runtime support is claimed."""
from copy import deepcopy
import json
from uuid import uuid4

import pytest

from goodteam_os.skills.content import ContentLimits, ContentSession, SkillContextProvider
from goodteam_os.skills.models import SkillConflict, SkillError, SkillSource, content_digest
from goodteam_os.skills.package import PackageFile, package_from_files
from goodteam_os.skills.task_context import task_skill_context
from goodteam_os.skills.task_locks import TaskSkillIdentity, TaskSkillPlan
from test_worker_skill_requests import ctx as ctx


class FixtureAuthority:
    """Injected test-only authority; never installed or accepted by production."""
    def __init__(self):
        self.calls = []
        self.config = content_digest(b"fixture-config")
        self.denied = False

    def authorize(self, snapshot, *, context_id, operation):
        self.calls.append((context_id, operation))
        if self.denied:
            raise SkillError("runtime_unverified", "Fixture authority denied")
        request = snapshot["job_input"]["request"]
        return ContentSession(request["adapter"], "fixture-adapter-v1", "fixture-only",
                              content_digest(b"not-a-real-binary"), request["model"], self.config,
                              "fixture-" + context_id, "fixture-not-conformance", "contract_fixture")


def job(ctx, *, activation="required", header="", body="Use the reference.\n", required=False, source=None):
    token = uuid4().hex
    content = (f"---\nname: arbitrary-{token}\ndescription: A previously unknown method.\n{header}---\n" + body).encode()
    package = package_from_files((PackageFile("SKILL.md", content),
                                  PackageFile("references/detail.txt", b"original pinned resource\n"),
                                  PackageFile("references/script.py", b"raise RuntimeError('never execute')\n", True)),
                                 directories=("references",))
    published = ctx.packages.publish(package, source or SkillSource("local", str(ctx.root / token)), command_key=token)
    item = {"skill_id": published.installed.skill_id, "version_id": published.version.version_id,
            "package_digest": package.digest, "activation": activation, "enabled": True}
    binding = deepcopy(ctx.plan.bindings["role:code_review"])
    binding["items"] = [item]
    plan = TaskSkillPlan(2, {"role:code_review": binding}, ctx.plan.role_contracts)
    task = TaskSkillIdentity("workflow_run", "DEMO", "run-" + token)
    ref = ctx.locks.begin(task, command_key=token, task_input={"request": "Review"}, resolve_plan=lambda: plan)
    request = {**ctx.request, "run_id": task.logical_id, "idempotency_key": "job-" + token,
               "task_skill_context": task_skill_context(task, ref),
               "required_skill_ids": (item["skill_id"],) if required else ()}
    created = ctx.store.create_job(**request)
    return created, item, content, published.version.source


def provider(ctx, created, *, authority=None, limits=None):
    return SkillContextProvider(ctx.store, created["id"], authority=authority or FixtureAuthority(), limits=limits)


def test_real_worker_prepare_boundary_remains_closed_without_440(ctx):
    created, _item, _content, _source = job(ctx)
    with pytest.raises(SkillError) as error:
        SkillContextProvider(ctx.store, created["id"]).prepare("new-context")
    assert error.value.code == "runtime_conformance_unverified"
    assert ctx.store.run_job(created["id"])["skill_error_code"] == "skill_runtime_not_connected"
    assert not (ctx.operator / "skills" / "content-contexts").exists()
    assert ctx.calls == []


def test_required_input_is_complete_private_original_bytes_and_never_delivery_claim(ctx):
    created, item, content, _source = job(ctx, body="αβ\n" + "unchanged method\n" * 100)
    selected = provider(ctx, created)
    ref = selected.prepare("context-a")
    inputs = selected.private_input(ref)
    assert inputs["required"][0]["text"].encode() == content
    assert inputs["required"][0]["digest"] == content_digest(content)
    assert inputs["original_job_input"]["files"]["task.md"] == ctx.request["task"]
    assert inputs["original_job_input"]["files"]["research-report.md"] == "Original research.\n"
    assert inputs["catalog"][0]["skill_id"] == item["skill_id"]
    detail = selected.describe(ref)
    assert detail["status"] == "prepared_only"
    assert detail["runtime_verification"] == "contract_fixture"
    assert set(detail["stages"].values()) == {"unobserved"}
    assert "unchanged method" not in json.dumps(detail)
    assert str(ctx.operator) not in json.dumps(detail)
    assert "Original research" not in json.dumps(detail)
    assert ctx.calls == []


def test_on_demand_starts_with_catalog_then_broker_reads_exact_bound_resources(ctx):
    created, item, content, _source = job(ctx, activation="on_demand")
    selected = provider(ctx, created)
    ref = selected.prepare("context-a")
    assert selected.private_input(ref)["required"] == []
    assert selected.read_resource(ref, skill_id=item["skill_id"], relative="SKILL.md", call_id="read-1") == content
    assert selected.read_resource(ref, skill_id=item["skill_id"], relative="references/detail.txt", call_id="read-2") == b"original pinned resource\n"
    scripts = selected.read_resource(ref, skill_id=item["skill_id"], relative="references/script.py", call_id="read-3")
    assert b"never execute" in scripts
    assert set(selected.describe(ref)["stages"].values()) == {"unobserved"}
    receipts = list((ctx.operator / "skills" / "content-contexts" / created["id"]).glob("read-*.json"))
    assert len(receipts) == 3
    assert all(json.loads(path.read_text())["runtime_read_observed"] is False for path in receipts)
    assert ctx.calls == []


def test_task_required_on_demand_item_is_fully_injected(ctx):
    created, _item, content, _source = job(ctx, activation="on_demand", required=True)
    selected = provider(ctx, created)
    assert selected.private_input(selected.prepare("a"))["required"][0]["text"].encode() == content


@pytest.mark.parametrize("relative", ["../SKILL.md", "/etc/passwd", "references/../SKILL.md", "references\\detail.txt", "file:///secret", "", "references//detail.txt"])
def test_arbitrary_resource_paths_are_rejected(ctx, relative):
    created, item, _content, _source = job(ctx)
    selected = provider(ctx, created)
    ref = selected.prepare("a")
    with pytest.raises(SkillError):
        selected.read_resource(ref, skill_id=item["skill_id"], relative=relative, call_id="invalid")
    assert ctx.calls == []


def test_unbound_and_missing_resources_cannot_read_latest_or_other_package(ctx):
    created, item, _content, _source = job(ctx)
    _other, other_item, _body, _src = job(ctx)
    selected = provider(ctx, created)
    ref = selected.prepare("a")
    with pytest.raises(SkillError, match="subset"):
        selected.read_resource(ref, skill_id=other_item["skill_id"], relative="SKILL.md", call_id="x")
    with pytest.raises(SkillError, match="does not exist"):
        selected.read_resource(ref, skill_id=item["skill_id"], relative="missing", call_id="x")


def test_same_context_replay_and_new_context_restore_exact_old_snapshot(ctx):
    created, _item, content, source = job(ctx)
    selected = provider(ctx, created)
    ref = selected.prepare("a")
    _other, _other_item, _other_content, _source = job(ctx, source=source, body="New v2; not the original task.\n")
    ctx.research.write_text("Changed mutable source must never replace old research")
    assert selected.prepare("a") == ref
    later = selected.prepare("b")
    assert later["snapshot_ref"] == ref["snapshot_ref"]
    assert later != ref
    assert selected.private_input(later)["required"][0]["text"].encode() == content
    assert selected.private_input(later)["original_job_input"]["files"]["research-report.md"] == "Original research.\n"


def test_public_input_objects_are_not_shared_or_writable_authority(ctx):
    created, _item, content, _source = job(ctx)
    selected = provider(ctx, created)
    ref = selected.prepare("a")
    first = selected.private_input(ref)
    first["required"][0]["text"] = "mutated"
    first["catalog"].clear()
    assert selected.private_input(ref)["required"][0]["text"].encode() == content
    assert selected.private_input(ref)["catalog"]


def test_changed_runtime_session_is_not_a_valid_context_replay(ctx):
    created, _item, _content, _source = job(ctx)
    authority = FixtureAuthority()
    selected = provider(ctx, created, authority=authority)
    ref = selected.prepare("a")
    authority.config = content_digest(b"different-config")
    with pytest.raises(SkillConflict):
        selected.prepare("a")
    with pytest.raises(SkillConflict):
        selected.private_input(ref)


def test_current_adapter_denial_blocks_each_later_input_and_resource(ctx):
    created, item, _content, _source = job(ctx)
    authority = FixtureAuthority()
    selected = provider(ctx, created, authority=authority)
    ref = selected.prepare("a")
    authority.denied = True
    with pytest.raises(SkillError):
        selected.private_input(ref)
    with pytest.raises(SkillError):
        selected.read_resource(ref, skill_id=item["skill_id"], relative="SKILL.md", call_id="x")
    assert ctx.calls == []


def test_request_allow_flag_cannot_replace_control_plane_authority(ctx):
    created, _item, _content, _source = job(ctx)
    class InvalidAuthority:
        def authorize(self, *_args, **_kwargs):
            return {"supported": True, "verified": True}
    with pytest.raises(SkillError, match="allow flag"):
        provider(ctx, created, authority=InvalidAuthority()).prepare("a")
    assert ctx.calls == []


@pytest.mark.parametrize("header,body", [
    ("allowed-tools: Read\n", "Instructions\n"), ("hooks: {}\n", "Instructions\n"),
    ("model: another\n", "Instructions\n"), ("", "!`curl example.test`\n"), ("", ""),
])
def test_required_vendor_execution_semantics_are_not_silently_accepted(ctx, header, body):
    created, _item, _content, _source = job(ctx, header=header, body=body)
    with pytest.raises(SkillError) as error:
        provider(ctx, created).prepare("a")
    assert error.value.code == "skill_content_unsupported"
    assert not (ctx.operator / "skills" / "content-contexts").exists()
    assert ctx.calls == []


def test_descriptive_extensions_warn_without_execution(ctx):
    created, _item, _content, _source = job(ctx, header="custom-description: retained\n")
    selected = provider(ctx, created)
    inputs = selected.private_input(selected.prepare("a"))
    assert any(warning["code"] == "extension_preserved" for warning in inputs["catalog"][0]["warnings"])


def test_input_budget_counts_original_inputs_and_never_truncates(ctx):
    created, _item, _content, _source = job(ctx)
    with pytest.raises(SkillError, match="Complete generation input"):
        provider(ctx, created, limits=ContentLimits(max_input_bytes=100)).prepare("a")
    assert not (ctx.operator / "skills" / "content-contexts").exists()
    selected = provider(ctx, created)
    ref = selected.prepare("a")
    with pytest.raises(SkillError, match="current context budget"):
        provider(ctx, created, limits=ContentLimits(max_input_bytes=100)).private_input(ref)


def test_resource_budget_has_no_partial_read_fallback(ctx):
    created, item, _content, _source = job(ctx)
    selected = provider(ctx, created, limits=ContentLimits(max_resource_bytes=3))
    ref = selected.prepare("a")
    with pytest.raises(SkillError, match="no partial read"):
        selected.read_resource(ref, skill_id=item["skill_id"], relative="SKILL.md", call_id="large")


def test_resource_call_identity_replays_exactly_and_conflicts_on_changed_request(ctx):
    created, item, content, _source = job(ctx)
    selected = provider(ctx, created)
    ref = selected.prepare("a")
    assert selected.read_resource(ref, skill_id=item["skill_id"], relative="SKILL.md", call_id="once") == content
    assert selected.read_resource(ref, skill_id=item["skill_id"], relative="SKILL.md", call_id="once") == content
    with pytest.raises(SkillConflict):
        selected.read_resource(ref, skill_id=item["skill_id"], relative="references/detail.txt", call_id="once")


def test_worker_file_mutation_and_cross_job_reference_are_rejected(ctx):
    created, _item, _content, _source = job(ctx)
    other, _other_item, _body, _src = job(ctx)
    selected = provider(ctx, created)
    ref = selected.prepare("a")
    with pytest.raises(SkillError):
        provider(ctx, other).private_input(ref)
    (ctx.store.root / created["id"] / "task.md").write_text("changed")
    with pytest.raises(SkillError):
        selected.private_input(ref)
    with pytest.raises(SkillError):
        selected.prepare("b")


def test_context_digest_corruption_is_not_repaired_or_accepted(ctx):
    created, _item, _content, _source = job(ctx)
    selected = provider(ctx, created)
    ref = selected.prepare("a")
    directory = ctx.operator / "skills" / "content-contexts" / created["id"]
    context = next(directory.glob("*.json"))
    before = context.read_bytes()
    context.write_text("{}")
    with pytest.raises(SkillError):
        selected.private_input(ref)
    with pytest.raises(SkillConflict):
        selected.prepare("a")
    assert context.read_text() == "{}"
    assert before != b"{}"


def test_current_revocation_blocks_prepared_contexts_and_preserves_original_pins(ctx):
    created, item, _content, _source = job(ctx)
    selected = provider(ctx, created)
    ref = selected.prepare("a")
    before = ctx.packages.retention_pins(item["skill_id"], item["version_id"])
    ctx.status.revoke(item["skill_id"], item["version_id"], actor="user", reason="safety", command_key="revoke-content", expected_revision=0)
    with pytest.raises(SkillError):
        selected.private_input(ref)
    with pytest.raises(SkillError):
        selected.read_resource(ref, skill_id=item["skill_id"], relative="SKILL.md", call_id="x")
    assert ctx.packages.retention_pins(item["skill_id"], item["version_id"]) == before
    assert ctx.calls == []


def test_package_api_retains_fixed_binary_bytes_and_never_reopens_source(ctx):
    created, item, _content, _source = job(ctx)
    package = ctx.packages.read_package(item["skill_id"], item["version_id"])
    assert package.digest == item["package_digest"]
    assert package.resource("references/script.py").startswith(b"raise RuntimeError")
    assert created["skill_execution_snapshot_ref"]


def test_concurrent_preparation_publishes_one_complete_context(ctx):
    from concurrent.futures import ThreadPoolExecutor

    created, _item, _content, _source = job(ctx)
    selected = provider(ctx, created)
    with ThreadPoolExecutor(max_workers=2) as pool:
        refs = list(pool.map(selected.prepare, ["a", "a"]))
    assert refs[0] == refs[1]
    directory = ctx.operator / "skills" / "content-contexts" / created["id"]
    assert len(list(directory.glob("*.json"))) == 1
    assert not list(directory.glob(".stage-*"))


def test_current_status_unknown_blocks_existing_context_reads(ctx, monkeypatch):
    from goodteam_os.skills.availability import SkillAvailabilityStore

    created, _item, _content, _source = job(ctx)
    selected = provider(ctx, created)
    ref = selected.prepare("a")
    monkeypatch.setattr(SkillAvailabilityStore, "current_status", lambda *_args: {"available": True})
    with pytest.raises(SkillError, match="current status"):
        selected.private_input(ref)


def test_lost_task_pin_is_not_recreated_by_content_preparation(ctx):
    created, item, _content, _source = job(ctx)
    pins = ctx.packages.retention_pins(item["skill_id"], item["version_id"])
    for pin in pins:
        ctx.packages.release_pin(item["skill_id"], item["version_id"], holder_id=pin["holder_id"], command_key=uuid4().hex)
    with pytest.raises(SkillError, match="pin is missing"):
        provider(ctx, created).prepare("a")
    assert ctx.packages.retention_pins(item["skill_id"], item["version_id"]) == ()


def test_budget_change_cannot_rewrite_an_existing_context(ctx):
    created, _item, _content, _source = job(ctx)
    selected = provider(ctx, created)
    ref = selected.prepare("a")
    with pytest.raises(SkillConflict):
        provider(ctx, created, limits=ContentLimits(max_input_bytes=2 * 1024 * 1024)).prepare("a")
    assert selected.prepare("a") == ref


def test_catalog_budget_rejects_before_publication(ctx):
    created, _item, _content, _source = job(ctx)
    with pytest.raises(SkillError, match="catalog exceeds"):
        provider(ctx, created, limits=ContentLimits(max_catalog_bytes=1)).prepare("a")
    assert not (ctx.operator / "skills" / "content-contexts").exists()


def test_context_reference_cannot_substitute_another_snapshot(ctx):
    created, _item, _content, _source = job(ctx)
    selected = provider(ctx, created)
    ref = selected.prepare("a")
    invalid = deepcopy(ref)
    invalid["snapshot_ref"]["digest"] = content_digest(b"other snapshot")
    with pytest.raises(SkillError, match="another snapshot"):
        selected.private_input(invalid)
    assert selected.prepare("a") == ref


def test_revoke_during_resource_capture_prevents_return(ctx, monkeypatch):
    from goodteam_os.skills.store import SkillStore

    created, item, _content, _source = job(ctx)
    selected = provider(ctx, created)
    ref = selected.prepare("a")
    original = SkillStore.read_resource

    def revoked(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        ctx.status.revoke(item["skill_id"], item["version_id"], actor="user", reason="safety",
                          command_key="revoke-during-read", expected_revision=0)
        return result

    monkeypatch.setattr(SkillStore, "read_resource", revoked)
    with pytest.raises(SkillError):
        selected.read_resource(ref, skill_id=item["skill_id"], relative="SKILL.md", call_id="x")
    assert not list((ctx.operator / "skills" / "content-contexts" / created["id"]).glob("read-*.json"))
    assert ctx.calls == []


def test_unpublished_worker_cannot_prepare_content(ctx):
    created, _item, _content, _source = job(ctx)
    receipt = ctx.operator / "skills" / "worker-requests" / (created["id"] + ".published.json")
    receipt.unlink()
    with pytest.raises(SkillError, match="completely published"):
        provider(ctx, created).prepare("a")
    assert not (ctx.operator / "skills" / "content-contexts").exists()


def test_other_worker_root_cannot_use_registered_context(ctx):
    from goodteam_os.workers import WorkerJobStore

    created, _item, _content, _source = job(ctx)
    other = ctx.root / "other-root"
    other.mkdir()
    with pytest.raises(SkillError, match="another Worker scope"):
        SkillContextProvider(WorkerJobStore(other), created["id"], authority=FixtureAuthority()).prepare("a")


def test_interrupted_context_publication_replays_without_partial_visible_input(ctx, monkeypatch):
    from goodteam_os.skills import content as module

    created, _item, _content, _source = job(ctx)
    selected = provider(ctx, created)
    original = module.os.rename

    def interrupted(source, destination, **kwargs):
        if source.startswith(".stage-content-"):
            raise OSError("simulated interruption before atomic publication")
        return original(source, destination, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(module.os, "rename", interrupted)
        with pytest.raises(SkillError) as error:
            selected.prepare("a")
        assert error.value.code == "filesystem_unavailable"
        assert isinstance(error.value.__cause__, OSError)
        assert "simulated interruption" in str(error.value.__cause__)
    directory = ctx.operator / "skills" / "content-contexts" / created["id"]
    assert not list(directory.glob("*.json"))
    ref = selected.prepare("a")
    assert selected.private_input(ref)["required"]
    assert ctx.calls == []


@pytest.mark.parametrize("job_id", [None, 12, True, {}, "job-not-skill"])
def test_invalid_worker_identity_is_rejected_before_any_content_io(ctx, job_id):
    with pytest.raises(SkillError) as error:
        SkillContextProvider(ctx.store, job_id)
    assert error.value.code == "worker_skill_identity_invalid"
    assert not (ctx.operator / "skills" / "content-contexts").exists()
    assert ctx.calls == []
