import json
import subprocess

from goodteam_os.workers import WorkerJobStore


def test_worker_job_store_creates_protocol_files(tmp_path):
    store = WorkerJobStore(tmp_path)

    job = store.create_job(
        run_id="run-1",
        role="architect",
        adapter="human",
        task="Draft RFC",
        context="Approved PRD path: prd.md",
        constraints=["Do not change code."],
        expected_artifacts=["rfc.md"],
    )

    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / job["id"]
    assert job["status"] == "queued"
    assert (job_root / "task.md").read_text(encoding="utf-8") == "Draft RFC"
    assert (job_root / "context.md").read_text(encoding="utf-8") == "Approved PRD path: prd.md"
    assert "You are the Architect role." in (job_root / "role_prompt.md").read_text(encoding="utf-8")
    memory_paths = json.loads((job_root / "role_memory_paths.json").read_text(encoding="utf-8"))
    assert memory_paths["short-term"].endswith(".goodteam/memory/roles/architect/short-term.md")
    assert (tmp_path / ".goodteam" / "memory" / "roles" / "architect" / "short-term.md").is_file()
    pickup_packet = json.loads((job_root / "role_pickup_packet.json").read_text(encoding="utf-8"))
    assert pickup_packet["role_key"] == "architect"
    assert "short_term" in pickup_packet
    assert "summaries" in pickup_packet
    assert "Do not change code." in (job_root / "constraints.md").read_text(encoding="utf-8")
    assert json.loads((job_root / "job.json").read_text(encoding="utf-8"))["adapter"] == "human"


def test_worker_role_prompt_is_stable_and_task_is_separate(tmp_path):
    store = WorkerJobStore(tmp_path)

    job = store.create_job(
        run_id="run-1",
        role="product_requirement",
        adapter="human",
        task="Take over the configured project and inspect docs.",
        context="Context packet",
    )

    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / job["id"]
    role_prompt = (job_root / "role_prompt.md").read_text(encoding="utf-8")

    assert "You are the Product / Requirement role." in role_prompt
    assert "Take over the configured project" not in role_prompt
    assert "Take over the configured project" in (job_root / "task.md").read_text(encoding="utf-8")


def test_worker_pickup_packet_does_not_embed_full_long_memory(tmp_path):
    store = WorkerJobStore(tmp_path)
    role_root = tmp_path / ".goodteam" / "memory" / "roles" / "planner"
    role_root.mkdir(parents=True)
    (role_root / "long-term.md").write_text(
        "# Planner Long-Term Memory\n\nDO_NOT_EMBED_FULL_LONG_MEMORY\n",
        encoding="utf-8",
    )

    job = store.create_job(
        run_id="run-1",
        role="planner",
        adapter="human",
        task="Create plan",
        context="Approved RFC.",
    )

    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / job["id"]
    pickup_packet = (job_root / "role_pickup_packet.json").read_text(encoding="utf-8")

    assert "DO_NOT_EMBED_FULL_LONG_MEMORY" not in pickup_packet


def test_noop_adapter_completes_job_with_result(tmp_path):
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="planner",
        adapter="noop",
        task="Create task proposal",
        context="Context packet",
    )

    result = store.run_job(job["id"])

    assert result["status"] == "completed"
    assert result["adapter"] == "noop"
    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / job["id"]
    assert "noop adapter" in (job_root / "result.md").read_text(encoding="utf-8")
    assert json.loads((job_root / "status.json").read_text(encoding="utf-8"))["status"] == "completed"


def test_human_adapter_marks_job_waiting_for_manual_result(tmp_path):
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="product_requirement",
        adapter="human",
        task="Revise PRD",
        context="Reviewer requested changes.",
    )

    result = store.run_job(job["id"])

    assert result["status"] == "needs_review"
    assert "Fill result.md" in result["summary"]


def test_codex_adapter_invokes_codex_cli_and_records_metadata(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, *, cwd, input, capture_output, text, timeout, check):
        calls.append(
            {
                "command": command,
                "cwd": cwd,
                "input": input,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        output_path = command[command.index("--output-last-message") + 1]
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write("# Worker Result\n\nCodex drafted the implementation notes.\n")
        return subprocess.CompletedProcess(command, 0, stdout="codex stdout", stderr="codex stderr")

    monkeypatch.setattr("goodteam_os.workers.subprocess.run", fake_run)
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="implementation",
        adapter="codex",
        task="Implement the CONTENTOPS MVP.",
        context="Approved artifacts are available.",
        expected_artifacts=["README.md"],
    )

    result = store.run_job(job["id"])

    assert result["status"] == "completed"
    assert result["adapter"] == "codex"
    assert result["model"] == "gpt-5.5"
    assert result["exit_code"] == 0
    assert calls[0]["cwd"] == tmp_path
    assert calls[0]["command"][:4] == ["/opt/homebrew/bin/codex", "exec", "--model", "gpt-5.5"]
    assert "--skip-git-repo-check" in calls[0]["command"]
    assert "--ignore-user-config" not in calls[0]["command"]
    assert "--ephemeral" not in calls[0]["command"]
    assert "--dangerously-bypass-approvals-and-sandbox" in calls[0]["command"]
    assert "Implement the CONTENTOPS MVP." in calls[0]["input"]
    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / job["id"]
    status = json.loads((job_root / "status.json").read_text(encoding="utf-8"))
    assert status["command"] == calls[0]["command"]
    assert status["preflight"]["codex_available"] is True
    assert status["preflight"]["model"] == "gpt-5.5"
    assert "OPENAI_API_KEY" in status["preflight"]["auth_env"]
    assert "value" not in json.dumps(status["preflight"]).lower()
    assert status["stdout_path"].endswith("/stdout.txt")
    assert status["stderr_path"].endswith("/stderr.txt")
    assert (job_root / "result.md").read_text(encoding="utf-8").startswith("# Worker Result")


def test_codex_adapter_summarizes_invalid_api_key_failure(tmp_path, monkeypatch):
    def fake_run(command, *, cwd, input, capture_output, text, timeout, check):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr='ERROR: unexpected status 401 Unauthorized: {"code":"INVALID_API_KEY","message":"Invalid API key"}',
        )

    monkeypatch.setattr("goodteam_os.workers.subprocess.run", fake_run)
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="implementation",
        adapter="codex",
        task="Implement the CONTENTOPS MVP.",
        context="Approved artifacts are available.",
    )

    result = store.run_job(job["id"])

    assert result["status"] == "failed"
    assert "invalid API key" in result["summary"]
    assert result["preflight"]["codex_available"] is True


def test_codex_adapter_does_not_report_invalid_api_key_when_exit_succeeds(tmp_path, monkeypatch):
    def fake_run(command, *, cwd, input, capture_output, text, timeout, check):
        output_path = command[command.index("--output-last-message") + 1]
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write("# Worker Result\n\nCompleted despite plugin auth warnings.\n")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="codex stdout",
            stderr="plugin warmup ignored INVALID_API_KEY while the worker itself completed",
        )

    monkeypatch.setattr("goodteam_os.workers.subprocess.run", fake_run)
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="qa",
        adapter="codex",
        task="Verify the CONTENTOPS MVP.",
        context="Approved artifacts are available.",
    )

    result = store.run_job(job["id"])

    assert result["status"] == "completed"
    assert result["summary"] == "Codex CLI exited with code 0."


def test_codex_adapter_summarizes_stream_disconnect_failure(tmp_path, monkeypatch):
    def fake_run(command, *, cwd, input, capture_output, text, timeout, check):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="ERROR: stream disconnected before completion: stream closed before response.completed",
        )

    monkeypatch.setattr("goodteam_os.workers.subprocess.run", fake_run)
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="implementation",
        adapter="codex",
        task="Implement the CONTENTOPS init project scaffold.",
        context="Approved artifacts are available.",
    )

    result = store.run_job(job["id"])

    assert result["status"] == "failed"
    assert "stream disconnected" in result["summary"]
    assert "response.completed" in result["summary"]


def test_codex_adapter_fails_fast_when_codex_cli_is_missing(tmp_path, monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("goodteam_os.workers.which", lambda command: None)
    monkeypatch.setattr("goodteam_os.workers.DEFAULT_CODEX_PATH", "/missing/codex")
    monkeypatch.setattr("goodteam_os.workers.subprocess.run", fake_run)
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="implementation",
        adapter="codex",
        task="Implement the CONTENTOPS MVP.",
        context="Approved artifacts are available.",
    )

    result = store.run_job(job["id"])

    assert result["status"] == "failed"
    assert "Codex CLI is not available" in result["summary"]
    assert result["preflight"]["codex_available"] is False
    assert calls == []
