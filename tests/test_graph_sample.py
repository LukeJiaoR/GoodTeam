import json
from pathlib import Path
from types import MappingProxyType

from goodteam_os.graph import (
    advance_approved_workflow,
    collect_project_context,
    generate_rfc,
    generate_prd,
    implementation_task,
    resolve_project_runtime_root,
    resolve_role_adapter,
    render_downstream_artifact_markdown,
    render_prd_markdown,
    run_implementation_workers,
    run_until_next_gate,
    worker_blocker_message,
)
from goodteam_os.graph import run_adoption_until_next_gate
from goodteam_os.memory import RoleMemoryStore
from goodteam_os.state import create_initial_state
from goodteam_os.workflow_table import WorkflowNode


def test_worker_blocker_message_includes_failure_class_and_attempts():
    message = worker_blocker_message(
        {
            "role": "implementation",
            "summary": "Process exited 1",
            "failure_class": "timeout",
            "attempts": 3,
        }
    )

    assert message == "implementation worker failed [timeout, 3 attempts]: Process exited 1"


def test_worker_blocker_message_omits_failure_bracket_when_missing():
    message = worker_blocker_message({"role": "implementation", "summary": "Process exited 1"})

    assert message == "implementation worker failed: Process exited 1"


def test_resolve_role_adapter_prefers_per_role_then_global_then_default():
    target = {
        "worker_backend": {"mode": "codex"},
        "workers": {"implementation": "anthropic", "qa": "codex"},
    }
    # per-role override wins
    assert resolve_role_adapter(target, "implementation") == "anthropic"
    assert resolve_role_adapter(target, "qa") == "codex"
    # role not listed falls back to the global mode
    assert resolve_role_adapter(target, "ui") == "codex"
    # nothing configured at all falls back to codex
    assert resolve_role_adapter({}, "ui") == "codex"
    # `manual` is an alias for the human worker
    assert resolve_role_adapter({"worker_backend": {"mode": "manual"}}, "ui") == "human"


def test_graph_reaches_prd_gate(tmp_path):
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")

    result = run_until_next_gate(state=state, workspace=tmp_path)

    assert result.current_node == "prd_review_gate"
    assert result.next_node == "await_prd_approval"
    assert "prd" in result.artifacts


def test_adoption_graph_reaches_takeover_review_gate(tmp_path):
    project_config = write_project_fixture(tmp_path)
    state = create_initial_state("run-1", "SAMPLE", "Take over the configured product")

    result = run_adoption_until_next_gate(state=state, workspace=tmp_path, project_config=project_config)

    assert result.source_type == "existing_project"
    assert result.current_node == "takeover_review_gate"
    assert result.next_node == "await_takeover_approval"
    assert "takeover" in result.artifacts
    assert result.approvals["takeover"] == "pending"
    assert "prd" not in result.artifacts
    assert (tmp_path / ".goodteam" / "tasks" / "tasks.json").is_file()
    assert (tmp_path / ".goodteam" / "memory" / "roles" / "product-requirement" / "short-term.md").is_file()
    assert (tmp_path / ".goodteam" / "memory" / "roles" / "architect" / "long-term.md").is_file()


def write_project_fixture(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    for repo in [docs, backend, frontend]:
        repo.mkdir()

    (docs / "codex.md").write_text("# Shared Memory\n- Ticket-first handoff.\n", encoding="utf-8")
    (docs / "docs").mkdir()
    (docs / "docs" / "backend-roadmap.md").write_text(
        "# Backend Roadmap\n\n"
        "## Current Priority Order\n\n"
        "- Complete Better Auth source-of-truth integration.\n"
        "- Deploy Postgres-backed dev environment.\n",
        encoding="utf-8",
    )
    (backend / "README.md").write_text("# Backend\n- Owns API contracts.\n", encoding="utf-8")
    (frontend / "README.md").write_text("# Frontend\n- Owns UI states.\n", encoding="utf-8")

    config = tmp_path / "project.local.yaml"
    config.write_text(
        "\n".join(
            [
                "name: Sample Product",
                "key: SAMPLE",
                "repos:",
                f"  docs: {docs}",
                f"  backend: {backend}",
                f"  frontend: {frontend}",
            ]
        ),
        encoding="utf-8",
    )
    return config


def test_prd_uses_project_roadmap_contract_instead_of_takeover_workflow(tmp_path):
    project_config = write_project_fixture(tmp_path)
    state = create_initial_state("run-1", "SAMPLE", "Take over the configured product")

    prd = render_prd_markdown(state, project_config)

    assert "# Product Roadmap PRD" in prd
    assert "## Project Context Read" in prd
    assert "## Workflow Request" in prd
    assert "codex.md" in prd
    assert "backend-roadmap.md" in prd
    assert "Complete Better Auth source-of-truth integration." in prd
    assert "Backend overview" in prd
    assert "Produce a real takeover artifact" not in prd
    assert "Propose next GoodTeam local tasks" not in prd
    assert "Do not bypass the configured GoodTeam local task flow" not in prd


def test_prd_is_decision_ready_content_not_writing_instructions(tmp_path):
    project_config = write_project_fixture(tmp_path)
    state = create_initial_state("run-1", "SAMPLE", "Take over the configured product")

    prd = render_prd_markdown(state, project_config)

    assert "## Product Decision" in prd
    assert "## Users And Value" in prd
    assert "## V1 Scope" in prd
    assert "## Non-Goals" in prd
    assert "## Acceptance Criteria" in prd
    assert "## Risks And Open Questions" in prd
    assert "Write a decision-ready PRD" not in prd
    assert "List the next product capabilities" not in prd
    assert "PRD Writing Instructions" not in prd


def test_prd_template_is_generic_for_contentops_ideas(tmp_path):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state(
        "run-1",
        "CONTENTOPS",
        "Build ContentOps Studio for reusable brand briefs, source capture, topic organization, "
        "multi-platform drafts, review/publish board, and verification evidence. Prefer mature "
        "open-source components and the existing Fastify/Zod/SQLite + React/Vite/Vitest stack over "
        "custom infrastructure.",
    )
    state.delivery_mode = "init_project"

    prd = render_prd_markdown(state, project_config)

    assert "ContentOps Studio v1" not in prd
    assert "Reviewers or founders who need evidence" not in prd
    assert "Reuse-first technical constraint" not in prd
    assert "Acceptance Criteria" in prd
    assert "Define the next v1 product slice for AI Content Studio" in prd


def test_generate_prd_dispatches_product_requirement_worker_and_overwrites_prd(tmp_path, monkeypatch):
    project = tmp_path / "contentops"
    for repo in ["docs", "backend", "frontend"]:
        (project / repo).mkdir(parents=True)
    project_config = tmp_path / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir()
    project_config.write_text(
        "\n".join(
            [
                "name: AI Content Studio",
                "key: CONTENTOPS",
                "worker_backend:",
                "  mode: codex",
                "repos:",
                f"  docs: {project / 'docs'}",
                f"  backend: {project / 'backend'}",
                f"  frontend: {project / 'frontend'}",
            ]
        ),
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.role_models["product_requirement"] = {"primary": "codex:gpt-5.5"}
    old_prd = tmp_path / "artifacts" / "runs" / "run-1" / "prd" / "prd.md"
    old_prd.parent.mkdir(parents=True, exist_ok=True)
    old_prd.write_text("# Old PRD\n", encoding="utf-8")
    state.artifacts["prd"] = str(old_prd)
    from goodteam_os.state import StateStore

    StateStore(tmp_path).write_project_current(state)

    seen: dict[str, object] = {}

    def fake_create_job(self, **kwargs):
        seen.update(kwargs)
        return {"id": "job-1", **kwargs}

    def fake_run_job(self, job_id):
        job_root = self.root / job_id
        job_root.mkdir(parents=True, exist_ok=True)
        result_path = job_root / "result.md"
        result_path.write_text("# Real PRD\n\nGenerated by worker.\n", encoding="utf-8")
        return {
            "job_id": job_id,
            "run_id": "run-1",
            "role": "product_requirement",
            "status": "completed",
            "summary": "completed",
            "result_path": str(result_path),
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.create_job", fake_create_job)
    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    payload = generate_prd(workspace=tmp_path, project_key="CONTENTOPS")

    assert seen["role"] == "product_requirement"
    assert seen["adapter"] == "codex"
    assert seen["model"] == "gpt-5.5"
    assert "Write a complete PRD for the following product idea:" in seen["task"]
    assert payload["current_node"] == "prd_review_gate"
    assert payload["next_node"] == "await_prd_approval"
    assert Path(payload["artifacts"]["prd"]).read_text(encoding="utf-8") == "# Real PRD\n\nGenerated by worker.\n"


def test_generate_prd_keeps_existing_prd_on_worker_failure(tmp_path, monkeypatch):
    project = tmp_path / "contentops"
    docs = project / "docs"
    docs.mkdir(parents=True)
    project_config = tmp_path / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir()
    project_config.write_text(
        f"name: AI Content Studio\nkey: CONTENTOPS\nrepos:\n  docs: {docs}\n",
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    prd = tmp_path / "artifacts" / "runs" / "run-1" / "prd" / "prd.md"
    prd.parent.mkdir(parents=True, exist_ok=True)
    prd.write_text("# Placeholder PRD\n", encoding="utf-8")
    state.artifacts["prd"] = str(prd)
    from goodteam_os.state import StateStore

    StateStore(tmp_path).write_project_current(state)

    monkeypatch.setattr(
        "goodteam_os.graph.WorkerJobStore.run_job",
        lambda self, job_id: {
            "job_id": job_id,
            "run_id": "run-1",
            "role": "product_requirement",
            "status": "failed",
            "summary": "CLI failed",
            "failure_class": "timeout",
        },
    )

    try:
        generate_prd(workspace=tmp_path, project_key="CONTENTOPS")
    except RuntimeError as exc:
        assert "[timeout]" in str(exc)
    else:
        raise AssertionError("Expected generate_prd failure")

    assert prd.read_text(encoding="utf-8") == "# Placeholder PRD\n"


def test_generate_rfc_dispatches_architect_worker_and_overwrites_rfc(tmp_path, monkeypatch):
    project = tmp_path / "contentops"
    for repo in ["docs", "backend", "frontend"]:
        (project / repo).mkdir(parents=True)
    project_config = tmp_path / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir()
    project_config.write_text(
        "\n".join(
            [
                "name: AI Content Studio",
                "key: CONTENTOPS",
                "worker_backend:",
                "  mode: codex",
                "repos:",
                f"  docs: {project / 'docs'}",
                f"  backend: {project / 'backend'}",
                f"  frontend: {project / 'frontend'}",
            ]
        ),
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.role_models["architect"] = {"primary": "codex:gpt-5.5"}
    old_rfc = tmp_path / "artifacts" / "runs" / "run-1" / "rfc" / "rfc.md"
    old_rfc.parent.mkdir(parents=True, exist_ok=True)
    old_rfc.write_text("# Old RFC\n", encoding="utf-8")
    state.artifacts["rfc"] = str(old_rfc)
    from goodteam_os.state import StateStore

    StateStore(tmp_path).write_project_current(state)

    seen: dict[str, object] = {}

    def fake_create_job(self, **kwargs):
        seen.update(kwargs)
        return {"id": "job-1", **kwargs}

    def fake_run_job(self, job_id):
        job_root = self.root / job_id
        job_root.mkdir(parents=True, exist_ok=True)
        result_path = job_root / "result.md"
        result_path.write_text("# Real RFC\n\nGenerated by worker.\n", encoding="utf-8")
        return {
            "job_id": job_id,
            "run_id": "run-1",
            "role": "architect",
            "status": "completed",
            "summary": "completed",
            "result_path": str(result_path),
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.create_job", fake_create_job)
    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    payload = generate_rfc(workspace=tmp_path, project_key="CONTENTOPS")

    assert seen["role"] == "architect"
    assert seen["adapter"] == "codex"
    assert seen["model"] == "gpt-5.5"
    assert "Write a complete RFC / technical design for:" in seen["task"]
    assert payload["current_node"] == "rfc_review_gate"
    assert payload["next_node"] == "await_rfc_approval"
    assert Path(payload["artifacts"]["rfc"]).read_text(encoding="utf-8") == "# Real RFC\n\nGenerated by worker.\n"


def test_generate_rfc_keeps_existing_rfc_on_worker_failure(tmp_path, monkeypatch):
    project = tmp_path / "contentops"
    docs = project / "docs"
    docs.mkdir(parents=True)
    project_config = tmp_path / "projects" / "contentops.local.yaml"
    project_config.parent.mkdir()
    project_config.write_text(
        f"name: AI Content Studio\nkey: CONTENTOPS\nrepos:\n  docs: {docs}\n",
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    rfc = tmp_path / "artifacts" / "runs" / "run-1" / "rfc" / "rfc.md"
    rfc.parent.mkdir(parents=True, exist_ok=True)
    rfc.write_text("# Placeholder RFC\n", encoding="utf-8")
    state.artifacts["rfc"] = str(rfc)
    from goodteam_os.state import StateStore

    StateStore(tmp_path).write_project_current(state)

    monkeypatch.setattr(
        "goodteam_os.graph.WorkerJobStore.run_job",
        lambda self, job_id: {
            "job_id": job_id,
            "run_id": "run-1",
            "role": "architect",
            "status": "failed",
            "summary": "CLI failed",
            "failure_class": "timeout",
        },
    )

    try:
        generate_rfc(workspace=tmp_path, project_key="CONTENTOPS")
    except RuntimeError as exc:
        assert "[timeout]" in str(exc)
    else:
        raise AssertionError("Expected generate_rfc failure")

    assert rfc.read_text(encoding="utf-8") == "# Placeholder RFC\n"


def test_prd_includes_init_project_delivery_standard(tmp_path):
    project_config = write_project_fixture(tmp_path)
    state = create_initial_state("run-1", "SAMPLE", "Take over the configured product")
    state.delivery_mode = "init_project"

    prd = render_prd_markdown(state, project_config)

    assert "Delivery mode: `init_project`" in prd
    assert "frontend/backend separation" in prd
    assert "data layer" in prd
    assert "routing" in prd
    assert "DevOps" in prd


def test_rfc_includes_multi_service_delivery_standard(tmp_path):
    project_config = write_project_fixture(tmp_path)
    state = create_initial_state("run-1", "SAMPLE", "Take over the configured product")
    state.delivery_mode = "multi_service"

    rfc = render_downstream_artifact_markdown("rfc", state, project_config)

    assert "Delivery mode: `multi_service`" in rfc
    assert "service boundaries" in rfc
    assert "API or event contracts" in rfc
    assert "observability" in rfc


def test_rfc_template_is_generic_for_contentops_ideas(tmp_path):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state(
        "run-1",
        "CONTENTOPS",
        "Build ContentOps Studio for reusable brand briefs, source capture, topic organization, "
        "multi-platform drafts, review/publish board, and verification evidence. Prefer mature "
        "open-source components and the existing Fastify/Zod/SQLite + React/Vite/Vitest stack over "
        "custom infrastructure.",
    )
    state.delivery_mode = "init_project"
    state.artifacts["prd"] = "/tmp/prd.md"

    rfc = render_downstream_artifact_markdown("rfc", state, project_config)

    assert "## Architecture Decision" in rfc
    assert "## Data Model" in rfc
    assert "## API Contract" in rfc
    assert "ContentOps Studio v1" not in rfc
    assert "brand_briefs" not in rfc
    assert "POST /api/brand-brief" not in rfc
    assert "Build the approved AI Content Studio v1 slice" in rfc
    assert "Use existing persistence models" in rfc


def test_takeover_context_does_not_include_prd_review_prompt(tmp_path):
    project_config = write_project_fixture(tmp_path)
    state = create_initial_state("run-1", "SAMPLE", "Take over the configured product")

    result = run_adoption_until_next_gate(state=state, workspace=tmp_path, project_config=project_config)
    takeover = Path(result.artifacts["takeover"]).read_text(encoding="utf-8")

    assert "Write a decision-ready PRD, not a template." not in takeover
    assert "## Multi-Role PRD Review" not in takeover


def test_collect_project_context_reads_configured_sources(tmp_path):
    project_config = write_project_fixture(tmp_path)
    context = collect_project_context(project_config)
    paths = [item["path"] for item in context]

    assert any(path.endswith("docs/codex.md") for path in paths)
    assert any(path.endswith("docs/docs/backend-roadmap.md") for path in paths)
    assert any(path.endswith("backend/README.md") for path in paths)
    assert any(path.endswith("frontend/README.md") for path in paths)


def test_collect_project_context_reads_project_memory_from_project_root(tmp_path):
    project_config = write_project_fixture(tmp_path)
    memory = tmp_path / ".goodteam" / "memory" / "project-memory.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("# Project Memory\n\n- Stable local project memory.", encoding="utf-8")

    context = collect_project_context(project_config)
    memory_packet = next(item for item in context if item["repo"] == "project_memory")

    assert memory_packet["path"] == str(memory)
    assert "Stable local project memory" in memory_packet["summary"]


def test_resolve_project_runtime_root_prefers_parent_project_directory(tmp_path):
    project = tmp_path / "product"
    docs = project / "docs"
    backend = project / "api"
    frontend = project / "ui"
    for path in [docs, backend, frontend]:
        path.mkdir(parents=True)

    root = resolve_project_runtime_root({"docs": str(docs), "backend": str(backend), "frontend": str(frontend)})

    assert root == project


def test_advance_approved_workflow_generates_rfc_after_prd_approval(tmp_path):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state(
        "run-1",
        "CONTENTOPS",
        "Build an AI content studio for TikTok, Reddit, X, and LinkedIn post production.",
    )
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.approvals["prd"] = "approved"
    state.current_node = "prd_review_gate"
    state.next_node = "architect"

    result = advance_approved_workflow(state=state, workspace=tmp_path, project_config=project_config)

    assert result.current_node == "rfc_review_gate"
    assert result.next_node == "await_rfc_approval"
    assert "rfc" in result.artifacts
    assert Path(result.artifacts["rfc"]).is_file()
    assert "# Technical RFC" in Path(result.artifacts["rfc"]).read_text(encoding="utf-8")


def test_advance_approved_workflow_can_complete_contentops_run(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state(
        "run-1",
        "CONTENTOPS",
        "Build an AI content studio for TikTok, Reddit, X, and LinkedIn post production.",
    )
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.approvals.update({"prd": "approved", "rfc": "approved", "release": "approved"})
    state.current_node = "rfc_review_gate"
    state.next_node = "planner"

    def fake_run_job(self, job_id):
        job_root = self.root / job_id
        job = json.loads((job_root / "job.json").read_text(encoding="utf-8"))
        if job["role"] == "implementation":
            app = self.project_root / "app.py"
            readme = self.project_root / "README.md"
            app.write_text("print('CONTENTOPS ready')\n", encoding="utf-8")
            readme.write_text("# AI Content Studio\n\nRun `python app.py`.\n", encoding="utf-8")
        return {
            "job_id": job_id,
            "run_id": job["run_id"],
            "role": job["role"],
            "adapter": job["adapter"],
            "status": "completed",
            "summary": f"{job['role']} completed",
            "artifacts": [],
            "changed_files": [],
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    result = advance_approved_workflow(state=state, workspace=tmp_path, project_config=project_config)

    assert result.current_node == "complete"
    assert result.next_node == ""
    for artifact in [
        "review_report",
        "task_breakdown",
        "ui_spec",
        "implementation_handoff",
        "qa_report",
        "code_review",
        "release_notes",
        "retro_memory",
    ]:
        assert artifact in result.artifacts
        assert Path(result.artifacts[artifact]).is_file()
    runtime_root = resolve_project_runtime_root({"docs": tmp_path / "contentops" / "docs", "backend": tmp_path / "contentops" / "backend", "frontend": tmp_path / "contentops" / "frontend"})
    assert (runtime_root / "app.py").is_file()
    assert (runtime_root / "README.md").is_file()


def test_release_gate_artifacts_include_worker_evidence_not_placeholders(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state(
        "run-1",
        "CONTENTOPS",
        "Build ContentOps Studio for reusable brand briefs, source capture, topic organization, "
        "multi-platform drafts, review/publish board, and verification evidence.",
    )
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.role_models = {
        "implementation": {"primary": "codex:gpt-5.3-codex"},
        "qa": {"primary": "codex:gpt-5.3-codex"},
    }
    state.approvals.update({"prd": "approved", "rfc": "approved"})
    state.current_node = "rfc_review_gate"
    state.next_node = "planner"

    def fake_run_job(self, job_id):
        job_root = self.root / job_id
        job = json.loads((job_root / "job.json").read_text(encoding="utf-8"))
        if job["role"] == "implementation":
            result_text = (
                "# Implementation Result\n\n"
                "Verification commands run:\n"
                "- `npm run test` passed with 16/16 tests.\n"
                "- `npm run build` passed.\n\n"
                "Unresolved risks:\n"
                "- `better-sqlite3` may require `npm rebuild better-sqlite3` after Node ABI changes.\n"
            )
        elif job["role"] == "ui":
            result_text = (
                "# UI Delivery Result\n\n"
                "Status: pass\n\n"
                "- Changed frontend files under `frontend/src/` and updated CSS tokens.\n"
                "- Homepage Brand Brief first screen is polished and directly usable.\n"
                "- Review board draft cards and evidence dossier readability were improved.\n"
                "- Browser screenshot evidence saved to screenshots/contentops-ui.png.\n"
            )
        else:
            result_text = (
                "# QA Result\n\n"
                "Status: pass\n\n"
                "- PASS live API smoke.\n"
                "- Draft generation returns exactly 4 platforms: `tiktok`, `reddit`, `x`, `linkedin`.\n"
                "- Coverage gaps: no real-browser interactive E2E run was executed.\n"
            )
        result_path = job_root / "result.md"
        result_path.write_text(result_text, encoding="utf-8")
        return {
            "job_id": job_id,
            "run_id": job["run_id"],
            "role": job["role"],
            "adapter": job["adapter"],
            "model": job["model"],
            "status": "completed",
            "summary": f"{job['role']} completed",
            "result_path": str(result_path),
            "artifacts": [],
            "changed_files": [],
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    result = advance_approved_workflow(state=state, workspace=tmp_path, project_config=project_config)

    assert result.current_node == "release_review_gate"
    code_review = Path(result.artifacts["code_review"]).read_text(encoding="utf-8")
    qa_report = Path(result.artifacts["qa_report"]).read_text(encoding="utf-8")
    release_notes = Path(result.artifacts["release_notes"]).read_text(encoding="utf-8")
    for content in [code_review, qa_report, release_notes]:
        assert "gpt-5.3-codex" in content
        assert "npm run test" in content
        assert "npm run build" in content
        assert "Homepage Brand Brief first screen" in content
        assert "screenshots/contentops-ui.png" in content
        assert "no real-browser interactive E2E" in content
        assert "Lead with actionable issues" not in content
        assert "List staging evidence" not in content
        assert "Map requirements to tests" not in content
    assert "## UI Delivery Evidence" in qa_report
    assert "Direct frontend implementation: covered" in qa_report
    assert "Browser screenshot evidence: covered" in qa_report
    assert "Release recommendation: approve for local starter release with documented caveats" in release_notes


def test_delivery_workflow_stops_when_codex_worker_fails(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state(
        "run-1",
        "CONTENTOPS",
        "Build an AI content studio for TikTok, Reddit, X, and LinkedIn post production.",
    )
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.approvals.update({"prd": "approved", "rfc": "approved"})
    state.current_node = "rfc_review_gate"
    state.next_node = "planner"

    def fake_run_job(self, job_id):
        job = json.loads((self.root / job_id / "job.json").read_text(encoding="utf-8"))
        return {
            "job_id": job_id,
            "run_id": job["run_id"],
            "role": job["role"],
            "adapter": job["adapter"],
            "status": "failed",
            "summary": "Codex CLI exited with code 1.",
            "artifacts": [],
            "changed_files": [],
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    result = advance_approved_workflow(state=state, workspace=tmp_path, project_config=project_config)

    assert result.current_node == "implementation"
    assert result.next_node == "blocked"
    assert any("Codex CLI exited with code 1" in blocker for blocker in result.blockers)
    assert "release_notes" not in result.artifacts


def test_delivery_workflow_blocks_when_qa_result_reports_failure(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state(
        "run-1",
        "CONTENTOPS",
        "Build an AI content studio for TikTok, Reddit, X, and LinkedIn post production.",
    )
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.approvals.update({"prd": "approved", "rfc": "approved"})
    state.current_node = "rfc_review_gate"
    state.next_node = "planner"

    def fake_run_job(self, job_id):
        job_root = self.root / job_id
        job = json.loads((job_root / "job.json").read_text(encoding="utf-8"))
        if job["role"] == "qa":
            (job_root / "result.md").write_text(
                "# QA Report\n\nStatus: **fail with one release-blocking UI/API integration gap**.\n",
                encoding="utf-8",
            )
        return {
            "job_id": job_id,
            "run_id": job["run_id"],
            "role": job["role"],
            "adapter": job["adapter"],
            "status": "completed",
            "summary": f"{job['role']} completed",
            "result_path": str(job_root / "result.md"),
            "artifacts": [],
            "changed_files": [],
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    result = advance_approved_workflow(state=state, workspace=tmp_path, project_config=project_config)

    assert result.current_node == "implementation"
    assert result.next_node == "blocked"
    assert any("qa worker reported release-blocking failure" in blocker for blocker in result.blockers)
    assert "release_notes" not in result.artifacts


def test_delivery_failure_uses_declared_on_error_target(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow.")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.approvals.update({"prd": "approved", "rfc": "approved"})
    state.current_node = "rfc_review_gate"
    state.next_node = "planner"

    def fake_run_job(self, job_id):
        job = json.loads((self.root / job_id / "job.json").read_text(encoding="utf-8"))
        return {
            "job_id": job_id,
            "run_id": job["run_id"],
            "role": job["role"],
            "adapter": job["adapter"],
            "status": "failed",
            "summary": "failed",
            "failure_class": "scaffold",
        }

    monkeypatch.setattr(
        "goodteam_os.workflow_table.WORKFLOW_NODES",
        MappingProxyType(
            {
                "planner": WorkflowNode(
                    name="planner",
                    run_guard_gate="rfc",
                    runner_fn="run_delivery_until_release_gate",
                    on_error="failure_triage",
                ),
            }
        ),
    )
    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    result = advance_approved_workflow(state=state, workspace=tmp_path, project_config=project_config)

    assert result.current_node == "implementation"
    assert result.next_node == "failure_triage"
    assert result.blockers


def test_retryable_worker_failure_reruns_then_succeeds_without_blocking(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow.")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.approvals.update({"prd": "approved", "rfc": "approved"})
    state.current_node = "rfc_review_gate"
    state.next_node = "planner"
    calls_by_role: dict[str, int] = {}

    def fake_run_job(self, job_id):
        job = json.loads((self.root / job_id / "job.json").read_text(encoding="utf-8"))
        role = job["role"]
        calls_by_role[role] = calls_by_role.get(role, 0) + 1
        if role == "implementation" and calls_by_role[role] == 1:
            return {
                "job_id": job_id,
                "run_id": job["run_id"],
                "role": role,
                "adapter": job["adapter"],
                "status": "failed",
                "summary": "transient timeout",
                "failure_class": "timeout",
            }
        return {
            "job_id": job_id,
            "run_id": job["run_id"],
            "role": role,
            "adapter": job["adapter"],
            "status": "completed",
            "summary": f"{role} completed",
        }

    monkeypatch.setattr(
        "goodteam_os.workflow_table.WORKFLOW_NODES",
        MappingProxyType(
            {
                "planner": WorkflowNode(
                    name="planner",
                    run_guard_gate="rfc",
                    runner_fn="run_delivery_until_release_gate",
                    retry_max=1,
                    retry_on_classes=("timeout",),
                ),
            }
        ),
    )
    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    result = advance_approved_workflow(state=state, workspace=tmp_path, project_config=project_config)

    assert calls_by_role["implementation"] == 2
    assert result.next_node != "blocked"
    assert not result.blockers
    assert "release_notes" in result.artifacts


def test_retry_exhausted_routes_to_on_error_and_records_blocker(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow.")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.approvals.update({"prd": "approved", "rfc": "approved"})
    state.current_node = "rfc_review_gate"
    state.next_node = "planner"
    calls_by_role: dict[str, int] = {}

    def fake_run_job(self, job_id):
        job = json.loads((self.root / job_id / "job.json").read_text(encoding="utf-8"))
        role = job["role"]
        calls_by_role[role] = calls_by_role.get(role, 0) + 1
        if role == "implementation":
            return {
                "job_id": job_id,
                "run_id": job["run_id"],
                "role": role,
                "adapter": job["adapter"],
                "status": "failed",
                "summary": "transient timeout",
                "failure_class": "timeout",
            }
        return {
            "job_id": job_id,
            "run_id": job["run_id"],
            "role": role,
            "adapter": job["adapter"],
            "status": "completed",
            "summary": f"{role} completed",
        }

    monkeypatch.setattr(
        "goodteam_os.workflow_table.WORKFLOW_NODES",
        MappingProxyType(
            {
                "planner": WorkflowNode(
                    name="planner",
                    run_guard_gate="rfc",
                    runner_fn="run_delivery_until_release_gate",
                    on_error="failure_triage",
                    retry_max=1,
                    retry_on_classes=("timeout",),
                ),
            }
        ),
    )
    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    result = advance_approved_workflow(state=state, workspace=tmp_path, project_config=project_config)

    assert calls_by_role["implementation"] == 2
    assert result.next_node == "failure_triage"
    assert any(
        blocker.startswith("NEEDS HUMAN [timeout]:") and "timeout, 2 attempts" in blocker
        for blocker in result.blockers
    )
    action_log = tmp_path / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl"
    actions = [json.loads(line) for line in action_log.read_text(encoding="utf-8").splitlines()]
    triage = [action for action in actions if action["action"] == "triage_decided"]
    assert triage[-1]["failure_class"] == "timeout"
    assert triage[-1]["decision"] == "escalate_human"
    assert triage[-1]["after_node"] == "failure_triage"


def test_escalated_worker_failure_writes_scoped_failure_memory_event(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow.")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.approvals.update({"prd": "approved", "rfc": "approved"})
    state.current_node = "rfc_review_gate"
    state.next_node = "planner"

    def fake_run_job(self, job_id):
        job = json.loads((self.root / job_id / "job.json").read_text(encoding="utf-8"))
        role = job["role"]
        if role == "implementation":
            return {
                "job_id": job_id,
                "run_id": job["run_id"],
                "role": role,
                "adapter": job["adapter"],
                "status": "failed",
                "summary": "network socket closed during checkout",
                "failure_class": "network",
                "attempts": 2,
            }
        return {
            "job_id": job_id,
            "run_id": job["run_id"],
            "role": role,
            "adapter": job["adapter"],
            "status": "completed",
            "summary": f"{role} completed",
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    result = advance_approved_workflow(state=state, workspace=tmp_path, project_config=project_config)

    assert result.next_node == "blocked"
    assert any(blocker.startswith("NEEDS HUMAN [network]:") for blocker in result.blockers)
    memory_root = tmp_path / "contentops"
    memory_store = RoleMemoryStore(memory_root)
    events = memory_store.list_events("implementation")
    failure_events = [
        event
        for event in events
        if event["body"].startswith("FAILURE [network]") and event["source"].startswith("worker_failure:")
    ]
    assert failure_events
    assert "node=implementation" in failure_events[0]["body"]
    assert "attempts=2" in failure_events[0]["body"]
    assert "disposition=escalate_human" in failure_events[0]["body"]
    packet = memory_store.build_pickup_packet("implementation", query="network socket closed")
    assert "FAILURE [network]" not in str(packet["short_term"])
    assert packet["relevant_events"] == []


def test_non_retryable_worker_failure_does_not_retry(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow.")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.approvals.update({"prd": "approved", "rfc": "approved"})
    state.current_node = "rfc_review_gate"
    state.next_node = "planner"
    calls_by_role: dict[str, int] = {}

    def fake_run_job(self, job_id):
        job = json.loads((self.root / job_id / "job.json").read_text(encoding="utf-8"))
        role = job["role"]
        calls_by_role[role] = calls_by_role.get(role, 0) + 1
        return {
            "job_id": job_id,
            "run_id": job["run_id"],
            "role": role,
            "adapter": job["adapter"],
            "status": "failed" if role == "implementation" else "completed",
            "summary": f"{role} result",
            "failure_class": "scaffold" if role == "implementation" else "",
        }

    monkeypatch.setattr(
        "goodteam_os.workflow_table.WORKFLOW_NODES",
        MappingProxyType(
            {
                "planner": WorkflowNode(
                    name="planner",
                    run_guard_gate="rfc",
                    runner_fn="run_delivery_until_release_gate",
                    retry_max=1,
                    retry_on_classes=("timeout",),
                ),
            }
        ),
    )
    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    result = advance_approved_workflow(state=state, workspace=tmp_path, project_config=project_config)

    assert calls_by_role["implementation"] == 1
    assert result.next_node == "blocked"
    assert any("scaffold" in blocker for blocker in result.blockers)


def test_multi_service_delivery_mode_expands_worker_expected_artifacts(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state(
        "run-1",
        "CONTENTOPS",
        "Build a production-grade AI content platform.",
    )
    state.delivery_mode = "multi_service"
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.approvals.update({"prd": "approved", "rfc": "approved"})
    state.current_node = "rfc_review_gate"
    state.next_node = "planner"
    expected_by_role = {}

    def fake_create_job(self, **kwargs):
        expected_by_role[kwargs["role"]] = kwargs["expected_artifacts"]
        return {"id": f"job-{kwargs['role']}"}

    def fake_run_job(self, job_id):
        role = job_id.removeprefix("job-")
        return {
            "job_id": job_id,
            "run_id": state.run_id,
            "role": role,
            "adapter": "codex",
            "status": "completed",
            "summary": f"{role} completed",
            "artifacts": [],
            "changed_files": [],
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.create_job", fake_create_job)
    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    advance_approved_workflow(state=state, workspace=tmp_path, project_config=project_config)

    assert "services/api" in expected_by_role["implementation"]
    assert "services/web" in expected_by_role["implementation"]
    assert "docker-compose.yml" in expected_by_role["implementation"]
    assert "database migrations" in expected_by_role["implementation"]
    assert "service-level QA notes" in expected_by_role["qa"]


def test_implementation_workers_use_run_role_models(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "Build ContentOps Studio.")
    state.role_models = {
        "ui": {"primary": "codex:gpt-5.5"},
        "frontend": {"primary": "codex:gpt-5.3-codex"},
        "implementation": {"primary": "codex:gpt-5.3-codex"},
        "qa": {"primary": "codex:gpt-5.3-codex"},
    }
    models_by_role = {}

    def fake_create_job(self, **kwargs):
        models_by_role[kwargs["role"]] = kwargs.get("model")
        return {"id": f"job-{kwargs['role']}"}

    def fake_run_job(self, job_id):
        role = job_id.removeprefix("job-")
        return {
            "job_id": job_id,
            "run_id": state.run_id,
            "role": role,
            "adapter": "codex",
            "status": "completed",
            "summary": f"{role} completed",
            "artifacts": [],
            "changed_files": [],
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.create_job", fake_create_job)
    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    run_implementation_workers(state, project_config)

    assert models_by_role == {
        "implementation": "gpt-5.3-codex",
        "ui": "gpt-5.3-codex",
        "qa": "gpt-5.3-codex",
    }


def test_ui_worker_task_requires_direct_frontend_delivery(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "Build a task tracker")
    tasks_by_role = {}

    def fake_create_job(self, **kwargs):
        tasks_by_role[kwargs["role"]] = {
            "task": kwargs["task"],
            "expected_artifacts": kwargs.get("expected_artifacts", []),
        }
        return {"id": f"job-{kwargs['role']}"}

    def fake_run_job(self, job_id):
        role = job_id.removeprefix("job-")
        return {
            "job_id": job_id,
            "run_id": state.run_id,
            "role": role,
            "adapter": "codex",
            "status": "completed",
            "summary": f"{role} completed",
            "artifacts": [],
            "changed_files": [],
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.create_job", fake_create_job)
    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    run_implementation_workers(state, project_config)

    ui_task = tasks_by_role["ui"]["task"]
    qa_task = tasks_by_role["qa"]["task"]
    impl_task = tasks_by_role["implementation"]["task"]
    assert "Build a task tracker" in ui_task
    assert "Build a task tracker" in impl_task
    assert "Build a task tracker" in qa_task
    assert "approved PRD and RFC" in ui_task
    assert "approved PRD and RFC" in impl_task
    assert "screenshots" in ui_task
    assert "visual acceptance" in ui_task
    assert "CONTENTOPS" not in ui_task
    assert "CONTENTOPS" not in impl_task
    assert "CONTENTOPS" not in qa_task
    assert "content studio" not in ui_task.lower()
    assert "content studio" not in impl_task.lower()
    assert "Verify the implemented Build a task tracker" in qa_task
    assert "frontend files" in tasks_by_role["ui"]["expected_artifacts"]
    assert "browser screenshot evidence" in tasks_by_role["ui"]["expected_artifacts"]


def test_delivery_workflow_stops_when_ui_worker_fails(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state(
        "run-1",
        "CONTENTOPS",
        "Build a polished ContentOps Studio interface that can be used directly.",
    )
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.approvals.update({"prd": "approved", "rfc": "approved"})
    state.current_node = "rfc_review_gate"
    state.next_node = "planner"

    def fake_run_job(self, job_id):
        job = json.loads((self.root / job_id / "job.json").read_text(encoding="utf-8"))
        status = "failed" if job["role"] == "ui" else "completed"
        return {
            "job_id": job_id,
            "run_id": job["run_id"],
            "role": job["role"],
            "adapter": job["adapter"],
            "status": status,
            "summary": "Homepage visual delivery failed." if job["role"] == "ui" else f"{job['role']} completed",
            "artifacts": [],
            "changed_files": [],
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    result = advance_approved_workflow(state=state, workspace=tmp_path, project_config=project_config)

    assert result.current_node == "implementation"
    assert result.next_node == "blocked"
    assert any("ui worker failed" in blocker for blocker in result.blockers)
    assert "release_notes" not in result.artifacts


def test_init_project_implementation_task_requests_product_scaffold():
    state = create_initial_state("run-1", "CONTENTOPS", "Build a task tracker")
    task = implementation_task(state, "init_project")

    assert "MVP" not in task
    assert "frontend" in task
    assert "backend" in task
    assert "data layer" in task
    assert "DevOps" in task
    assert "Build a task tracker" in task
    assert "approved PRD and RFC" in task
    assert "CONTENTOPS" not in task


def write_contentops_project_fixture(tmp_path: Path) -> Path:
    project = tmp_path / "contentops"
    docs = project / "docs"
    backend = project / "backend"
    frontend = project / "frontend"
    for repo in [docs, backend, frontend]:
        repo.mkdir(parents=True)

    (docs / "codex.md").write_text(
        "# AI Content Studio Memory\n\n"
        "- Build an AI-focused self-media content production system.\n"
        "- It gathers source material and drafts posts for TikTok, Reddit, X, and LinkedIn.\n",
        encoding="utf-8",
    )
    (backend / "README.md").write_text("# Backend\n\nOwn source ingestion and draft generation.\n", encoding="utf-8")
    (frontend / "README.md").write_text("# Frontend\n\nOwn publishing dashboard UX.\n", encoding="utf-8")

    config = tmp_path / "contentops.local.yaml"
    config.write_text(
        "\n".join(
            [
                "name: AI Content Studio",
                "key: CONTENTOPS",
                "model_profile: balanced",
                "repos:",
                f"  docs: {docs}",
                f"  backend: {backend}",
                f"  frontend: {frontend}",
                "worker_backend:",
                "  mode: codex",
            ]
        ),
        encoding="utf-8",
    )
    return config
