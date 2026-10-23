from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from goodteam_os.delivery_outcomes import DeliveryArtifactError, build_delivery_summary, read_delivery_artifact
from goodteam_os.source_preview import MAX_FILE_BYTES, build_source_preview, register_source_preview
from goodteam_os.state import StateStore, create_initial_state


def result_for(files):
    return {"job_id": "workflow-task", "baseline_commit": "a" * 40,
            "result_commit": "b" * 40, "changed_files": list(files), "project_key": "DEMO", "run_id": "run-one"}


def capture_for(files):
    return {"files": {name: {"mode": "100644", "data": data} for name, data in files.items()}}


def registered(tmp_path, files=None):
    files = files or {"src/main.py": b"print('exact retained source')\n"}
    state = create_initial_state("run-one", "DEMO", "Source preview")
    result = result_for(files)
    result["source_preview"] = build_source_preview(capture=capture_for(files), result=result, project_key="DEMO", run_id="run-one")
    register_source_preview(tmp_path, state, result)
    state.release_candidate["repository_result"] = result
    StateStore(tmp_path).write_project_current(state)
    return state, result


def test_exact_host_bytes_are_read_through_registered_version(tmp_path):
    state, result = registered(tmp_path)
    item = build_delivery_summary(tmp_path, state=state)["artifacts"][0]
    assert item["key"] == "source_preview"
    assert item["status"] == "available"
    content = read_delivery_artifact(tmp_path, project_key="DEMO", run_id=state.run_id,
                                     artifact_key=item["key"], version=item["version"])["content"]
    assert "print('exact retained source')\n" in content
    assert result["result_commit"] in content
    assert result["source_manifest_version"] in content
    assert "source_preview" not in result  # no source payload persisted in state
    assert sha256(content.encode()).hexdigest() == result["source_preview_version"][7:]


@pytest.mark.parametrize("name", ["/tmp/secret.py", "../escape.py", "a/../escape.py", "a//b.py", "C:\\file.py", ".env", ".env.py", "config/secrets.json", ".aws/config.py", "id_rsa.py", "credentials.json", "src/[misleading](#fake).py", "src/`fake`.py"])
def test_unsafe_or_sensitive_names_never_enter_preview(name):
    files = {name: b"DO_NOT_EXPOSE"}
    preview = build_source_preview(capture=capture_for(files), result=result_for(files), project_key="DEMO", run_id="run-one")
    assert "DO_NOT_EXPOSE" not in preview["content"]
    assert "Excluded or unavailable changed files: 1" in preview["content"]


@pytest.mark.parametrize("data", [b"\xff", b"a\x00b", b"x" * (MAX_FILE_BYTES + 1), b"-----BEGIN PRIVATE KEY-----", b'API_KEY="do-not-publish-this"', b'{"api_key": "sk-secret123456"}', b'"refresh_token": "private-token-123"', b'Authorization: Bearer abcdefghijklmnop', b'eyJheaderAA.payloadAA.signatureAAA'])
def test_binary_oversized_or_sensitive_content_is_excluded(data):
    files = {"src/file.py": data}
    preview = build_source_preview(capture=capture_for(files), result=result_for(files), project_key="DEMO", run_id="run-one")
    assert "## " not in preview["content"]
    assert "Excluded or unavailable changed files: 1" in preview["content"]


def test_symlink_mode_is_not_previewed():
    files = {"src/link.py": b"/outside/file.py"}
    capture = capture_for(files)
    capture["files"]["src/link.py"]["mode"] = "120000"
    assert "/outside" not in build_source_preview(capture=capture, result=result_for(files), project_key="DEMO", run_id="run-one")["content"]


@pytest.mark.parametrize("mutation", ["version", "content", "symlink", "outside", "identity", "result_commit"])
def test_registered_preview_fails_closed(tmp_path, mutation):
    state, result = registered(tmp_path)
    version = result["source_preview_version"]
    path = Path(state.artifacts["source_preview"])
    if mutation == "version":
        version = "sha256:" + "0" * 64
    elif mutation == "content":
        path.write_text("replacement")
    elif mutation == "symlink":
        target = tmp_path / "private.txt"
        target.write_text("private")
        path.unlink()
        path.symlink_to(target)
    elif mutation == "outside":
        state.artifacts["source_preview"] = str(tmp_path / "outside.md")
        state.resources["source_preview"].path = str(tmp_path / "outside.md")
    elif mutation == "result_commit":
        state.release_candidate["repository_result"]["result_commit"] = "c" * 40
    else:
        state.release_candidate["repository_result"]["source_preview_version"] = "sha256:other"
    StateStore(tmp_path).write_project_current(state)
    with pytest.raises(DeliveryArtifactError):
        read_delivery_artifact(tmp_path, project_key="DEMO", run_id=state.run_id,
                               artifact_key="source_preview", version=version)


def test_other_run_project_and_unregistered_source_rejected(tmp_path):
    state, result = registered(tmp_path)
    for project, run, key in [("OTHER", state.run_id, "source_preview"), ("DEMO", "other", "source_preview"), ("DEMO", state.run_id, "src/main.py")]:
        with pytest.raises(DeliveryArtifactError):
            read_delivery_artifact(tmp_path, project_key=project, run_id=run,
                                   artifact_key=key, version=result["source_preview_version"])


def test_legacy_result_does_not_create_source_artifact(tmp_path):
    state = create_initial_state("old-run", "DEMO", "Old task")
    register_source_preview(tmp_path, state, {"result_commit": "b" * 40})
    assert "source_preview" not in state.artifacts
    assert not (tmp_path / "artifacts").exists()


def test_host_collection_seals_second_capture_without_reading_report_paths(tmp_path, monkeypatch):
    import goodteam_os.dispatch_workspace as module

    files = {"src/main.py": b"host captured bytes\n"}
    capture = capture_for(files)
    monkeypatch.setattr(module, "_capture", lambda *a, **k: capture)
    monkeypatch.setattr(module, "_capture_identity", lambda value: value)
    monkeypatch.setattr(module, "_raw_commit", lambda *a: "b" * 40)
    monkeypatch.setattr(module, "_git", lambda *a, **k: SimpleNamespace(stdout=b"src/main.py\0"))
    workspace = module.DispatchWorkspace(tmp_path, {"baseline_commit": "a" * 40, "files": {}}, None)
    monkeypatch.setattr(workspace, "_verify_job", lambda job: tmp_path)
    result = workspace.collect("workflow-task", include_source_preview=True, source_preview_scope=("DEMO", "run-one"))
    assert "host captured bytes" in result["source_preview"]["content"]
    assert result["result_commit"] in result["source_preview"]["content"]


def test_read_endpoint_never_invokes_collector(tmp_path, monkeypatch):
    import goodteam_os.dispatch_workspace as module
    state, result = registered(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("read called collector or Git")
    monkeypatch.setattr(module, "_git", forbidden)
    monkeypatch.setattr(module.DispatchWorkspace, "collect", forbidden)
    assert read_delivery_artifact(tmp_path, project_key="DEMO", run_id=state.run_id,
                                  artifact_key="source_preview", version=result["source_preview_version"])["content"]


@pytest.mark.parametrize("field", ["project_key", "run_id", "result_commit", "baseline_commit", "job_id"])
def test_mixed_task_result_bundle_is_rejected(tmp_path, field):
    files = {"src/main.py": b"print('safe')\n"}
    state = create_initial_state("run-one", "DEMO", "Task")
    result = result_for(files)
    result["source_preview"] = build_source_preview(capture=capture_for(files), result=result, project_key="DEMO", run_id="run-one")
    result[field] = "other"
    with pytest.raises(ValueError, match="does not belong"):
        register_source_preview(tmp_path, state, result)
    assert not (tmp_path / "artifacts").exists()


def test_graph_registers_final_host_preview_without_git_or_model(tmp_path, monkeypatch):
    import goodteam_os.graph as graph
    import goodteam_os.workflow_workspace as workflow
    state = create_initial_state("run-one", "DEMO", "Task")
    state.repository_baseline = {"fixture": "host binding"}
    config = tmp_path / "project.yaml"
    config.write_text("fixture")
    monkeypatch.setattr(graph, "load_project_target", lambda path: {"repos": {}})
    monkeypatch.setattr(graph, "implementation_worker_job_specs", lambda *a, **k: [{"role": "implementation"}])
    monkeypatch.setattr(graph, "resolve_project_runtime_root", lambda repos: tmp_path)
    monkeypatch.setattr(graph, "WorkerJobStore", lambda root: object())
    monkeypatch.setattr(graph, "run_worker_job_with_retry", lambda **kwargs: {"status": "completed"})
    calls = []
    def collect(root, binding, **kwargs):
        calls.append(kwargs)
        result = result_for({"src/main.py": b"fixture source\n"})
        result["source_preview"] = build_source_preview(capture=capture_for({"src/main.py": b"fixture source\n"}),
                                                        result=result, project_key="DEMO", run_id="run-one")
        return result
    monkeypatch.setattr(workflow, "collect_workflow_workspace", collect)
    results = graph.run_implementation_workers(state, config, workspace=tmp_path)
    assert calls == [{"project_key": "DEMO", "run_id": "run-one", "include_source_preview": True}]
    assert results[-1]["repository_result"] == state.release_candidate["repository_result"]
    assert "source_preview" not in results[-1]["repository_result"]
    StateStore(tmp_path).write_project_current(state)
    content = read_delivery_artifact(tmp_path, project_key="DEMO", run_id="run-one", artifact_key="source_preview",
                                    version=state.release_candidate["repository_result"]["source_preview_version"])["content"]
    assert "fixture source\n" in content


def test_registration_rejects_symlink_destination_without_touching_target(tmp_path):
    from goodteam_os.memory_storage import MemoryStorageError
    state = create_initial_state("run-one", "DEMO", "Task")
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "artifacts").symlink_to(outside, target_is_directory=True)
    files = {"src/main.py": b"safe bytes\n"}
    result = result_for(files)
    result["source_preview"] = build_source_preview(capture=capture_for(files), result=result,
                                                    project_key="DEMO", run_id="run-one")
    with pytest.raises(MemoryStorageError):
        register_source_preview(tmp_path, state, result)
    assert list(outside.iterdir()) == []
    assert "source_preview" not in state.artifacts


def test_total_output_bound_and_exclusion_reasons():
    from goodteam_os.source_preview import MAX_PREVIEW_BYTES
    files = {f"src/file{i}.py": b"x" * MAX_FILE_BYTES for i in range(20)}
    result = result_for(files)
    preview = build_source_preview(capture=capture_for(files), result=result,
                                   project_key="DEMO", run_id="run-one")
    assert len(preview["content"].encode()) <= MAX_PREVIEW_BYTES
    assert "total preview limit" in preview["content"]


def test_frozen_bytes_do_not_follow_source_after_registration(tmp_path):
    source = tmp_path / "source.py"
    source.write_bytes(b"original exact source\n")
    state, result = registered(tmp_path, {"src/source.py": source.read_bytes()})
    source.write_bytes(b"replacement unrelated bytes\n")
    content = read_delivery_artifact(tmp_path, project_key="DEMO", run_id=state.run_id,
                                     artifact_key="source_preview", version=result["source_preview_version"])["content"]
    assert "original exact source\n" in content
    assert "replacement unrelated bytes" not in content


@pytest.mark.parametrize("mismatch", [False, True])
def test_release_preserves_only_same_host_candidate_binding(tmp_path, monkeypatch, mismatch):
    import goodteam_os.workflow_workspace as workflow
    from goodteam_os.release_artifacts import ReleaseCandidateError, retain_repository_candidate
    state, result = registered(tmp_path)
    state.repository_baseline = {"source_root": str(tmp_path)}
    def collect(*args, **kwargs):
        candidate = {key: value for key, value in result.items() if key not in {"source_preview_version", "source_manifest_version"}}
        if kwargs.get("candidate"):
            candidate["integration"] = {"status": "succeeded"}
            if mismatch:
                candidate["project_key"] = "OTHER"
        return candidate
    monkeypatch.setattr(workflow, "collect_workflow_workspace", collect)
    if mismatch:
        with pytest.raises(ReleaseCandidateError, match="different task candidate"):
            retain_repository_candidate(state)
        return
    retain_repository_candidate(state)
    StateStore(tmp_path).write_project_current(state)
    assert read_delivery_artifact(tmp_path, project_key="DEMO", run_id="run-one", artifact_key="source_preview",
                                  version=result["source_preview_version"])["content"]


def test_registered_source_preview_is_read_only_and_generic_read_is_bound(tmp_path):
    from goodteam_os.resources import ResourceCapability
    from goodteam_os.web import read_artifact
    state, result = registered(tmp_path)
    assert ResourceCapability.EDIT not in state.resources['source_preview'].capabilities
    generic = read_artifact(workspace=tmp_path, project_key='DEMO', artifact_key='source_preview')
    assert generic['artifact_version'] == result['source_preview_version']
    assert generic['content'] == read_delivery_artifact(
        tmp_path, project_key='DEMO', run_id=state.run_id, artifact_key='source_preview',
        version=result['source_preview_version'])['content']


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('project_key', ['', 'DEMO'])
def test_source_save_rejected_without_mutating_evidence_or_state(tmp_path, legacy, project_key):
    from goodteam_os.resources import ResourceCapability
    from goodteam_os.web import ProjectApiError, save_artifact
    state, result = registered(tmp_path)
    if legacy:
        state.resources['source_preview'].capabilities.append(ResourceCapability.EDIT)
    store = StateStore(tmp_path)
    store.write_project_current(state)
    store.write_current(state)
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises(ProjectApiError, match='read-only'):
        save_artifact(workspace=tmp_path, project_key=project_key, artifact_key='source_preview',
                      content='replacement evidence', expected_run_id=state.run_id,
                      expected_artifact_version=result['source_preview_version'])
    after = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    # Workflow serialization may create a lock file; no evidence/state/log bytes may change.
    assert all(after.get(name) == content for name, content in before.items())
    assert not any('edit-log' in name for name in after)
    assert read_delivery_artifact(tmp_path, project_key='DEMO', run_id=state.run_id,
                                  artifact_key='source_preview', version=result['source_preview_version'])['content']


@pytest.mark.parametrize('mutation', ['bytes', 'identity', 'symlink', 'registry_path'])
@pytest.mark.parametrize('reader', ['artifact', 'questions'])
def test_generic_source_reads_reject_unbound_evidence(tmp_path, mutation, reader):
    from goodteam_os.web import read_artifact, read_open_questions
    state, _ = registered(tmp_path)
    path = Path(state.artifacts['source_preview'])
    if mutation == 'bytes':
        path.write_text('# Open Questions\n- forged evidence?\n')
    elif mutation == 'identity':
        state.release_candidate['repository_result']['result_commit'] = 'c' * 40
    elif mutation == 'registry_path':
        other = path.parent / 'other.md'
        other.write_text('unbound text')
        state.resources['source_preview'].path = str(other)
    else:
        other = path.parent / 'other.md'
        other.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(other)
    StateStore(tmp_path).write_project_current(state)
    fn = read_artifact if reader == 'artifact' else read_open_questions
    with pytest.raises(DeliveryArtifactError):
        fn(workspace=tmp_path, project_key='DEMO', artifact_key='source_preview')


def test_legacy_artifacts_only_preview_remains_readable_but_not_editable(tmp_path):
    from goodteam_os.web import ProjectApiError, read_artifact, save_artifact
    state, result = registered(tmp_path)
    state.resources.clear()
    StateStore(tmp_path).write_project_current(state)
    artifact = read_artifact(workspace=tmp_path, project_key='DEMO', artifact_key='source_preview')
    assert artifact['artifact_version'] == result['source_preview_version']
    with pytest.raises(ProjectApiError, match='read-only'):
        save_artifact(workspace=tmp_path, project_key='DEMO', artifact_key='source_preview', content='replacement')
