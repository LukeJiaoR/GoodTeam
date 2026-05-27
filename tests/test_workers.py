import json
import subprocess
from datetime import UTC, datetime

from goodteam_os.workers import WorkerJobStore


class FakePopen:
    pid = 4242

    def __init__(
        self,
        command,
        *,
        stdout_text: str = "",
        stderr_text: str = "",
        returncode: int = 0,
        result_text: str = "# Worker Result\n\nCompleted.\n",
        calls: list[dict[str, object]] | None = None,
        snapshot=None,
        raise_timeout: bool = False,
    ):
        self.command = command
        self.stdout_text = stdout_text
        self.stderr_text = stderr_text
        self.returncode = returncode
        self.result_text = result_text
        self.calls = calls
        self.snapshot = snapshot
        self.raise_timeout = raise_timeout

    def communicate(self, input, timeout):
        if self.calls is not None:
            self.calls.append(
                {
                    "command": self.command,
                    "cwd": self.cwd,
                    "input": input,
                    "text": True,
                    "timeout": timeout,
                }
            )
        if self.snapshot is not None:
            self.snapshot()
        if self.raise_timeout:
            raise subprocess.TimeoutExpired(self.command, timeout, output=self.stdout_text, stderr=self.stderr_text)
        output_path = self.command[self.command.index("--output-last-message") + 1]
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(self.result_text)
        return (self.stdout_text, self.stderr_text)

    def kill(self):
        self.returncode = -9


def install_fake_popen(
    monkeypatch,
    *,
    stdout_text: str = "",
    stderr_text: str = "",
    returncode: int = 0,
    result_text: str = "# Worker Result\n\nCompleted.\n",
    calls: list[dict[str, object]] | None = None,
    snapshot=None,
    raise_timeout: bool = False,
):
    def fake_popen(command, *, cwd, stdin, stdout, stderr, text):
        process = FakePopen(
            command,
            stdout_text=stdout_text,
            stderr_text=stderr_text,
            returncode=returncode,
            result_text=result_text,
            calls=calls,
            snapshot=snapshot,
            raise_timeout=raise_timeout,
        )
        process.cwd = cwd
        return process

    monkeypatch.setattr("goodteam_os.workers.subprocess.Popen", fake_popen)


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
    install_fake_popen(
        monkeypatch,
        stdout_text="codex stdout",
        stderr_text="codex stderr",
        result_text="# Worker Result\n\nCodex drafted the implementation notes.\n",
        calls=calls,
    )
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
    assert result["model"] == "gpt-5.3-codex"
    assert result["exit_code"] == 0
    assert calls[0]["cwd"] == tmp_path
    assert calls[0]["command"][:4] == ["/opt/homebrew/bin/codex", "exec", "--model", "gpt-5.3-codex"]
    assert "--skip-git-repo-check" in calls[0]["command"]
    assert "--ignore-user-config" not in calls[0]["command"]
    assert "--ephemeral" not in calls[0]["command"]
    assert "--dangerously-bypass-approvals-and-sandbox" in calls[0]["command"]
    assert "Implement the CONTENTOPS MVP." in calls[0]["input"]
    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / job["id"]
    status = json.loads((job_root / "status.json").read_text(encoding="utf-8"))
    assert status["command"] == calls[0]["command"]
    assert status["preflight"]["codex_available"] is True
    assert status["preflight"]["model"] == "gpt-5.3-codex"
    assert "OPENAI_API_KEY" in status["preflight"]["auth_env"]
    assert "value" not in json.dumps(status["preflight"]).lower()
    assert calls[0]["text"] is True
    assert calls[0]["timeout"] == 1800
    assert status["stdout_path"].endswith("/stdout.txt")
    assert status["stderr_path"].endswith("/stderr.txt")
    assert (job_root / "result.md").read_text(encoding="utf-8").startswith("# Worker Result")


def test_codex_adapter_marks_job_running_before_cli_exits(tmp_path, monkeypatch):
    snapshots = []
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="implementation",
        adapter="codex",
        task="Implement the CONTENTOPS MVP.",
        context="Approved artifacts are available.",
    )
    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / job["id"]
    install_fake_popen(
        monkeypatch,
        stdout_text="streamed stdout",
        stderr_text="streamed stderr",
        result_text="# Worker Result\n\nCodex finished.\n",
        snapshot=lambda: snapshots.append(json.loads((job_root / "status.json").read_text(encoding="utf-8"))),
    )

    result = store.run_job(job["id"])

    running = snapshots[0]
    assert running["status"] == "running"
    assert running["pid"] == 4242
    assert running["heartbeat_at"] == running["updated_at"]
    assert running["stdout_path"].endswith("/stdout.txt")
    assert running["stderr_path"].endswith("/stderr.txt")
    assert (job_root / "stdout.txt").exists()
    assert (job_root / "stderr.txt").exists()
    assert result["status"] == "completed"
    assert (job_root / "stdout.txt").read_text(encoding="utf-8") == "streamed stdout"
    assert (job_root / "stderr.txt").read_text(encoding="utf-8") == "streamed stderr"


def test_worker_job_store_list_jobs_merges_live_status(tmp_path):
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="qa",
        adapter="codex",
        task="Verify",
        context="Context",
    )
    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / job["id"]
    heartbeat = datetime.now(UTC).isoformat()
    (job_root / "status.json").write_text(
        json.dumps(
            {
                "job_id": job["id"],
                "run_id": "run-1",
                "role": "qa",
                "adapter": "codex",
                "status": "running",
                "summary": "Codex CLI is running.",
                "updated_at": heartbeat,
                "heartbeat_at": heartbeat,
                "pid": 4242,
                "stdout_path": str(job_root / "stdout.txt"),
                "stderr_path": str(job_root / "stderr.txt"),
            }
        ),
        encoding="utf-8",
    )

    [listed] = store.list_jobs()

    assert listed["status"] == "running"
    assert listed["pid"] == 4242
    assert listed["heartbeat_at"] == heartbeat
    assert listed["summary"] == "Codex CLI is running."


def test_worker_job_store_flags_stale_running_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.WORKER_STALE_AFTER_SECONDS", 1)
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="qa",
        adapter="codex",
        task="Verify",
        context="Context",
    )
    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / job["id"]
    old_heartbeat = "2026-05-27T00:00:00+00:00"
    (job_root / "status.json").write_text(
        json.dumps(
            {
                "job_id": job["id"],
                "run_id": "run-1",
                "role": "qa",
                "adapter": "codex",
                "status": "running",
                "summary": "Codex CLI is running.",
                "updated_at": old_heartbeat,
                "heartbeat_at": old_heartbeat,
            }
        ),
        encoding="utf-8",
    )

    [listed] = store.list_jobs()

    assert listed["status"] == "possibly_stuck"
    assert listed["raw_status"] == "running"
    assert listed["heartbeat_at"] == old_heartbeat


def test_codex_adapter_uses_job_model_when_present(tmp_path, monkeypatch):
    calls = []
    install_fake_popen(
        monkeypatch,
        result_text="# Worker Result\n\nCodex drafted the implementation notes.\n",
        calls=calls,
    )
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="implementation",
        adapter="codex",
        model="gpt-5.3-codex",
        task="Implement the CONTENTOPS MVP.",
        context="Approved artifacts are available.",
    )

    result = store.run_job(job["id"])

    assert result["model"] == "gpt-5.3-codex"
    assert calls[0]["command"][:4] == ["/opt/homebrew/bin/codex", "exec", "--model", "gpt-5.3-codex"]
    assert "Use model gpt-5.3-codex" in calls[0]["input"]


def test_codex_adapter_summarizes_invalid_api_key_failure(tmp_path, monkeypatch):
    install_fake_popen(
        monkeypatch,
        returncode=1,
        stderr_text='ERROR: unexpected status 401 Unauthorized: {"code":"INVALID_API_KEY","message":"Invalid API key"}',
        result_text="",
    )
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


def test_codex_adapter_prefers_terminal_provider_error_over_plugin_auth_warning(tmp_path, monkeypatch):
    install_fake_popen(
        monkeypatch,
        returncode=1,
        stderr_text=(
            "WARN startup remote plugin sync failed: chatgpt authentication required\n"
            'WARN featured plugin cache failed with status 401 Unauthorized: {"detail":"Unauthorized"}\n'
            "ERROR: Reconnecting... 5/5\n"
            "ERROR: unexpected status 503 Service Unavailable: Service temporarily unavailable, "
            "url: https://example.invalid/responses\n"
        ),
        result_text="",
    )
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="qa",
        adapter="codex",
        task="Verify the CONTENTOPS MVP.",
        context="Approved artifacts are available.",
    )

    result = store.run_job(job["id"])

    assert result["status"] == "failed"
    assert result["summary"] == "Codex CLI exited with code 1: 503 Service Unavailable."
    assert "401 Unauthorized" not in result["summary"]


def test_codex_adapter_writes_fallback_result_when_final_message_is_empty(tmp_path, monkeypatch):
    install_fake_popen(
        monkeypatch,
        returncode=1,
        stderr_text="ERROR: unexpected status 503 Service Unavailable: Service temporarily unavailable",
        result_text="",
    )
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="qa",
        adapter="codex",
        task="Verify the CONTENTOPS MVP.",
        context="Approved artifacts are available.",
    )

    result = store.run_job(job["id"])

    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / job["id"]
    result_text = (job_root / "result.md").read_text(encoding="utf-8")
    assert result["status"] == "failed"
    assert "Status: failed" in result_text
    assert "503 Service Unavailable" in result_text


def test_codex_adapter_does_not_report_invalid_api_key_when_exit_succeeds(tmp_path, monkeypatch):
    install_fake_popen(
        monkeypatch,
        stdout_text="codex stdout",
        stderr_text="plugin warmup ignored INVALID_API_KEY while the worker itself completed",
        result_text="# Worker Result\n\nCompleted despite plugin auth warnings.\n",
    )
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
    install_fake_popen(
        monkeypatch,
        returncode=1,
        stderr_text="ERROR: stream disconnected before completion: stream closed before response.completed",
        result_text="",
    )
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


def test_codex_adapter_records_timeout_as_failed_status(tmp_path, monkeypatch):
    install_fake_popen(
        monkeypatch,
        stdout_text="partial stdout",
        stderr_text="partial stderr",
        raise_timeout=True,
    )
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
    assert result["exit_code"] is None
    assert "timed out after 1800 seconds" in result["summary"]
    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / job["id"]
    assert (job_root / "stdout.txt").read_text(encoding="utf-8") == "partial stdout"
    assert (job_root / "stderr.txt").read_text(encoding="utf-8") == "partial stderr"
    assert "timed out" in (job_root / "result.md").read_text(encoding="utf-8")
    status = json.loads((job_root / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["timeout_seconds"] == 1800


def test_codex_worker_prompt_requires_bounded_smoke_cleanup(tmp_path, monkeypatch):
    calls = []
    install_fake_popen(monkeypatch, calls=calls)
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="implementation",
        adapter="codex",
        task="Implement the CONTENTOPS init project scaffold.",
        context="Approved artifacts are available.",
    )

    store.run_job(job["id"])

    assert "Use bounded smoke checks" in calls[0]["input"]
    assert "stop any dev servers, browser sessions, or watchers before exiting" in calls[0]["input"]


def test_codex_adapter_fails_fast_when_codex_cli_is_missing(tmp_path, monkeypatch):
    calls = []

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return FakePopen(args[0])

    monkeypatch.setattr("goodteam_os.workers.which", lambda command: None)
    monkeypatch.setattr("goodteam_os.workers.DEFAULT_CODEX_PATH", "/missing/codex")
    monkeypatch.setattr("goodteam_os.workers.subprocess.Popen", fake_popen)
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
