"""New production dispatch producer + broker + Runtime with explicit workspace/listener doubles.
Not real Git baseline, installed Pi SDK, or model acceptance.
"""

from types import SimpleNamespace
import pytest
from test_manager_pi_method_producer import setup
from test_manager_pi_runtime import fake_manager_pi
from goodteam_os.dispatch import dispatch_tracks
from goodteam_os.skills.pi_methods import PiMethodInput
from goodteam_os.skills.pi_resources import PiResourceChannel
from goodteam_os.session_runtimes import PiSessionRuntime, PiRuntimeConfig
from goodteam_os.pi_read_only import PiReadOnlyPolicyError


@pytest.mark.parametrize("missing", [False, True])
def test_dispatch_readonly_to_real_fake_rpc_restore(tmp_path, monkeypatch, missing):
    monkeypatch.setenv("GOODTEAM_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GOODTEAM_HOME", str(tmp_path / "home"))
    root, config, team, workers = setup(tmp_path)
    import goodteam_os.dispatch_workspace as spaces

    class BeforeGit(Exception):
        pass

    seen = []

    def capture(root, run_id, job_ids, **kwargs):
        seen.extend(job_ids)
        raise BeforeGit()

    monkeypatch.setattr(spaces, "prepare_dispatch_workspace", capture)
    with pytest.raises(BeforeGit):
        dispatch_tracks(
            project_root=root,
            require_isolation=True,
            logical_task_id="readonly-one",
            tracks=[
                dict(
                    role="research",
                    adapter="pi",
                    model="fixture",
                    execution_policy="read_only",
                    task="Read only the supplied repo.",
                    context="Original investigation.",
                    track_id="task-one",
                    project_task_id="task-one",
                )
            ],
        )
    job = workers._read_json(workers.root / seen[0] / "job.json")
    assert job["method_loading_mode"] == "readonly_pi_methods.v1"
    target = tmp_path / "fixture-mapping"
    target.mkdir()
    (target / ".git").mkdir()

    def mapping(source, run_id, ids, **kw):
        assert source == root and run_id == job["run_id"] and ids is None
        return SimpleNamespace(workspace=lambda id: target if id == job["id"] else None)

    monkeypatch.setattr(spaces, "prepare_dispatch_workspace", mapping)
    method = PiMethodInput(workers, job["id"])
    assert method.readonly_tools and not method.manager_tools
    method.bind_workspace(
        dict(
            available=True,
            mode="dispatch_isolated",
            workspace_path=str(target),
            execution_id=job["run_id"],
            sandbox_id=job["id"],
        )
    )
    channels = []

    def witness(channel):
        channels.append(channel)
        channel._check_context()
        p = channel.startup_policy
        if not missing:
            assert (
                channel.call(
                    dict(
                        operation="tool_policy_witness",
                        version=p.version,
                        nonce=p.nonce,
                        active=list(p.tools),
                        available=list(p.tools),
                    )
                )["status"]
                == "policy_witness_received"
            )
        return {}

    monkeypatch.setattr(PiResourceChannel, "start", witness)
    monkeypatch.setattr("goodteam_os.session_runtimes.PI_STARTUP_TIMEOUT_FLOOR_SECONDS", 0.02)
    fake = fake_manager_pi(tmp_path)
    cfg = PiRuntimeConfig(str(fake), "", "", "", tmp_path / "sessions", 0.1 if missing else 2)

    def runtime():
        return PiSessionRuntime(
            project_root=root,
            job_root=workers.root / job["id"],
            job=job,
            runtime_cwd=target,
            config=cfg,
            stderr_path=tmp_path / "err",
            method_input=method,
        )

    first = runtime()
    if missing:
        try:
            with pytest.raises(PiReadOnlyPolicyError):
                first.start_or_resume()
            assert not first.metadata.get("initial_prompt_sent")
            with pytest.raises(PiReadOnlyPolicyError):
                first.rpc.request({"type": "prompt", "message": "must not send"})
        finally:
            first.close()
        return
    try:
        first.start_or_resume()
        assert first.manager_policy is None and first.tool_policy is first.readonly_policy
        assert "--no-mcp" in first.rpc.command
        assert set(first.rpc.command[first.rpc.command.index("--tools") + 1].split(",")) == {
            "read",
            "grep",
            "find",
            "ls",
            "goodteam_skill_resource",
        }
        for command in ("bash", "switch_session", "export_html"):
            with pytest.raises(PiReadOnlyPolicyError):
                first.rpc.request({"type": command})
        first.send_prompt(method.prompt())
        first.wait_until_settled(3)
        original = first.metadata["active_operation"]["id"]
        nonce = first.readonly_policy.nonce
    finally:
        first.close()
    resumed = runtime()
    try:
        resumed.start_or_resume()
        assert "--session" in resumed.rpc.command and resumed.already_settled
        assert resumed.readonly_policy.nonce != nonce
        assert resumed.metadata["active_operation"]["id"] == original
        assert resumed.metadata["readonly_tool_contract"]["guarantee"] == "tool-level; not an OS sandbox"
    finally:
        resumed.close()
