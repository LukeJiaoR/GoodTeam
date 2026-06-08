import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from goodteam_os.memory import RoleMemoryStore
from goodteam_os.workers import (
    WORKER_MAX_ATTEMPTS,
    WorkerJobStore,
    _claude_summary,
    _codex_summary,
    _query_agy_models,
    _query_codex_models,
    classify_worker_failure,
    discover_installed_agents,
    is_transient,
    parse_result_sections,
    worker_backend_doctor,
)
from goodteam_os.dispatch import (
    _job_report,
    dispatch_tracks,
    ensure_dispatch_project_root_is_safe,
    parse_dispatch_track_spec,
    run_track_verify,
)


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

    def communicate(self, input=None, timeout=None):
        if self.calls is not None:
            self.calls.append(
                {
                    "command": self.command,
                    "cwd": self.cwd,
                    "env": self.env,
                    "input": input,
                    "text": True,
                    "timeout": timeout,
                }
            )
        if self.snapshot is not None:
            self.snapshot()
        if self.raise_timeout:
            raise subprocess.TimeoutExpired(self.command, timeout, output=self.stdout_text, stderr=self.stderr_text)
        if "--output-last-message" in self.command:
            output_path = self.command[self.command.index("--output-last-message") + 1]
            with open(output_path, "w", encoding="utf-8") as handle:
                handle.write(self.result_text)
        return (self.stdout_text, self.stderr_text)

    def kill(self):
        self.returncode = -9


class FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


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
    def fake_popen(command, *, cwd, env, stdin, stdout, stderr, text):
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
        process.env = env
        return process

    monkeypatch.setattr("goodteam_os.workers.which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setattr("goodteam_os.workers.subprocess.Popen", fake_popen)


def install_fake_popen_sequence(monkeypatch, responses, *, calls=None):
    state = {"index": 0}

    def fake_popen(command, *, cwd, env, stdin, stdout, stderr, text):
        if state["index"] >= len(responses):
            response = responses[-1]
        else:
            response = responses[state["index"]]
        state["index"] += 1
        process = FakePopen(
            command,
            stdout_text=response.get("stdout_text", ""),
            stderr_text=response.get("stderr_text", ""),
            returncode=response.get("returncode", 0),
            result_text=response.get("result_text", "# Worker Result\n\nCompleted.\n"),
            calls=calls,
            snapshot=response.get("snapshot"),
            raise_timeout=response.get("raise_timeout", False),
        )
        process.cwd = cwd
        process.env = env
        return process

    monkeypatch.setattr("goodteam_os.workers.which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setattr("goodteam_os.workers.subprocess.Popen", fake_popen)


def install_fake_run(
    monkeypatch,
    *,
    stdout_text: str = "",
    stderr_text: str = "",
    returncode: int = 0,
    calls: list[dict[str, object]] | None = None,
):
    def fake_run(command, *, cwd, capture_output, text, check):
        if calls is not None:
            calls.append(
                {
                    "command": command,
                    "cwd": cwd,
                    "capture_output": capture_output,
                    "text": text,
                    "check": check,
                }
            )

        class Completed:
            def __init__(self):
                self.returncode = returncode
                self.stdout = stdout_text
                self.stderr = stderr_text

        return Completed()

    monkeypatch.setattr("goodteam_os.dispatch.subprocess.run", fake_run)


def memory_events(project_root, role):
    event_path = RoleMemoryStore(project_root).event_path(role)
    if not event_path.is_file():
        return []
    return [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_parse_result_sections_maps_standard_sections_to_horizons():
    text = """
Intro prose should not become memory.

## Decisions
- Memory write-back uses the append-only WAL.

## Learned
- Pickup packets are created before workers run.

## Caveats
- Worker result markdown is freeform.

## Open Questions
- Should same-run propagation be a future milestone?
"""

    assert parse_result_sections(text) == [
        ("long-term", "Memory write-back uses the append-only WAL."),
        ("mid-term", "Pickup packets are created before workers run."),
        ("mid-term", "Worker result markdown is freeform."),
        ("short-term", "Should same-run propagation be a future milestone?"),
    ]


def test_parse_result_sections_tolerates_header_case_colons_and_bullet_markers():
    text = """
### learned:
* Normalize section names before matching.

### CaVeAtS
• Avoid treating prompt guidance as memory.

### OPEN   QUESTIONS:
- Who reviews memory promotion later?
"""

    assert parse_result_sections(text) == [
        ("mid-term", "Normalize section names before matching."),
        ("mid-term", "Avoid treating prompt guidance as memory."),
        ("short-term", "Who reviews memory promotion later?"),
    ]


def test_parse_result_sections_uses_first_prose_paragraph_and_stops_at_heading():
    text = """
Opening summary should be ignored.

## Learned
The parser can capture a paragraph
that wraps across lines.

This second paragraph should be ignored.

## Caveats
This caveat paragraph is captured.

# Unrelated Heading
This prose must not be captured.
"""

    assert parse_result_sections(text) == [
        ("mid-term", "The parser can capture a paragraph that wraps across lines."),
        ("mid-term", "This caveat paragraph is captured."),
    ]


def test_parse_result_sections_skips_empty_sections_and_caps_each_section():
    learned_bullets = "\n".join(f"- Learned fact {index}" for index in range(12))
    text = f"""
## Caveats

## Learned
{learned_bullets}

## Decisions
- Durable decision
"""

    parsed = parse_result_sections(text)

    learned = [item for item in parsed if item[0] == "mid-term" and item[1].startswith("Learned fact")]
    assert len(learned) == 10
    assert learned[-1] == ("mid-term", "Learned fact 9")
    assert ("long-term", "Durable decision") in parsed
    assert all(text for _, text in parsed)


def test_parse_dispatch_track_spec_loads_file_content(tmp_path):
    task_file = tmp_path / "backend.md"
    task_file.write_text("Implement backend dispatch.\n", encoding="utf-8")

    track = parse_dispatch_track_spec(f"backend:codex:{task_file}", base_dir=tmp_path)

    assert track == {
        "role": "backend",
        "adapter": "codex",
        "task": "Implement backend dispatch.\n",
        "task_source": str(task_file),
    }


def test_parse_dispatch_track_spec_accepts_inline_task_with_colons(tmp_path):
    track = parse_dispatch_track_spec("frontend:gemini:Implement UI: preserve state", base_dir=tmp_path)

    assert track["role"] == "frontend"
    assert track["adapter"] == "gemini"
    assert track["task"] == "Implement UI: preserve state"
    assert track["task_source"] == "inline"


def test_run_track_verify_records_failure_and_output_tail(tmp_path, monkeypatch):
    calls = []
    install_fake_run(
        monkeypatch,
        stdout_text="\n".join(f"stdout {index}" for index in range(30)),
        stderr_text="verification failed",
        returncode=2,
        calls=calls,
    )

    result = run_track_verify("pytest -q", cwd=tmp_path)

    assert result["command"] == "pytest -q"
    assert result["cwd"] == str(tmp_path)
    assert result["exit_code"] == 2
    assert result["passed"] is False
    assert "stdout 29" in result["output_tail"]
    assert "stdout 0" not in result["output_tail"]
    assert result["stderr"] == "verification failed"
    assert calls[0]["cwd"] == tmp_path


def test_isolation_guard_rejects_project_root_inside_configured_repo(tmp_path):
    repo = tmp_path / "real-repo"
    nested = repo / "work"
    nested.mkdir(parents=True)

    result = ensure_dispatch_project_root_is_safe(
        nested,
        configured_repo_paths=[repo],
    )

    assert result["safe"] is False
    assert str(repo.resolve()) in result["reason"]


def test_job_report_surfaces_failure_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr("goodteam_os.dispatch.changed_files", lambda _: [])
    job = {"id": "job-1", "role": "implementation", "adapter": "codex"}
    status = {
        "status": "failed",
        "summary": "worker failed",
        "failure_class": "timeout",
        "attempts": 2,
        "disposition": "escalated",
    }
    verify = {"passed": False, "skipped": True, "reason": "worker_failed"}

    report = _job_report(project_root=tmp_path, job=job, status=status, verify=verify)

    assert report["failure_class"] == "timeout"
    assert report["attempts"] == 2
    assert report["disposition"] == "escalated"


def test_isolation_guard_accepts_temp_worktree(tmp_path):
    target = tmp_path / "dev-workspace" / "target"
    target.mkdir(parents=True)

    result = ensure_dispatch_project_root_is_safe(
        target,
        configured_repo_paths=[tmp_path / "other-real-repo"],
    )

    assert result == {"safe": True, "reason": ""}


def test_dispatch_tracks_groups_ok_and_verify_failure(tmp_path):
    project_root = tmp_path / "target"
    project_root.mkdir()

    payload = dispatch_tracks(
        project_root=project_root,
        tracks=[
            {"role": "implementation", "adapter": "fake", "task": "Implement backend."},
            {"role": "qa", "adapter": "fake", "task": "Verify frontend."},
        ],
        verify_commands={
            "implementation": "echo ok",
            "qa": "exit 1",
        },
    )

    assert payload["all_ok"] is False
    assert [job["role"] for job in payload["jobs"]] == ["implementation", "qa"]
    assert [job["role"] for job in payload["ok"]] == ["implementation"]
    assert [job["role"] for job in payload["needs_human"]] == ["qa"]
    assert payload["jobs"][0]["outcome"] == "ok"
    assert payload["jobs"][0]["memory_writeback"]["status"] == "skipped"
    assert payload["jobs"][0]["verify"]["passed"] is True
    assert payload["jobs"][1]["outcome"] == "needs_human"
    assert payload["jobs"][1]["verify"]["passed"] is False
    assert (project_root / ".goodteam" / "workers" / "jobs" / payload["jobs"][0]["job_id"] / "result.md").is_file()


def test_dispatch_tracks_persists_task_and_constraints_sources(tmp_path):
    task_file = tmp_path / "backend-task.md"
    task_file.write_text("Implement backend from contract.\n", encoding="utf-8")
    constraints_file = tmp_path / "constraints.md"
    constraints_file.write_text("- Stay in backend.\n", encoding="utf-8")
    project_root = tmp_path / "target"
    project_root.mkdir()
    track = parse_dispatch_track_spec(f"implementation:fake:{task_file}", base_dir=tmp_path)

    payload = dispatch_tracks(
        project_root=project_root,
        tracks=[track],
        constraints_file=constraints_file,
    )

    job_id = payload["jobs"][0]["job_id"]
    job_json = json.loads((project_root / ".goodteam" / "workers" / "jobs" / job_id / "job.json").read_text(encoding="utf-8"))
    assert job_json["task_source"] == str(task_file)
    assert job_json["constraints_source"] == str(constraints_file)


def test_dispatch_tracks_archives_research_report_source(tmp_path):
    task_file = tmp_path / "backend-task.md"
    task_file.write_text("Implement backend from researched contract.\n", encoding="utf-8")
    research_report = tmp_path / "research-report.md"
    research_report.write_text("# Research\n\n- Existing helper already writes manifests.\n", encoding="utf-8")
    project_root = tmp_path / "target"
    project_root.mkdir()
    track = parse_dispatch_track_spec(f"implementation:fake:{task_file}", base_dir=tmp_path)

    payload = dispatch_tracks(
        project_root=project_root,
        tracks=[track],
        research_report_file=research_report,
    )

    job_id = payload["jobs"][0]["job_id"]
    job_root = project_root / ".goodteam" / "workers" / "jobs" / job_id
    job_json = json.loads((job_root / "job.json").read_text(encoding="utf-8"))
    assert job_json["research_report_source"] == str(research_report)
    assert (job_root / "research-report.md").read_text(encoding="utf-8") == research_report.read_text(encoding="utf-8")


def test_dispatch_tracks_captures_one_track_exception_without_blocking_other(tmp_path, monkeypatch):
    original_run_job = WorkerJobStore.run_job

    def flaky_run_job(self, job_id):
        job = json.loads((self.root / job_id / "job.json").read_text(encoding="utf-8"))
        if job["role"] == "qa":
            raise RuntimeError("qa exploded")
        return original_run_job(self, job_id)

    monkeypatch.setattr(WorkerJobStore, "run_job", flaky_run_job)
    project_root = tmp_path / "target"
    project_root.mkdir()

    payload = dispatch_tracks(
        project_root=project_root,
        tracks=[
            {"role": "implementation", "adapter": "fake", "task": "Implement backend."},
            {"role": "qa", "adapter": "fake", "task": "Verify frontend."},
        ],
    )

    assert payload["all_ok"] is False
    assert [job["role"] for job in payload["ok"]] == ["implementation"]
    assert [job["role"] for job in payload["needs_human"]] == ["qa"]
    assert payload["jobs"][0]["status"] == "completed"
    assert payload["jobs"][1]["status"] == "failed"
    assert payload["jobs"][1]["outcome"] == "needs_human"
    assert "qa exploded" in payload["jobs"][1]["summary"]


def test_dispatch_tracks_reports_changed_files_from_git_diff(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, *, cwd=None, capture_output=True, text=True, check=False):
        calls.append({"command": command, "cwd": cwd})
        if command[:3] == ["git", "-C", str(tmp_path / "target")]:
            return FakeCompleted(returncode=0, stdout="src/app.py\ntests/test_app.py\n", stderr="")
        return FakeCompleted(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("goodteam_os.dispatch.subprocess.run", fake_run)
    project_root = tmp_path / "target"
    project_root.mkdir()

    payload = dispatch_tracks(
        project_root=project_root,
        tracks=[
            {"role": "implementation", "adapter": "fake", "task": "Implement backend."},
        ],
        verify_commands={"implementation": "echo ok"},
    )

    assert payload["jobs"][0]["changed_files"] == ["src/app.py", "tests/test_app.py"]
    assert any(call["command"][:3] == ["git", "-C", str(project_root)] for call in calls)


def test_dispatch_tracks_reports_file_collisions_when_tracks_touch_same_file(tmp_path, monkeypatch):
    def fake_run_dispatched_job(project_root, job, verify_commands):
        if job["role"] == "ui":
            changed = ["src/App.jsx", "src/theme.css"]
        else:
            changed = ["src/App.jsx", "src/api.py"]
        return {
            "job_id": job["id"],
            "role": job["role"],
            "adapter": job["adapter"],
            "status": "completed",
            "outcome": "ok",
            "summary": "done",
            "memory_writeback": {"status": "skipped"},
            "verify": {"passed": True, "skipped": True, "reason": "none"},
            "changed_files": changed,
        }

    monkeypatch.setattr("goodteam_os.dispatch._run_dispatched_job", fake_run_dispatched_job)
    project_root = tmp_path / "target"
    project_root.mkdir()

    payload = dispatch_tracks(
        project_root=project_root,
        tracks=[
            {"role": "ui", "adapter": "fake", "task": "UI edits."},
            {"role": "implementation", "adapter": "fake", "task": "Backend edits."},
        ],
    )

    assert payload["has_collisions"] is True
    assert payload["all_ok"] is False
    assert payload["file_collisions"] == [{"path": "src/App.jsx", "roles": ["implementation", "ui"]}]


def test_dispatch_tracks_reports_no_file_collisions_for_disjoint_changes(tmp_path, monkeypatch):
    def fake_run_dispatched_job(project_root, job, verify_commands):
        if job["role"] == "ui":
            changed = ["src/App.jsx"]
        else:
            changed = ["src/api.py"]
        return {
            "job_id": job["id"],
            "role": job["role"],
            "adapter": job["adapter"],
            "status": "completed",
            "outcome": "ok",
            "summary": "done",
            "memory_writeback": {"status": "skipped"},
            "verify": {"passed": True, "skipped": True, "reason": "none"},
            "changed_files": changed,
        }

    monkeypatch.setattr("goodteam_os.dispatch._run_dispatched_job", fake_run_dispatched_job)
    project_root = tmp_path / "target"
    project_root.mkdir()

    payload = dispatch_tracks(
        project_root=project_root,
        tracks=[
            {"role": "ui", "adapter": "fake", "task": "UI edits."},
            {"role": "implementation", "adapter": "fake", "task": "Backend edits."},
        ],
    )

    assert payload["has_collisions"] is False
    assert payload["file_collisions"] == []


def test_dispatch_tracks_passes_projects_dir_to_safety_check(tmp_path, monkeypatch):
    seen = {}

    def fake_safety_check(project_root, *, projects_dir, allow_unsafe):
        seen["project_root"] = project_root
        seen["projects_dir"] = projects_dir
        seen["allow_unsafe"] = allow_unsafe
        return {"safe": True, "reason": ""}

    def fake_run_dispatched_job(project_root, job, verify_commands):
        return {
            "job_id": job["id"],
            "role": job["role"],
            "adapter": job["adapter"],
            "status": "completed",
            "outcome": "ok",
            "summary": "done",
            "memory_writeback": {"status": "skipped"},
            "verify": {"passed": True, "skipped": True, "reason": "none"},
            "changed_files": [],
        }

    monkeypatch.setattr("goodteam_os.dispatch.ensure_dispatch_project_root_is_safe", fake_safety_check)
    monkeypatch.setattr("goodteam_os.dispatch._run_dispatched_job", fake_run_dispatched_job)
    project_root = tmp_path / "target"
    project_root.mkdir()
    projects_dir = tmp_path / "served-workspace" / "projects"

    dispatch_tracks(
        project_root=project_root,
        projects_dir=projects_dir,
        tracks=[
            {"role": "implementation", "adapter": "fake", "task": "Backend edits."},
        ],
    )

    assert seen == {
        "project_root": project_root,
        "projects_dir": projects_dir,
        "allow_unsafe": False,
    }


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


def test_worker_prompt_leads_with_retrieved_relevant_events(tmp_path):
    memory_store = RoleMemoryStore(tmp_path)
    memory_store.ensure_role_memory("planner", "Planner", "AI Content Studio")
    memory_store.append_memory_event(
        "planner",
        horizon="mid-term",
        text="Use BM25/embedding retrieval as the worker pickup decision signal.",
        source="job-1/result.md",
    )
    memory_store.append_memory_event(
        "planner",
        horizon="mid-term",
        text="Display summaries are informational and should not drive task decisions.",
        source="job-2/result.md",
    )
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="planner",
        adapter="human",
        task="decide worker approach from retrieval signal",
        context="Approved RFC.",
    )
    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / job["id"]
    prompt = store._worker_prompt(job_root, job)

    assert "## Worker Pickup Signal" in prompt
    assert "PRIMARY decision context" in prompt
    assert "Retrieved Relevant Events (Primary Signal)" in prompt
    assert "Use BM25/embedding retrieval as the worker pickup decision signal." in prompt
    assert "Horizon Summary (Display-Only)" in prompt


def test_run_job_records_memory_citations_from_pickup_packet_relevant_events(tmp_path):
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="planner",
        adapter="noop",
        task="Create plan",
        context="Approved RFC.",
    )
    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / job["id"]
    pickup_path = job_root / "role_pickup_packet.json"
    pickup_packet = json.loads(pickup_path.read_text(encoding="utf-8"))
    pickup_packet["relevant_events"] = [
        {"id": "mem:abc123", "text": "Fact A"},
        {"id": "mem:def456", "text": "Fact B"},
    ]
    pickup_path.write_text(json.dumps(pickup_packet, indent=2), encoding="utf-8")

    result = store.run_job(job["id"])

    assert result["memory_citations"] == ["mem:abc123", "mem:def456"]
    status = json.loads((job_root / "status.json").read_text(encoding="utf-8"))
    assert status["memory_citations"] == ["mem:abc123", "mem:def456"]


def test_run_job_records_empty_memory_citations_when_pickup_packet_has_no_relevant_events(tmp_path):
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="planner",
        adapter="noop",
        task="",
        context="Approved RFC.",
    )

    result = store.run_job(job["id"])

    assert result["memory_citations"] == []
    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / job["id"]
    status = json.loads((job_root / "status.json").read_text(encoding="utf-8"))
    assert status["memory_citations"] == []


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


def test_fake_adapter_completes_without_memory_sections_as_noop(tmp_path):
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="planner",
        adapter="fake",
        task="Create task proposal",
        context="Context packet",
    )

    result = store.run_job(job["id"])

    assert result["status"] == "completed"
    assert result["memory_writeback"]["status"] == "skipped"
    assert result["memory_writeback"]["reason"] == "no_sections"
    assert result["memory_provenance"]["status"] == "ok"
    assert len(memory_events(tmp_path, "planner")) == 1


def test_noop_adapter_completes_without_memory_sections_as_noop(tmp_path):
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
    assert result["memory_writeback"]["status"] == "skipped"
    assert result["memory_writeback"]["reason"] == "no_sections"
    assert result["memory_provenance"]["status"] == "ok"
    assert len(memory_events(tmp_path, "planner")) == 1


def test_completed_job_writes_short_term_provenance_event(tmp_path):
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="planner",
        adapter="noop",
        task="Create task proposal with context from approved plan.",
        context="Context packet",
    )

    result = store.run_job(job["id"])

    assert result["status"] == "completed"
    assert result["memory_provenance"]["status"] == "ok"
    events = memory_events(tmp_path, "planner")
    provenance_events = [event for event in events if event["source"] == f"{job['id']}/provenance"]
    assert len(provenance_events) == 1
    assert provenance_events[0]["horizon"] == "short-term"
    assert job["id"] in provenance_events[0]["text"]
    assert "for task: Create task proposal with context from approved plan." in provenance_events[0]["text"]


def test_completed_job_provenance_records_task_and_constraints_sources(tmp_path):
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="planner",
        adapter="noop",
        task="Create task proposal with context from approved plan.",
        context="Context packet",
        task_source="/tmp/dispatch-task.md",
        constraints_source="/tmp/dispatch-constraints.md",
    )

    result = store.run_job(job["id"])

    assert result["memory_provenance"]["status"] == "ok"
    provenance_event = [
        event for event in memory_events(tmp_path, "planner") if event["source"] == f"{job['id']}/provenance"
    ][0]
    assert "task_source=/tmp/dispatch-task.md" in provenance_event["text"]
    assert "constraints_source=/tmp/dispatch-constraints.md" in provenance_event["text"]


def test_rerunning_completed_job_dedups_short_term_provenance_event(tmp_path):
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="planner",
        adapter="noop",
        task="Create task proposal with context from approved plan.",
        context="Context packet",
    )

    first = store.run_job(job["id"])
    second = store.run_job(job["id"])

    assert first["status"] == "completed"
    assert second["status"] == "completed"
    assert first["memory_provenance"]["status"] == "ok"
    assert second["memory_provenance"]["status"] == "skipped"
    assert second["memory_provenance"]["skipped_duplicates"] == 1
    assert second["memory_provenance"]["appended"] == 0

    events = memory_events(tmp_path, "planner")
    provenance_events = [event for event in events if event["source"].endswith("/provenance")]
    assert [event["source"] for event in provenance_events] == [f"{job['id']}/provenance"]


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
    assert "memory_writeback" not in result
    assert memory_events(tmp_path, "product_requirement") == []


def test_completed_cli_job_writes_memory_events_to_expected_horizons(tmp_path, monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.which", lambda name: f"/fake/bin/{name}")
    result_text = """
# Worker Result

Implemented memory write-back.

## Decisions
- Write-back uses the role memory WAL.

## Learned
- Worker pickup packets are created at job creation.

## Caveats
- Result markdown may include freeform prose.

## Open Questions
- Should later milestones promote repeated mid-term facts?
"""
    install_fake_popen(monkeypatch, result_text=result_text)
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="implementation",
        adapter="codex",
        task="Implement memory write-back.",
        context="Approved plan.",
    )

    result = store.run_job(job["id"])

    assert result["status"] == "completed"
    assert result["memory_writeback"] == {
        "status": "ok",
        "source": f"{job['id']}/result.md",
        "parsed": 4,
        "appended": 4,
        "skipped_duplicates": 0,
    }
    events = memory_events(tmp_path, "implementation")
    result_events = [event for event in events if event["source"] == f"{job['id']}/result.md"]
    assert [(event["horizon"], event["text"]) for event in result_events] == [
        ("long-term", "Write-back uses the role memory WAL."),
        ("mid-term", "Worker pickup packets are created at job creation."),
        ("mid-term", "Result markdown may include freeform prose."),
        ("short-term", "Should later milestones promote repeated mid-term facts?"),
    ]
    assert {event["source"] for event in result_events} == {f"{job['id']}/result.md"}
    status = json.loads(
        (tmp_path / ".goodteam" / "workers" / "jobs" / job["id"] / "status.json").read_text(encoding="utf-8")
    )
    assert status["memory_writeback"]["appended"] == 4
    assert (tmp_path / ".goodteam" / "memory" / "roles" / "implementation" / "events.jsonl").is_file()


def test_rerunning_same_job_does_not_append_duplicate_memory_events(tmp_path, monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.which", lambda name: f"/fake/bin/{name}")
    result_text = """
# Worker Result

## Learned
- Re-running the same job must not duplicate memory.
"""
    install_fake_popen(monkeypatch, result_text=result_text)
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="implementation",
        adapter="codex",
        task="Implement memory write-back.",
        context="Approved plan.",
    )

    first = store.run_job(job["id"])
    second = store.run_job(job["id"])

    assert first["memory_writeback"]["appended"] == 1
    assert second["memory_writeback"]["appended"] == 0
    assert second["memory_writeback"]["skipped_duplicates"] == 1
    assert first["memory_provenance"]["appended"] == 1
    assert second["memory_provenance"]["appended"] == 0
    assert second["memory_provenance"]["skipped_duplicates"] == 1
    assert len(memory_events(tmp_path, "implementation")) == 2


def test_write_back_skips_duplicate_bullets_and_existing_texts(tmp_path, monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.which", lambda name: f"/fake/bin/{name}")
    memory_store = RoleMemoryStore(tmp_path)
    memory_store.append_memory_event(
        "qa",
        horizon="mid-term",
        text="Existing caveat from a previous job.",
        source="job-old/result.md",
    )
    result_text = """
# Worker Result

## Caveats
- Existing caveat from a previous job.
- Duplicate inside this result.
- Duplicate inside this result.
"""
    install_fake_popen(monkeypatch, result_text=result_text)
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="qa",
        adapter="codex",
        task="Verify memory write-back.",
        context="Approved plan.",
    )

    result = store.run_job(job["id"])

    assert result["memory_writeback"]["parsed"] == 3
    assert result["memory_writeback"]["appended"] == 1
    assert result["memory_writeback"]["skipped_duplicates"] == 2
    result_events = [event for event in memory_events(tmp_path, "qa") if event["source"].endswith("/result.md")]
    assert [event["text"] for event in result_events] == [
        "Existing caveat from a previous job.",
        "Duplicate inside this result.",
    ]


def test_write_back_exception_is_recorded_without_failing_completed_job(tmp_path, monkeypatch):
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="planner",
        adapter="fake",
        task="Create task proposal",
        context="Context packet",
    )

    def raise_write_back(job_root, job_payload):
        raise RuntimeError("write-back unavailable")

    monkeypatch.setattr(store, "_write_back_memory", raise_write_back)

    result = store.run_job(job["id"])

    assert result["status"] == "completed"
    assert result["memory_writeback"] == {"status": "error", "error": "write-back unavailable"}
    status = json.loads((tmp_path / ".goodteam" / "workers" / "jobs" / job["id"] / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "completed"
    assert status["memory_writeback"] == {"status": "error", "error": "write-back unavailable"}


def test_provenance_write_back_exception_is_recorded_without_failing_completed_job(tmp_path, monkeypatch):
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="planner",
        adapter="fake",
        task="Create task proposal",
        context="Context packet",
    )

    def raise_provenance(job_root, job_payload):
        raise RuntimeError("provenance unavailable")

    monkeypatch.setattr(store, "_write_back_provenance", raise_provenance)

    result = store.run_job(job["id"])

    assert result["status"] == "completed"
    assert result["memory_provenance"] == {"status": "error", "error": "provenance unavailable"}
    status = json.loads((tmp_path / ".goodteam" / "workers" / "jobs" / job["id"] / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "completed"
    assert status["memory_provenance"] == {"status": "error", "error": "provenance unavailable"}


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
    assert calls[0]["command"][:4] == ["/fake/bin/codex", "exec", "--model", "gpt-5.3-codex"]
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


def test_codex_adapter_uses_isolated_per_job_codex_home_with_allowlisted_files(tmp_path, monkeypatch):
    calls = []
    source_codex_home = tmp_path / "real-codex-home"
    source_codex_home.mkdir()
    (source_codex_home / "auth.json").write_text('{"ok":true}', encoding="utf-8")
    (source_codex_home / "AGENTS.md").write_text("# Global protocol", encoding="utf-8")
    (source_codex_home / "sessions").mkdir()
    (source_codex_home / "ambient-suggestions").mkdir()
    monkeypatch.setenv("CODEX_HOME", str(source_codex_home))
    install_fake_popen(monkeypatch, calls=calls)

    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="implementation",
        adapter="codex",
        task="Implement isolation.",
        context="Approved artifacts are available.",
    )

    result = store.run_job(job["id"])

    assert result["status"] == "completed"
    launched_home = calls[0]["env"]["CODEX_HOME"]
    assert launched_home != str(source_codex_home)
    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / job["id"]
    assert launched_home == str(job_root / ".codex-home")
    isolated_home = Path(launched_home)
    assert (isolated_home / "auth.json").exists()
    assert (isolated_home / "AGENTS.md").exists() is False
    assert (isolated_home / "sessions").exists() is False
    assert (isolated_home / "ambient-suggestions").exists() is False


def test_worker_backend_doctor_builtin_modes_are_always_ready():
    report = worker_backend_doctor("fake")
    assert report["kind"] == "builtin"
    assert report["ready"] is True
    assert "no" in report["hint"].lower()  # "needs no CLI or credentials"


def test_worker_backend_doctor_anthropic_ready_with_cli_and_key(monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    report = worker_backend_doctor("anthropic")
    assert report["kind"] == "cli"
    assert report["command"] == "claude"
    assert report["available"] is True
    assert report["auth_present"] is True
    assert report["ready"] is True
    # never echo the secret value
    assert "sk-ant-xxx" not in json.dumps(report)


def test_worker_backend_doctor_anthropic_hint_when_no_key(monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.which", lambda name: f"/fake/bin/{name}")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    report = worker_backend_doctor("anthropic")
    assert report["available"] is True
    assert report["auth_present"] is False
    assert "login" in report["hint"].lower()


def test_worker_backend_doctor_codex_not_available(monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.which", lambda name: None)
    monkeypatch.setattr("goodteam_os.workers.DEFAULT_CODEX_PATH", "/no/such/codex")
    report = worker_backend_doctor("codex")
    assert report["available"] is False
    assert report["ready"] is False
    assert "path" in report["hint"].lower()


def test_worker_backend_doctor_codex_warns_about_env_shadowing_auth_file(tmp_path, monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.which", lambda name: f"/fake/bin/{name}")
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_API_KEY", "stale-token")
    report = worker_backend_doctor("codex")
    assert report["available"] is True
    assert report["auth_file_present"] is True
    # the hard-won footprint lesson: a stale env var shadows ~/.codex/auth.json
    assert "env -u CODEX_API_KEY" in report["hint"]
    assert "stale-token" not in json.dumps(report)


def test_worker_backend_doctor_gemini_ready_with_cli_and_key(monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret")

    report = worker_backend_doctor("gemini")

    assert report["kind"] == "cli"
    assert report["command"] == "gemini"
    assert report["available"] is True
    assert report["auth_present"] is True
    assert report["ready"] is True
    assert "gemini-secret" not in json.dumps(report)


def test_worker_backend_doctor_accepts_agy_backend(monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.which", lambda name: f"/fake/bin/{name}")

    report = worker_backend_doctor("agy")

    assert report["kind"] == "cli"
    assert report["command"] == "agy"


def test_query_codex_models_parses_list_visible_slugs(monkeypatch):
    calls = []

    def fake_run(command, *, check, capture_output, text, timeout):
        calls.append(
            {
                "command": command,
                "check": check,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
            }
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "models": [
                        {"slug": "gpt-5.5", "visibility": "list"},
                        {"slug": "hidden-model", "visibility": "hide"},
                        {"slug": "gpt-5.3-codex", "visibility": "list"},
                        {"slug": "", "visibility": "list"},
                    ]
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("goodteam_os.workers.subprocess.run", fake_run)

    assert _query_codex_models("/fake/bin/codex") == ("gpt-5.5", "gpt-5.3-codex")
    assert calls == [
        {
            "command": ["/fake/bin/codex", "debug", "models"],
            "check": False,
            "capture_output": True,
            "text": True,
            "timeout": 10,
        }
    ]


def test_query_codex_models_returns_none_on_failures(monkeypatch):
    def set_run(result_or_exc):
        def fake_run(command, *, check, capture_output, text, timeout):
            if isinstance(result_or_exc, BaseException):
                raise result_or_exc
            return subprocess.CompletedProcess(command, result_or_exc[0], stdout=result_or_exc[1], stderr="")

        monkeypatch.setattr("goodteam_os.workers.subprocess.run", fake_run)

    set_run((1, '{"models":[{"slug":"gpt-5.5","visibility":"list"}]}'))
    assert _query_codex_models("/fake/bin/codex") is None

    set_run(subprocess.TimeoutExpired(["codex", "debug", "models"], 10))
    assert _query_codex_models("/fake/bin/codex") is None

    set_run((0, "{not json"))
    assert _query_codex_models("/fake/bin/codex") is None

    set_run((0, '{"models":[{"slug":"hidden","visibility":"hide"}]}'))
    assert _query_codex_models("/fake/bin/codex") is None


def test_query_agy_models_parses_line_or_json_catalog(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})

        class Completed:
            returncode = 0
            stdout = "Gemini 3.5 Flash (Medium)\nClaude Sonnet 4.6\n"

        return Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _query_agy_models("/fake/bin/agy") == ("Gemini 3.5 Flash (Medium)", "Claude Sonnet 4.6")
    assert calls[0]["command"] == ["/fake/bin/agy", "models"]

    def fake_json_run(command, **kwargs):
        class Completed:
            returncode = 0
            stdout = '{"models":[{"name":"Agy A"},{"label":"Agy B"}]}'

        return Completed()

    monkeypatch.setattr(subprocess, "run", fake_json_run)

    assert _query_agy_models("/fake/bin/agy") == ("Agy A", "Agy B")


def test_query_agy_models_returns_none_on_failures(monkeypatch):
    def set_run(value):
        def fake_run(command, **kwargs):
            if isinstance(value, BaseException):
                raise value

            class Completed:
                returncode = value[0]
                stdout = value[1]

            return Completed()

        monkeypatch.setattr(subprocess, "run", fake_run)

    set_run((1, ""))
    assert _query_agy_models("/fake/bin/agy") is None

    set_run(subprocess.TimeoutExpired(["agy", "models"], 10))
    assert _query_agy_models("/fake/bin/agy") is None

    set_run((0, "\n\n"))
    assert _query_agy_models("/fake/bin/agy") is None


def test_discover_installed_agents_dedupes_aliases_and_reflects_availability(monkeypatch):
    available_commands = {"codex", "claude", "gemini"}

    def fake_which(name):
        if name in available_commands:
            return f"/fake/bin/{name}"
        return None

    monkeypatch.setattr("goodteam_os.workers.which", fake_which)
    monkeypatch.setattr("goodteam_os.workers.DEFAULT_CODEX_PATH", "/missing/codex")
    monkeypatch.setattr("goodteam_os.workers.DEFAULT_CLAUDE_PATH", "/missing/claude")
    monkeypatch.setattr("goodteam_os.workers.DEFAULT_GEMINI_PATH", "/missing/gemini")
    monkeypatch.setattr("goodteam_os.workers.DEFAULT_AGY_PATH", "/missing/agy")
    monkeypatch.setenv("CODEX_API_KEY", "codex-secret")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE", raising=False)
    monkeypatch.delenv("AGY_API_KEY", raising=False)
    monkeypatch.setattr("goodteam_os.workers._query_codex_models", lambda command_path: None)
    monkeypatch.setattr("goodteam_os.workers._query_agy_models", lambda command_path: None)

    discovered = discover_installed_agents()

    assert [item["backend"] for item in discovered] == ["agy", "claude", "codex", "gemini"]
    assert [item["aliases"] for item in discovered] == [
        ["agy", "antigravity"],
        ["anthropic", "claude"],
        ["codex"],
        ["gemini"],
    ]
    by_backend = {item["backend"]: item for item in discovered}
    assert by_backend["codex"]["available"] is True
    assert by_backend["codex"]["ready"] is True
    assert by_backend["codex"]["command"] == "codex"
    assert by_backend["codex"]["model_default"] == "gpt-5.3-codex"
    assert "gpt-5.5" in by_backend["codex"]["models"]
    assert "gpt-5.3-codex" in by_backend["codex"]["models"]
    assert by_backend["codex"]["models_source"] == "static"
    assert by_backend["claude"]["available"] is True
    assert by_backend["claude"]["ready"] is True
    assert by_backend["claude"]["model_default"] == "claude-sonnet-4-6"
    assert "claude-sonnet-4-6" in by_backend["claude"]["models"]
    assert by_backend["claude"]["models_source"] == "static"
    assert by_backend["gemini"]["available"] is True
    assert by_backend["gemini"]["ready"] is True
    assert by_backend["gemini"]["model_default"] == "auto"
    assert "gemini-2.5-pro" in by_backend["gemini"]["models"]
    assert by_backend["gemini"]["models_source"] == "static"
    assert by_backend["agy"]["available"] is False
    assert by_backend["agy"]["ready"] is False
    assert by_backend["agy"]["models"] == ["auto"]
    assert by_backend["agy"]["model_default"] == "auto"
    assert by_backend["agy"]["models_source"] == "static"


def test_discover_installed_agents_uses_live_codex_models(monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setenv("CODEX_API_KEY", "codex-secret")
    monkeypatch.setattr(
        "goodteam_os.workers._query_codex_models",
        lambda command_path: ("gpt-live-a", "gpt-live-b", "gpt-live-a"),
    )
    monkeypatch.setattr("goodteam_os.workers._query_agy_models", lambda command_path: ("Agy Medium", "Agy Max"))

    by_backend = {item["backend"]: item for item in discover_installed_agents()}

    assert by_backend["codex"]["models"] == ["gpt-live-a", "gpt-live-b"]
    assert by_backend["codex"]["models_source"] == "live"
    assert by_backend["claude"]["models_source"] == "static"
    assert "claude-sonnet-4-6" in by_backend["claude"]["models"]
    assert by_backend["gemini"]["models_source"] == "static"
    assert "gemini-2.5-pro" in by_backend["gemini"]["models"]
    assert by_backend["agy"]["models_source"] == "live"
    assert by_backend["agy"]["models"] == ["auto", "Agy Medium", "Agy Max"]


def test_discover_installed_agents_uses_static_codex_models_when_live_query_fails(monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setenv("CODEX_API_KEY", "codex-secret")
    monkeypatch.setattr("goodteam_os.workers._query_codex_models", lambda command_path: None)
    monkeypatch.setattr("goodteam_os.workers._query_agy_models", lambda command_path: None)

    by_backend = {item["backend"]: item for item in discover_installed_agents()}

    assert "gpt-5.5" in by_backend["codex"]["models"]
    assert "gpt-5.3-codex" in by_backend["codex"]["models"]
    assert by_backend["codex"]["models_source"] == "static"


def test_agy_build_command_includes_model_when_provided():
    from goodteam_os.workers import _agy_build_command

    cmd = _agy_build_command("/fake/bin/agy", "gpt-5.5", Path("."), Path("r.md"))

    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "gpt-5.5"


def test_agy_build_command_omits_model_when_blank():
    from goodteam_os.workers import _agy_build_command

    cmd = _agy_build_command("/fake/bin/agy", "", Path("."), Path("r.md"))

    assert "--model" not in cmd


def test_agy_build_command_has_no_gemini_only_flags():
    from goodteam_os.workers import CLI_BACKENDS, _command_with_prompt

    spec = CLI_BACKENDS["agy"]
    cmd = _command_with_prompt(
        spec.build_command("/fake/bin/agy", "", Path("."), Path("r.md")),
        "do the thing",
        prompt_via=spec.prompt_via,
    )
    assert cmd[:3] == ["/fake/bin/agy", "-p", "do the thing"]
    assert "--dangerously-skip-permissions" in cmd
    assert "--model" not in cmd
    assert "--approval-mode" not in cmd


def test_anthropic_adapter_invokes_claude_cli_and_captures_stdout_result(tmp_path, monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.which", lambda name: f"/fake/bin/{name}")
    calls = []
    install_fake_popen(
        monkeypatch,
        stdout_text="# Worker Result\n\nStatus: pass\n\nClaude implemented the feature.\n",
        stderr_text="",
        calls=calls,
    )
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="implementation",
        adapter="anthropic",
        model="anthropic:claude-sonnet-4-6",
        task="Implement the CONTENTOPS MVP.",
        context="Approved artifacts are available.",
        expected_artifacts=["README.md"],
    )

    result = store.run_job(job["id"])

    assert result["status"] == "completed"
    assert result["adapter"] == "anthropic"
    assert result["model"] == "claude-sonnet-4-6"
    assert result["exit_code"] == 0
    assert calls[0]["cwd"] == tmp_path
    assert calls[0]["command"] == [
        "/fake/bin/claude",
        "-p",
        "--model",
        "claude-sonnet-4-6",
        "--dangerously-skip-permissions",
    ]
    assert "Implement the CONTENTOPS MVP." in calls[0]["input"]
    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / job["id"]
    status = json.loads((job_root / "status.json").read_text(encoding="utf-8"))
    assert status["preflight"]["claude_available"] is True
    assert "ANTHROPIC_API_KEY" in status["preflight"]["auth_env"]
    assert "value" not in json.dumps(status["preflight"]).lower()
    # claude -p prints the final message to stdout, so it becomes the result.
    assert "Claude implemented the feature." in (job_root / "result.md").read_text(encoding="utf-8")


def test_anthropic_adapter_reports_missing_cli(tmp_path, monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.which", lambda name: None)
    monkeypatch.setattr("goodteam_os.workers.DEFAULT_CLAUDE_PATH", "/no/such/claude")
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="implementation",
        adapter="anthropic",
        task="Implement the CONTENTOPS MVP.",
        context="ctx",
    )

    result = store.run_job(job["id"])

    assert result["status"] == "failed"
    assert "not available" in result["summary"].lower()
    assert result["exit_code"] is None


def test_gemini_adapter_passes_prompt_as_p_arg_value_and_captures_stdout(tmp_path, monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.which", lambda name: f"/fake/bin/{name}")
    calls = []
    install_fake_popen(
        monkeypatch,
        stdout_text="# Worker Result\n\nStatus: pass\n\nGemini implemented the feature.\n",
        stderr_text="",
        calls=calls,
    )
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="implementation",
        adapter="gemini",
        model="gemini:gemini-2.5-pro",
        task="Implement the frontend dispatch track.",
        context="Approved artifacts are available.",
    )

    result = store.run_job(job["id"])

    assert result["status"] == "completed"
    assert result["adapter"] == "gemini"
    command = calls[0]["command"]
    prompt_index = command.index("-p")
    assert command[prompt_index + 1].startswith("# GoodTeam Worker Job")
    assert "Implement the frontend dispatch track." in command[prompt_index + 1]
    assert command[prompt_index + 2 : prompt_index + 4] == ["--approval-mode", "yolo"]
    assert "--model" in command
    assert command[command.index("--model") + 1] == "gemini-2.5-pro"
    assert calls[0]["input"] is None
    job_root = tmp_path / ".goodteam" / "workers" / "jobs" / job["id"]
    assert "Gemini implemented the feature." in (job_root / "result.md").read_text(encoding="utf-8")


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
                "stdout_path": str(job_root / "stdout.txt"),
                "stderr_path": str(job_root / "stderr.txt"),
                "result_path": str(job_root / "result.md"),
            }
        ),
        encoding="utf-8",
    )

    [listed] = store.list_jobs()

    assert listed["status"] == "possibly_stuck"
    assert listed["raw_status"] == "running"
    assert listed["heartbeat_at"] == old_heartbeat


def test_worker_job_store_reports_stale_dead_pid_as_failed(tmp_path, monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.WORKER_STALE_AFTER_SECONDS", 1)
    monkeypatch.setattr("goodteam_os.workers._is_pid_alive", lambda pid: False)
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
                "pid": 4242,
                "stdout_path": str(job_root / "stdout.txt"),
                "stderr_path": str(job_root / "stderr.txt"),
                "result_path": str(job_root / "result.md"),
            }
        ),
        encoding="utf-8",
    )

    [listed] = store.list_jobs()

    assert listed["status"] == "failed"
    assert listed["raw_status"] == "running"
    assert listed["failure_class"] == "stuck"
    assert listed["disposition"] == "abandoned"
    assert listed["summary"] == "Worker heartbeat is stale and the process is no longer alive."
    assert listed["summary"] != "Codex CLI is running."


def test_worker_job_store_does_not_fail_live_running_job(tmp_path, monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.WORKER_STALE_AFTER_SECONDS", 1)
    monkeypatch.setattr("goodteam_os.workers._is_pid_alive", lambda pid: True)
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
                "pid": 4242,
                "stdout_path": str(job_root / "stdout.txt"),
                "stderr_path": str(job_root / "stderr.txt"),
                "result_path": str(job_root / "result.md"),
            }
        ),
        encoding="utf-8",
    )

    [listed] = store.list_jobs()

    assert listed["status"] == "possibly_stuck"
    assert listed["raw_status"] == "running"
    assert listed["pid"] == 4242


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
    assert calls[0]["command"][:4] == ["/fake/bin/codex", "exec", "--model", "gpt-5.3-codex"]
    assert "Configured worker backend: codex." in calls[0]["input"]
    assert "Configured model: gpt-5.3-codex." in calls[0]["input"]


def test_worker_prompt_uses_adapter_default_model_instead_of_codex_default_for_agy(tmp_path, monkeypatch):
    monkeypatch.setattr("goodteam_os.workers.which", lambda name: f"/fake/bin/{name}")
    calls = []
    install_fake_popen(
        monkeypatch,
        result_text="Agy implemented the feature.",
        calls=calls,
    )
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="ui",
        adapter="agy",
        task="Polish the UI.",
        context="Approved artifacts are available.",
    )

    result = store.run_job(job["id"])

    assert result["model"] == "auto"
    command = calls[0]["command"]
    prompt = command[command.index("-p") + 1]
    assert calls[0]["input"] is None
    assert "Configured worker backend: agy." in prompt
    assert "Configured model: auto." in prompt
    assert "gpt-5.3-codex" not in prompt


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


def test_classify_worker_failure_maps_known_summary_patterns():
    codex_auth = _codex_summary(1, 'ERROR: unexpected status 401 Unauthorized: {"code":"INVALID_API_KEY"}')
    codex_stream = _codex_summary(1, "stream disconnected before response.completed")
    claude_auth = _claude_summary(1, "Authentication failed")
    claude_rate = _claude_summary(1, "429 rate limit reached")

    assert classify_worker_failure(codex_auth, 1) == "auth"
    assert classify_worker_failure(codex_stream, 1) == "stream_disconnect"
    assert classify_worker_failure(claude_auth, 1) == "auth"
    assert classify_worker_failure(claude_rate, 1) == "rate_limit"
    assert classify_worker_failure("Codex CLI timed out after 1800 seconds.", None) == "timeout"
    assert classify_worker_failure("Codex CLI exited with code 1: 503 Service Unavailable.", 1) == "network"
    assert classify_worker_failure("Codex CLI exited with code 1.", 1, "Cannot find module 'x'") == "scaffold"
    assert classify_worker_failure("Codex CLI exited with code 1.", 1, "random failure") == "unknown"


def test_is_transient_only_true_for_retryable_classes():
    assert is_transient("auth") is True
    assert is_transient("rate_limit") is True
    assert is_transient("network") is True
    assert is_transient("timeout") is True
    assert is_transient("stream_disconnect") is True
    assert is_transient("scaffold") is False
    assert is_transient("unknown") is False
    assert is_transient("code_bug") is False
    assert is_transient("ambiguous_spec") is False


def test_cli_retry_recovers_after_transient_failure(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr("goodteam_os.workers._sleep", lambda seconds: sleeps.append(seconds))
    install_fake_popen_sequence(
        monkeypatch,
        responses=[
            {
                "returncode": 1,
                "stderr_text": "ERROR: unexpected status 503 Service Unavailable",
                "result_text": "",
            },
            {
                "returncode": 0,
                "stderr_text": "",
                "result_text": "# Worker Result\n\nRecovered on retry.\n",
            },
        ],
    )
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="implementation",
        adapter="codex",
        task="Implement retry logic.",
        context="Approved artifacts are available.",
    )

    result = store.run_job(job["id"])

    assert result["status"] == "completed"
    assert result["attempts"] == 2
    assert result["disposition"] == "recovered"
    assert result["failure_class"] == "network"
    assert sleeps == [2]


def test_cli_retry_escalates_after_max_transient_failures(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr("goodteam_os.workers._sleep", lambda seconds: sleeps.append(seconds))
    install_fake_popen_sequence(
        monkeypatch,
        responses=[
            {"returncode": 1, "stderr_text": "ERROR: stream disconnected before response.completed", "result_text": ""},
        ],
    )
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="implementation",
        adapter="codex",
        task="Implement retry logic.",
        context="Approved artifacts are available.",
    )

    result = store.run_job(job["id"])

    assert result["status"] == "failed"
    assert result["attempts"] == WORKER_MAX_ATTEMPTS
    assert result["failure_class"] == "stream_disconnect"
    assert result["disposition"] == "escalated"
    assert len(sleeps) == WORKER_MAX_ATTEMPTS - 1


def test_cli_non_transient_failure_is_not_retried(tmp_path, monkeypatch):
    sleeps = []
    calls = []
    monkeypatch.setattr("goodteam_os.workers._sleep", lambda seconds: sleeps.append(seconds))
    install_fake_popen_sequence(
        monkeypatch,
        responses=[{"returncode": 1, "stderr_text": "Cannot find module 'better-sqlite3'", "result_text": ""}],
        calls=calls,
    )
    store = WorkerJobStore(tmp_path)
    job = store.create_job(
        run_id="run-1",
        role="implementation",
        adapter="codex",
        task="Implement retry logic.",
        context="Approved artifacts are available.",
    )

    result = store.run_job(job["id"])

    assert result["status"] == "failed"
    assert result["attempts"] == 1
    assert result["failure_class"] == "scaffold"
    assert result["disposition"] == "escalated"
    assert len(calls) == 1
    assert sleeps == []


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


def test_codex_worker_prompt_requests_memory_write_back_sections_without_copyable_bullets(tmp_path, monkeypatch):
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

    prompt = calls[0]["input"]
    assert "## Memory Write-Back Sections" in prompt
    assert "Do not copy the parenthetical guidance text itself" in prompt
    assert "## Learned\n(one short bullet per reusable fact or lesson" in prompt
    assert "## Caveats\n(one short bullet per risk, gotcha" in prompt
    assert "## Decisions\n(one short bullet per durable decision" in prompt
    assert "## Open Questions\n(one short bullet per unresolved question" in prompt
    assert "- Reusable facts" not in prompt


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
