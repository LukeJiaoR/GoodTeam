import json
from pathlib import Path
from types import MappingProxyType

from goodteam_os.graph import (
    accept_refinement,
    advance_approved_workflow,
    collect_project_context,
    MAX_CONTEXT_DOC_FILES,
    single_root_context_files,
    run_fix_until_next_gate,
    delivery_standards_for_mode,
    discard_refinement,
    extract_open_questions,
    generate_rfc,
    generate_prd,
    implementation_worker_job_specs,
    refine_artifact,
    resolve_project_runtime_root,
    resolve_role_adapter,
    render_downstream_artifact_markdown,
    render_prd_markdown,
    run_architecture_until_gate,
    run_implementation_workers,
    run_until_next_gate,
    worker_context_for_role,
)
from goodteam_os.graph import run_adoption_until_next_gate
from goodteam_os.failure_patterns import FailurePatternLibrary
from goodteam_os.memory import RoleMemoryStore
from goodteam_os.messages import append_message
from goodteam_os.state import StateStore, create_initial_state
from goodteam_os.web import decide_approval
from goodteam_os.workflow_table import WorkflowNode


def test_extract_open_questions_from_open_questions_section():
    content = """# PRD

## Open Questions

- Which billing model should v1 use?
* What admin roles are needed?
1. Should imports run async?

## Next Section

- Not an open question.
"""

    assert extract_open_questions(content) == [
        "Which billing model should v1 use?",
        "What admin roles are needed?",
        "Should imports run async?",
    ]


def test_extract_open_questions_from_risks_and_open_questions_section():
    content = """# PRD

## Risks And Open Questions

- Confirm launch geography.
2) Decide retention period.
"""

    assert extract_open_questions(content) == [
        "Confirm launch geography.",
        "Decide retention period.",
    ]


def test_extract_open_questions_from_takeover_brief_questions_for_you_section():
    content = """# Takeover Brief

## Questions for you

- Which customer workflow matters most this quarter?
- Is deployment ownership still shared with the platform team?

## Supporting material

- This is not a question.
"""

    assert extract_open_questions(content) == [
        "Which customer workflow matters most this quarter?",
        "Is deployment ownership still shared with the platform team?",
    ]


def test_extract_open_questions_from_chinese_takeover_brief_question_section():
    content = """# 接管简报

## 需要你确认的问题

- 当前最重要的客户工作流是什么？
"""

    assert extract_open_questions(content) == ["当前最重要的客户工作流是什么？"]


def test_extract_open_questions_returns_empty_without_section():
    assert extract_open_questions("# PRD\n\n## Risks\n\n- Missing vendor decision.\n") == []


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


def test_worker_context_for_role_includes_only_addressed_operator_messages(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Ship billing")
    append_message(
        tmp_path,
        "CONTENTOPS",
        "run-1",
        from_role="human",
        to=["qa"],
        kind="ask",
        body="Focus regression on login and billing.",
        gate="qa",
    )
    append_message(
        tmp_path,
        "CONTENTOPS",
        "run-1",
        from_role="human",
        to=["architect"],
        kind="ask",
        body="Confirm whether SQLite remains acceptable.",
        gate="prd",
    )

    qa_context = worker_context_for_role(state, "qa", workspace=tmp_path)
    architect_context = worker_context_for_role(state, "architect", workspace=tmp_path)

    assert "## Human / Operator Role Messages (Advisory)" in qa_context
    assert "human/operator context only" in qa_context
    assert "do not override the task, constraints, system policy" in qa_context
    assert "Focus regression on login and billing." in qa_context
    assert "Confirm whether SQLite remains acceptable." not in qa_context
    assert "Confirm whether SQLite remains acceptable." in architect_context
    assert "Focus regression on login and billing." not in architect_context


def test_worker_context_for_role_omits_advisory_section_when_no_messages(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Ship billing")

    context = worker_context_for_role(state, "qa", workspace=tmp_path)

    assert "Human / Operator Role Messages" not in context
    assert context == "Run: run-1\nProject: CONTENTOPS\nGoal: Ship billing\nArtifacts:\n- none"


def test_implementation_worker_job_specs_include_qa_role_messages_only(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Ship billing")
    append_message(
        tmp_path,
        "CONTENTOPS",
        "run-1",
        from_role="human",
        to=["qa"],
        kind="propose",
        body="Exercise the payment retry path.",
        gate="qa",
    )
    append_message(
        tmp_path,
        "CONTENTOPS",
        "run-1",
        from_role="human",
        to=["architect"],
        kind="ask",
        body="Should the API be versioned?",
        gate="rfc",
    )

    specs = implementation_worker_job_specs(state, {"worker_backend": {"mode": "human"}}, workspace=tmp_path)
    contexts = {str(spec["role"]): str(spec["context"]) for spec in specs}

    assert "Exercise the payment retry path." in contexts["qa"]
    assert "Should the API be versioned?" not in contexts["qa"]
    assert "Exercise the payment retry path." not in contexts["implementation"]
    assert "Should the API be versioned?" not in contexts["implementation"]


def test_legacy_unmigrated_worker_type_settings_apply_without_duplicating_jobs(tmp_path):
    """Unmigrated/scratch targets keep the legacy role-settings dispatch path.

    Targets without Team Control Plane state (e.g. scratch projects outside
    the standard workspace layout) still dispatch through the legacy
    `worker_type_settings` branch of `_worker_job_fields`. The contract is
    that legacy `desired_instances` never explodes one role into N duplicate
    job specs — not that the workflow always emits exactly this job set.
    """
    state = create_initial_state("run-1", "CONTENTOPS", "Ship billing")
    target = {
        "worker_backend": {"mode": "codex"},
        "workers": {"implementation": "pi"},
        "worker_type_settings": {
            "implementation": {
                "reasoning_depth": "high",
                "additional_prompt": "Keep public APIs stable.",
                "desired_instances": "4",
            }
        },
    }

    specs = implementation_worker_job_specs(state, target, workspace=tmp_path)
    implementation_specs = [spec for spec in specs if spec["role"] == "implementation"]
    assert len(implementation_specs) == 1
    implementation = implementation_specs[0]

    assert implementation["reasoning_depth"] == "high"
    assert implementation["additional_prompt"] == "Keep public APIs stable."
    assert implementation["desired_instances"] == 4
    assert implementation["pi_thinking_level"] == "high"


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
                "worker_backend:",
                "  mode: fake",
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


def test_normal_prd_uses_worker_output_for_real_backend(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow")
    state.role_models["product_requirement"] = {"primary": "codex:gpt-5.5"}
    seen: dict[str, object] = {}

    def fake_create_job(self, **kwargs):
        seen.update(kwargs)
        return {"id": "job-1", **kwargs}

    def fake_run_job(self, job_id):
        job_root = self.root / job_id
        job_root.mkdir(parents=True, exist_ok=True)
        result_path = job_root / "result.md"
        result_path.write_text("# Worker PRD\n\nGenerated by real backend.\n", encoding="utf-8")
        return {
            "job_id": job_id,
            "run_id": state.run_id,
            "role": "product_requirement",
            "status": "completed",
            "summary": "completed",
            "result_path": str(result_path),
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.create_job", fake_create_job)
    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    result = run_until_next_gate(state=state, workspace=tmp_path, project_config=project_config)

    assert seen["role"] == "product_requirement"
    assert seen["adapter"] == "codex"
    assert seen["model"] == "gpt-5.5"
    assert Path(result.artifacts["prd"]).read_text(encoding="utf-8") == "# Worker PRD\n\nGenerated by real backend.\n"


def test_prd_changes_requested_revises_with_feedback_and_returns_to_gate(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    prd = tmp_path / "artifacts" / "runs" / "run-1" / "prd" / "prd.md"
    prd.parent.mkdir(parents=True, exist_ok=True)
    prd.write_text("# Old PRD\n", encoding="utf-8")
    state.artifacts["prd"] = str(prd)
    StateStore(tmp_path).write_project_current(state)
    seen: dict[str, object] = {}

    def fake_create_job(self, **kwargs):
        seen.update(kwargs)
        return {"id": "job-1", **kwargs}

    def fake_run_job(self, job_id):
        job_root = self.root / job_id
        job_root.mkdir(parents=True, exist_ok=True)
        result_path = job_root / "result.md"
        result_path.write_text("# Revised PRD\n\nFeedback addressed.\n", encoding="utf-8")
        return {
            "job_id": job_id,
            "run_id": state.run_id,
            "role": "product_requirement",
            "status": "completed",
            "summary": "completed",
            "result_path": str(result_path),
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.create_job", fake_create_job)
    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="prd",
        decision="changes_requested",
        note="Tighten acceptance criteria around exports.",
    )

    assert payload["next_node"] == "await_prd_approval"
    assert payload["approvals"]["prd"] == "pending"
    assert "Tighten acceptance criteria around exports." in seen["task"]
    assert Path(payload["artifacts"]["prd"]).read_text(encoding="utf-8") == "# Revised PRD\n\nFeedback addressed.\n"
    assert "prd" not in payload["change_requests"]
    actions = [
        json.loads(line)
        for line in (tmp_path / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(action["action"] == "prd_revised" for action in actions)


def test_prd_changes_requested_preserves_feedback_when_worker_fails(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.current_node = "prd_review_gate"
    state.next_node = "await_prd_approval"
    prd = tmp_path / "artifacts" / "runs" / "run-1" / "prd" / "prd.md"
    prd.parent.mkdir(parents=True, exist_ok=True)
    prd.write_text("# Old PRD\n", encoding="utf-8")
    state.artifacts["prd"] = str(prd)
    StateStore(tmp_path).write_project_current(state)
    seen: dict[str, object] = {}

    def fail_worker(**kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr("goodteam_os.graph.generate_artifact_via_worker", fail_worker)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="prd",
        decision="changes_requested",
        note="Keep the export scope narrow.",
    )

    assert payload["next_node"] == "await_prd_approval"
    assert payload["approvals"]["prd"] == "pending"
    assert payload["change_requests"]["prd"] == "Keep the export scope narrow."
    assert "Keep the export scope narrow." in seen["task_text"]
    assert "# Product Roadmap PRD" in Path(payload["artifacts"]["prd"]).read_text(encoding="utf-8")


def test_normal_prd_falls_back_to_template_when_worker_fails(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow")

    monkeypatch.setattr(
        "goodteam_os.graph.WorkerJobStore.run_job",
        lambda self, job_id: {
            "job_id": job_id,
            "run_id": state.run_id,
            "role": "product_requirement",
            "status": "failed",
            "summary": "CLI failed",
            "failure_class": "timeout",
        },
    )

    result = run_until_next_gate(state=state, workspace=tmp_path, project_config=project_config)
    prd = Path(result.artifacts["prd"]).read_text(encoding="utf-8")

    assert "# Product Roadmap PRD" in prd
    assert "Generated by real backend" not in prd
    assert result.current_node == "prd_review_gate"
    assert result.next_node == "await_prd_approval"


def test_normal_rfc_uses_worker_output_for_real_backend(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow")
    state.role_models["architect"] = {"primary": "codex:gpt-5.5"}
    prd = tmp_path / "artifacts" / "runs" / "run-1" / "prd" / "prd.md"
    prd.parent.mkdir(parents=True, exist_ok=True)
    prd.write_text("# Approved PRD\n", encoding="utf-8")
    state.artifacts["prd"] = str(prd)
    seen: dict[str, object] = {}

    def fake_create_job(self, **kwargs):
        seen.update(kwargs)
        return {"id": "job-1", **kwargs}

    def fake_run_job(self, job_id):
        job_root = self.root / job_id
        job_root.mkdir(parents=True, exist_ok=True)
        result_path = job_root / "result.md"
        result_path.write_text("# Worker RFC\n\nGenerated by real backend.\n", encoding="utf-8")
        return {
            "job_id": job_id,
            "run_id": state.run_id,
            "role": "architect",
            "status": "completed",
            "summary": "completed",
            "result_path": str(result_path),
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.create_job", fake_create_job)
    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    result = run_architecture_until_gate(state=state, workspace=tmp_path, project_config=project_config)

    assert seen["role"] == "architect"
    assert seen["adapter"] == "codex"
    assert seen["model"] == "gpt-5.5"
    assert Path(result.artifacts["rfc"]).read_text(encoding="utf-8") == "# Worker RFC\n\nGenerated by real backend.\n"


def test_rfc_changes_requested_revises_with_feedback_and_returns_to_gate(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.current_node = "rfc_review_gate"
    state.next_node = "await_rfc_approval"
    rfc = tmp_path / "artifacts" / "runs" / "run-1" / "rfc" / "rfc.md"
    rfc.parent.mkdir(parents=True, exist_ok=True)
    rfc.write_text("# Old RFC\n", encoding="utf-8")
    state.artifacts["rfc"] = str(rfc)
    StateStore(tmp_path).write_project_current(state)
    seen: dict[str, object] = {}

    def fake_create_job(self, **kwargs):
        seen.update(kwargs)
        return {"id": "job-1", **kwargs}

    def fake_run_job(self, job_id):
        job_root = self.root / job_id
        job_root.mkdir(parents=True, exist_ok=True)
        result_path = job_root / "result.md"
        result_path.write_text("# Revised RFC\n\nFeedback addressed.\n", encoding="utf-8")
        return {
            "job_id": job_id,
            "run_id": state.run_id,
            "role": "architect",
            "status": "completed",
            "summary": "completed",
            "result_path": str(result_path),
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.create_job", fake_create_job)
    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="rfc",
        decision="changes_requested",
        note="Clarify rollback for schema migration.",
    )

    assert payload["next_node"] == "await_rfc_approval"
    assert payload["approvals"]["rfc"] == "pending"
    assert "Clarify rollback for schema migration." in seen["task"]
    assert Path(payload["artifacts"]["rfc"]).read_text(encoding="utf-8") == "# Revised RFC\n\nFeedback addressed.\n"
    assert "rfc" not in payload["change_requests"]
    actions = [
        json.loads(line)
        for line in (tmp_path / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(action["action"] == "rfc_revised" for action in actions)


def test_rfc_changes_requested_note_dispatches_worker(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.current_node = "rfc_review_gate"
    state.next_node = "await_rfc_approval"
    rfc = tmp_path / "artifacts" / "runs" / "run-1" / "rfc" / "rfc.md"
    rfc.parent.mkdir(parents=True, exist_ok=True)
    rfc.write_text("# Old RFC\n", encoding="utf-8")
    state.artifacts["rfc"] = str(rfc)
    StateStore(tmp_path).write_project_current(state)
    seen: dict[str, object] = {}

    def fake_create_job(self, **kwargs):
        seen.update(kwargs)
        return {"id": "job-1", **kwargs}

    def fake_run_job(self, job_id):
        job_root = self.root / job_id
        job_root.mkdir(parents=True, exist_ok=True)
        result_path = job_root / "result.md"
        result_path.write_text("# Revised RFC\n\nBlank-note worker revision.\n", encoding="utf-8")
        return {
            "job_id": job_id,
            "run_id": state.run_id,
            "role": "architect",
            "status": "completed",
            "summary": "completed",
            "result_path": str(result_path),
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.create_job", fake_create_job)
    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="rfc",
        decision="changes_requested",
        note="Clarify the service boundaries.",
    )

    assert seen["role"] == "architect"
    assert "Clarify the service boundaries." in seen["task"]
    assert payload["next_node"] == "await_rfc_approval"
    assert payload["approvals"]["rfc"] == "pending"
    assert "rfc" not in payload["change_requests"]
    assert Path(payload["artifacts"]["rfc"]).read_text(encoding="utf-8") == "# Revised RFC\n\nBlank-note worker revision.\n"


def test_normal_rfc_falls_back_to_template_when_worker_fails(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow")

    monkeypatch.setattr(
        "goodteam_os.graph.WorkerJobStore.run_job",
        lambda self, job_id: {
            "job_id": job_id,
            "run_id": state.run_id,
            "role": "architect",
            "status": "failed",
            "summary": "CLI failed",
            "failure_class": "timeout",
        },
    )

    result = run_architecture_until_gate(state=state, workspace=tmp_path, project_config=project_config)
    rfc = Path(result.artifacts["rfc"]).read_text(encoding="utf-8")

    assert "# Technical RFC" in rfc
    assert "Generated by real backend" not in rfc
    assert result.current_node == "rfc_review_gate"
    assert result.next_node == "await_rfc_approval"


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


def test_generate_prd_resolves_referenced_file_from_worker_summary(tmp_path, monkeypatch):
    project = tmp_path / "contentops"
    docs = project / "docs"
    docs.mkdir(parents=True)
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
                f"  docs: {docs}",
            ]
        ),
        encoding="utf-8",
    )
    # The worker wrote the actual substantive PRD to docs/prd.md
    substantive_prd = docs / "prd.md"
    substantive_prd.write_text("# Comprehensive PRD Document\n\nFull detailed specifications.\n", encoding="utf-8")

    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.role_models["product_requirement"] = {"primary": "codex:gpt-5.5"}
    from goodteam_os.state import StateStore

    StateStore(tmp_path).write_project_current(state)

    def fake_run_job(self, job_id):
        job_root = self.root / job_id
        job_root.mkdir(parents=True, exist_ok=True)
        result_path = job_root / "result.md"
        # Worker result.md only contains summary log output
        result_path.write_text("**Changed Files**\n- `prd.md`\n\n**Verification**\n- test\n", encoding="utf-8")
        return {
            "job_id": job_id,
            "run_id": "run-1",
            "role": "product_requirement",
            "status": "completed",
            "summary": "completed",
            "result_path": str(result_path),
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.create_job", lambda self, **kw: {"id": "job-1", **kw})
    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    payload = generate_prd(workspace=tmp_path, project_key="CONTENTOPS")
    assert Path(payload["artifacts"]["prd"]).read_text(encoding="utf-8") == "# Comprehensive PRD Document\n\nFull detailed specifications.\n"


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


def test_refine_artifact_proposes_without_clobbering_and_accept_promotes(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    prd = tmp_path / "artifacts" / "runs" / "run-1" / "prd" / "prd.md"
    prd.parent.mkdir(parents=True, exist_ok=True)
    prd.write_text("# Old PRD\n", encoding="utf-8")
    state.artifacts["prd"] = str(prd)
    state.approvals["prd"] = "changes_requested"
    StateStore(tmp_path).write_project_current(state)
    seen: dict[str, object] = {}

    def fake_worker(**kwargs):
        seen.update(kwargs)
        return "# Edited PRD\n\n- Sharper acceptance criteria\n"

    monkeypatch.setattr("goodteam_os.graph.generate_artifact_via_worker", fake_worker)

    result = refine_artifact(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        artifact_key="prd",
        content="# Edited PRD\n",
    )

    assert prd.read_text(encoding="utf-8") == "# Edited PRD\n"
    proposed = Path(result["proposed_path"])
    assert proposed.read_text(encoding="utf-8") == "# Edited PRD\n\n- Sharper acceptance criteria\n"
    assert seen["role"] == "product_requirement"
    assert "DEEPEN and refine it" in seen["task_text"]
    assert "# Edited PRD" in seen["task_text"]
    assert result["diff_summary"]["added_lines"] == 2
    current = StateStore(tmp_path).read_project_current("CONTENTOPS")
    assert current is not None
    assert current.artifacts["prd_proposed"] == str(proposed)

    accepted = accept_refinement(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        artifact_key="prd",
    )

    assert accepted["accepted"] is True
    assert prd.read_text(encoding="utf-8") == "# Edited PRD\n\n- Sharper acceptance criteria\n"
    assert not proposed.exists()
    current = StateStore(tmp_path).read_project_current("CONTENTOPS")
    assert current is not None
    assert "prd_proposed" not in current.artifacts
    assert current.approvals["prd"] == "pending"
    assert current.current_node == "prd_review_gate"
    assert current.next_node == "await_prd_approval"

    approved = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="prd",
        decision="approved",
        note="Model-free acceptance: continue with simulated evidence to verify the workflow contract.",
    )

    assert approved["approvals"]["prd"] == "approved"
    # After accept resets the PRD gate to pending and re-approval runs the
    # architect stage, the run advances to the RFC gate awaiting approval.
    assert approved["next_node"] == "await_rfc_approval"


def test_refine_artifact_guidance_builds_targeted_revision_task(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    prd = tmp_path / "artifacts" / "runs" / "run-1" / "prd" / "prd.md"
    prd.parent.mkdir(parents=True, exist_ok=True)
    prd.write_text("# Old PRD\n", encoding="utf-8")
    state.artifacts["prd"] = str(prd)
    StateStore(tmp_path).write_project_current(state)
    seen: dict[str, object] = {}

    def fake_worker(**kwargs):
        seen.update(kwargs)
        return str(kwargs["task_text"])

    monkeypatch.setattr("goodteam_os.graph.generate_artifact_via_worker", fake_worker)

    result = refine_artifact(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        artifact_key="prd",
        content="# Edited PRD\n\n## Open Questions\n\n- Which market launches first?\n",
        guidance="Q: Which market launches first?\nA: Launch in the US first.\n\n",
    )

    assert "Revise the PRD to incorporate the following answers" in seen["task_text"]
    assert "RESOLVING the corresponding open questions" in seen["task_text"]
    assert "Launch in the US first." in seen["task_text"]
    # minimal, scoped edit so per-question apply yields a small diff
    assert "SMALLEST possible edit" in seen["task_text"]
    assert "VERBATIM" in seen["task_text"]
    assert "DEEPEN and refine it" not in seen["task_text"]
    assert "Launch in the US first." in Path(result["proposed_path"]).read_text(encoding="utf-8")


def test_accept_refinement_requires_reapproval_after_prior_approval(tmp_path):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.approvals["prd"] = "approved"
    prd = tmp_path / "artifacts" / "runs" / "run-1" / "prd" / "prd.md"
    proposed = prd.with_name("prd.proposed.md")
    prd.parent.mkdir(parents=True, exist_ok=True)
    prd.write_text("# Approved PRD\n", encoding="utf-8")
    proposed.write_text("# Refined PRD\n", encoding="utf-8")
    state.artifacts.update({"prd": str(prd), "prd_proposed": str(proposed)})
    StateStore(tmp_path).write_project_current(state)

    accepted = accept_refinement(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        artifact_key="prd",
    )

    assert accepted["approvals"]["prd"] == "pending"
    assert accepted["next_node"] == "await_prd_approval"


def test_discard_refinement_preserves_proposal_when_project_config_is_unresolvable(tmp_path):
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow")
    state.source_type = "existing_project"
    state.source_ref = str(tmp_path / "missing-project.local.yaml")
    proposed = tmp_path / "artifacts" / "runs" / "run-1" / "prd" / "prd.proposed.md"
    proposed.parent.mkdir(parents=True, exist_ok=True)
    proposed.write_text("# Proposed PRD\n", encoding="utf-8")
    state.artifacts["prd_proposed"] = str(proposed)
    StateStore(tmp_path).write_project_current(state)

    try:
        discard_refinement(
            workspace=tmp_path,
            project_key="CONTENTOPS",
            artifact_key="prd",
        )
    except ValueError as exc:
        assert str(exc) == "Project config not found for project: CONTENTOPS"
    else:
        raise AssertionError("Expected project config resolution to fail")

    current = StateStore(tmp_path).read_project_current("CONTENTOPS")
    assert current is not None
    assert current.artifacts["prd_proposed"] == str(proposed)
    assert proposed.read_text(encoding="utf-8") == "# Proposed PRD\n"


def test_refine_artifact_keeps_human_edit_when_worker_fails(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    prd = tmp_path / "artifacts" / "runs" / "run-1" / "prd" / "prd.md"
    prd.parent.mkdir(parents=True, exist_ok=True)
    prd.write_text("# Old PRD\n", encoding="utf-8")
    state.artifacts["prd"] = str(prd)
    StateStore(tmp_path).write_project_current(state)

    def fail_worker(**kwargs):
        kwargs["failure_status"].update({"summary": "worker timed out", "failure_class": "timeout"})
        return None

    monkeypatch.setattr("goodteam_os.graph.generate_artifact_via_worker", fail_worker)

    try:
        refine_artifact(
            workspace=tmp_path,
            project_key="CONTENTOPS",
            artifact_key="prd",
            content="# Human Edit\n",
        )
    except RuntimeError as exc:
        assert "[timeout]" in str(exc)
    else:
        raise AssertionError("Expected refine_artifact failure")

    assert prd.read_text(encoding="utf-8") == "# Human Edit\n"
    current = StateStore(tmp_path).read_project_current("CONTENTOPS")
    assert current is not None
    assert "prd_proposed" not in current.artifacts


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


def test_takeover_keeps_ai_context_separate_from_human_brief(tmp_path):
    project_config = write_project_fixture(tmp_path)
    state = create_initial_state("run-1", "SAMPLE", "Take over the configured product")

    result = run_adoption_until_next_gate(state=state, workspace=tmp_path, project_config=project_config)
    context = Path(result.artifacts["takeover_context"]).read_text(encoding="utf-8")
    brief = Path(result.artifacts["takeover"]).read_text(encoding="utf-8")

    assert "docs/codex.md" in context
    assert "docs/codex.md" not in brief
    assert "# Takeover Brief" in brief
    assert "## Questions for you" in brief
    assert "Write a decision-ready PRD, not a template." not in context
    assert "## Multi-Role PRD Review" not in context


def test_adoption_uses_configured_worker_for_human_takeover_brief(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "接管内容工作流")
    state.role_models["project_intake"] = {"primary": "codex:gpt-5.5"}
    seen: dict[str, object] = {}

    def fake_create_job(self, **kwargs):
        seen.update(kwargs)
        return {"id": "job-1", **kwargs}

    def fake_run_job(self, job_id):
        job_root = self.root / job_id
        job_root.mkdir(parents=True, exist_ok=True)
        result_path = job_root / "result.md"
        result_path.write_text(
            "# Takeover Brief\n\n## Key findings\n\n- 当前 Worker 已经能产出可读判断。\n\n"
            "## Questions for you\n\n- 这个季度最重要的目标是什么？\n",
            encoding="utf-8",
        )
        return {
            "job_id": job_id,
            "run_id": state.run_id,
            "role": "project_intake",
            "status": "completed",
            "summary": "completed",
            "result_path": str(result_path),
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.create_job", fake_create_job)
    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    result = run_adoption_until_next_gate(state=state, workspace=tmp_path, project_config=project_config)
    brief = Path(result.artifacts["takeover"]).read_text(encoding="utf-8")

    assert seen["role"] == "project_intake"
    assert seen["adapter"] == "codex"
    assert seen["model"] == "gpt-5.5"
    assert "human review document" in str(seen["task"])
    assert result.artifacts["takeover_context"] in str(seen["context"])
    assert brief.startswith("# Takeover Brief")
    assert "当前 Worker 已经能产出可读判断" in brief
    assert result.artifacts["takeover_context"] != result.artifacts["takeover"]


def test_takeover_changes_requested_regenerates_human_brief_and_reopens_review(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "接管内容工作流")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.current_node = "takeover_review_gate"
    state.next_node = "await_takeover_approval"
    state.approvals["takeover"] = "pending"
    legacy_context = tmp_path / "artifacts" / "runs" / "run-1" / "takeover" / "takeover-context.md"
    legacy_context.parent.mkdir(parents=True, exist_ok=True)
    legacy_context.write_text("# Internal source packet\n\n/path/for/ai-only-use\n", encoding="utf-8")
    state.artifacts["takeover"] = str(legacy_context)
    StateStore(tmp_path).write_project_current(state)
    seen: dict[str, object] = {}

    def fake_create_job(self, **kwargs):
        seen.update(kwargs)
        return {"id": "job-1", **kwargs}

    def fake_run_job(self, job_id):
        job_root = self.root / job_id
        job_root.mkdir(parents=True, exist_ok=True)
        result_path = job_root / "result.md"
        result_path.write_text(
            "# Takeover Brief\n\n## Key findings\n\n- 已根据反馈重新检查了项目。\n\n"
            "## Questions for you\n\n- 这个季度最重要的目标是什么？\n",
            encoding="utf-8",
        )
        return {
            "job_id": job_id,
            "run_id": state.run_id,
            "role": "project_intake",
            "status": "completed",
            "summary": "completed",
            "result_path": str(result_path),
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.create_job", fake_create_job)
    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="takeover",
        decision="changes_requested",
        note="请说明当前最重要的风险。",
    )

    assert payload["current_node"] == "takeover_review_gate"
    assert payload["next_node"] == "await_takeover_approval"
    assert payload["approvals"]["takeover"] == "pending"
    assert "请说明当前最重要的风险。" in str(seen["task"])
    assert "takeover" not in payload["change_requests"]
    revised_brief = Path(payload["artifacts"]["takeover"])
    assert revised_brief.name == "takeover-brief.md"
    assert "# Takeover Brief" in revised_brief.read_text(encoding="utf-8")
    actions = [
        json.loads(line)
        for line in (tmp_path / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(action["action"] == "takeover_revised" for action in actions)


def test_takeover_changes_requested_keeps_visible_feedback_when_intake_worker_fails(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "接管内容工作流")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.current_node = "takeover_review_gate"
    state.next_node = "await_takeover_approval"
    state.approvals["takeover"] = "pending"
    StateStore(tmp_path).write_project_current(state)

    monkeypatch.setattr("goodteam_os.graph.generate_artifact_via_worker", lambda **kwargs: None)

    payload = decide_approval(
        workspace=tmp_path,
        project_key="CONTENTOPS",
        approval="takeover",
        decision="changes_requested",
        note="请说明当前最重要的风险。",
    )

    assert payload["next_node"] == "await_takeover_approval"
    assert payload["approvals"]["takeover"] == "pending"
    assert payload["change_requests"]["takeover"] == "请说明当前最重要的风险。"
    fallback = Path(payload["artifacts"]["takeover"]).read_text(encoding="utf-8")
    assert "## Requested changes" in fallback
    assert "请说明当前最重要的风险。" in fallback


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


def test_collect_project_context_discovers_single_root_context(tmp_path):
    project = tmp_path / "contentops"
    docs = project / "docs"
    docs.mkdir(parents=True)
    (project / "README.md").write_text("# ContentOps\n\nRoot overview.", encoding="utf-8")
    (project / "package.json").write_text('{"scripts":{"test":"vitest"}}\n', encoding="utf-8")
    (docs / "architecture.md").write_text("# Architecture\n\nSingle service starter.", encoding="utf-8")
    config = tmp_path / "contentops.local.yaml"
    config.write_text(
        "\n".join(
            [
                "name: ContentOps",
                "key: CONTENTOPS",
                "repos:",
                f"  root: {project}",
                "worker_backend:",
                "  mode: fake",
            ]
        ),
        encoding="utf-8",
    )

    context = collect_project_context(config)
    paths = [item["path"] for item in context]

    assert str((project / "README.md").resolve()) in paths
    assert str((project / "package.json").resolve()) in paths
    assert str((docs / "architecture.md").resolve()) in paths
    assert any(item["purpose"] == "Shallow project directory overview" for item in context)


def test_single_root_context_excludes_generated_takeover_docs_and_includes_delivery_files(tmp_path):
    project = tmp_path / "delivery-context"
    docs = project / "docs"
    workflows = project / ".github" / "workflows"
    app = project / "apps" / "server"
    docs.mkdir(parents=True)
    workflows.mkdir(parents=True)
    app.mkdir(parents=True)
    (docs / "takeover.md").write_text("# stale generated takeover\n", encoding="utf-8")
    (docs / "takeover-context.md").write_text("# stale generated context\n", encoding="utf-8")
    (docs / "deployment.md").write_text("# Deployment\n\nProduction notes.\n", encoding="utf-8")
    (workflows / "ci.yml").write_text("name: CI\n", encoding="utf-8")
    (project / "docker-compose.prod.yml").write_text("services: {}\n", encoding="utf-8")
    (app / "Dockerfile").write_text("FROM node:22\n", encoding="utf-8")

    files = single_root_context_files(project)
    names = [path.name for path, _ in files]
    purposes = {path.name: purpose for path, purpose in files}

    assert "takeover.md" not in names
    assert "takeover-context.md" not in names
    assert purposes["ci.yml"] == "CI workflow"
    assert purposes["docker-compose.prod.yml"] == "Deployment manifest"
    assert purposes["Dockerfile"] == "Container build definition"


def _write_single_root_config(tmp_path: Path, project: Path) -> Path:
    config = tmp_path / "single.local.yaml"
    config.write_text(
        "\n".join(
            [
                "name: Single Root",
                "key: SINGLE",
                "repos:",
                f"  root: {project}",
                "worker_backend:",
                "  mode: fake",
            ]
        ),
        encoding="utf-8",
    )
    return config


def test_single_root_context_lifts_twenty_doc_cap(tmp_path):
    project = tmp_path / "docheavy"
    docs = project / "docs"
    docs.mkdir(parents=True)
    for index in range(35):
        (docs / f"doc-{index:03d}.md").write_text(f"# Doc {index}\n\nbody\n", encoding="utf-8")

    files = single_root_context_files(project)
    doc_entries = [path for path, purpose in files if purpose == "Project documentation"]

    assert len(doc_entries) > 20
    assert len(doc_entries) <= MAX_CONTEXT_DOC_FILES
    # Deterministic alphabetical-by-relative-path selection is preserved.
    names = [path.name for path in doc_entries]
    assert names == sorted(names)


def test_single_root_context_byte_budget_stops_accumulation(tmp_path):
    project = tmp_path / "bigdocs"
    docs = project / "docs"
    docs.mkdir(parents=True)
    # Each doc is ~1.2MB; the 2MB budget should admit roughly the first two.
    blob = "x" * 1_200_000
    for index in range(6):
        (docs / f"doc-{index:02d}.md").write_text(blob, encoding="utf-8")

    files = single_root_context_files(project)
    doc_entries = [path for path, purpose in files if purpose == "Project documentation"]

    assert 0 < len(doc_entries) < 6


def test_single_root_context_loads_root_instruction_files_before_docs(tmp_path):
    project = tmp_path / "withclaude"
    docs = project / "docs"
    docs.mkdir(parents=True)
    (project / "CLAUDE.md").write_text("# CLAUDE\n\nagent guidance\n", encoding="utf-8")
    (docs / "design.md").write_text("# Design\n\nbody\n", encoding="utf-8")

    files = single_root_context_files(project)
    purposes = [purpose for _, purpose in files]
    names = [path.name for path, _ in files]

    assert "CLAUDE.md" in names
    claude_index = next(i for i, (p, _) in enumerate(files) if p.name == "CLAUDE.md")
    first_doc_index = purposes.index("Project documentation")
    assert claude_index < first_doc_index
    assert files[claude_index][1] == "Project instructions / agent guidance"


def test_collect_project_context_includes_root_claude_md(tmp_path):
    project = tmp_path / "withclaude"
    docs = project / "docs"
    docs.mkdir(parents=True)
    (project / "CLAUDE.md").write_text("# CLAUDE\n\nProject instructions live here.\n", encoding="utf-8")
    (docs / "design.md").write_text("# Design\n\nbody\n", encoding="utf-8")
    config = _write_single_root_config(tmp_path, project)

    context = collect_project_context(config)
    paths = [item["path"] for item in context]

    assert str((project / "CLAUDE.md").resolve()) in paths
    claude_index = paths.index(str((project / "CLAUDE.md").resolve()))
    doc_index = paths.index(str((docs / "design.md").resolve()))
    assert claude_index < doc_index


def test_run_fix_until_next_gate_loads_project_context(tmp_path):
    project = tmp_path / "withclaude"
    docs = project / "docs"
    docs.mkdir(parents=True)
    (project / "CLAUDE.md").write_text("# CLAUDE\n\nProject instructions live here.\n", encoding="utf-8")

    config = _write_single_root_config(tmp_path, project)
    state = create_initial_state("fix-1", "SINGLE", "Button click does nothing")

    result = run_fix_until_next_gate(state=state, workspace=tmp_path, project_config=config)
    fix_plan = Path(result.artifacts["fix_plan"]).read_text(encoding="utf-8")

    assert "## Project Context Read" in fix_plan
    assert "CLAUDE.md" in fix_plan


def test_run_until_next_gate_mirrors_artifact_to_single_root_docs(tmp_path):
    project = tmp_path / "contentops"
    project.mkdir()
    config = tmp_path / "contentops.local.yaml"
    config.write_text(
        "\n".join(
            [
                "name: ContentOps",
                "key: CONTENTOPS",
                "repos:",
                f"  root: {project}",
                "worker_backend:",
                "  mode: fake",
            ]
        ),
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow")

    result = run_until_next_gate(state=state, workspace=tmp_path, project_config=config)

    assert Path(result.artifacts["prd"]).is_file()
    mirror = project / "docs" / "prd.md"
    assert mirror.is_file()
    assert mirror.read_text(encoding="utf-8") == Path(result.artifacts["prd"]).read_text(encoding="utf-8")


def test_resolve_project_runtime_root_prefers_parent_project_directory(tmp_path):
    project = tmp_path / "product"
    docs = project / "docs"
    backend = project / "api"
    frontend = project / "ui"
    for path in [docs, backend, frontend]:
        path.mkdir(parents=True)

    root = resolve_project_runtime_root({"docs": str(docs), "backend": str(backend), "frontend": str(frontend)})

    assert root == project


def test_resolve_project_runtime_root_accepts_single_named_root(tmp_path):
    project = tmp_path / "product"
    project.mkdir()

    root = resolve_project_runtime_root({"root": str(project)})

    assert root == project


def test_delivery_standards_for_mode_uses_custom_project_config():
    target = {
        "delivery_standards": {
            "standard_0002": "Include rollback notes.",
            "standard_0001": "Run the local smoke path.",
        }
    }

    assert delivery_standards_for_mode("custom", target) == [
        "Run the local smoke path.",
        "Include rollback notes.",
    ]


def test_transient_custom_delivery_standards_override_config_in_prompts(tmp_path):
    project = tmp_path / "product"
    project.mkdir()
    config = tmp_path / "project.local.yaml"
    config.write_text(
        "\n".join(
            [
                "name: Custom Product",
                "key: CUSTOM",
                "delivery_mode: custom",
                "delivery_standards:",
                "  standard_0001: Config checklist.",
                "repos:",
                f"  root: {project}",
            ]
        ),
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CUSTOM", "Build custom delivery")
    state.delivery_mode = "custom"
    state.delivery_standards = ["Temporary PRD standard.", "Temporary implementation standard."]

    prd = render_prd_markdown(state, config)
    rfc = render_downstream_artifact_markdown("rfc", state, config)
    implementation = render_downstream_artifact_markdown("implementation_handoff", state, config)

    for prompt in [prd, rfc, implementation]:
        assert "- Temporary PRD standard." in prompt
        assert "- Temporary implementation standard." in prompt
        assert "- Config checklist." not in prompt


def test_custom_delivery_standards_fall_back_to_config_without_transient_state(tmp_path):
    project = tmp_path / "product"
    project.mkdir()
    config = tmp_path / "project.local.yaml"
    config.write_text(
        "\n".join(
            [
                "name: Custom Product",
                "key: CUSTOM",
                "delivery_mode: custom",
                "delivery_standards:",
                "  standard_0001: Config checklist.",
                "repos:",
                f"  root: {project}",
            ]
        ),
        encoding="utf-8",
    )
    state = create_initial_state("run-1", "CUSTOM", "Build custom delivery")
    state.delivery_mode = "custom"

    implementation = render_downstream_artifact_markdown("implementation_handoff", state, config)

    assert "- Config checklist." in implementation


def test_advance_approved_workflow_waits_for_security_gate_after_rfc(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow.")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.approvals.update({"prd": "approved", "rfc": "approved", "security": "pending"})
    state.current_node = "rfc_review_gate"
    state.next_node = "security"

    def fail_if_workers_run(self, job_id):
        raise AssertionError("planner workers must not run before security approval")

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fail_if_workers_run)

    result = advance_approved_workflow(state=state, workspace=tmp_path, project_config=project_config)

    assert result.current_node == "security_review_gate"
    assert result.next_node == "await_security_approval"
    assert "implementation_handoff" not in result.artifacts


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
    state.approvals.update({"prd": "approved", "rfc": "approved", "security": "approved"})
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
    state.approvals.update({"prd": "approved", "rfc": "approved", "security": "approved"})
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
    state.approvals.update({"prd": "approved", "rfc": "approved", "security": "approved"})
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
    state.approvals.update({"prd": "approved", "rfc": "approved", "security": "approved"})
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
    state.approvals.update({"prd": "approved", "rfc": "approved", "security": "approved"})
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
    state.approvals.update({"prd": "approved", "rfc": "approved", "security": "approved"})
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
    state.approvals.update({"prd": "approved", "rfc": "approved", "security": "approved"})
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
    events = memory_store.list_events("implementation", include_execution=True)
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

    patterns = FailurePatternLibrary(tmp_path).list_patterns()
    assert len(patterns) == 1
    assert patterns[0]["classifier_hint"] == "network"
    assert patterns[0]["recommended_disposition"] == "escalate_human"
    assert patterns[0]["hit_count"] == 1
    assert patterns[0]["roles"] == ["implementation"]
    assert patterns[0]["sources"] == ["worker_failure:run-1:implementation:implementation"]
    assert patterns[0]["routing_outcomes"] == {
        "total": 0,
        "confirmed": 0,
        "false_positive": 0,
        "shadow_only": 0,
        "last_outcome": "",
        "last_reason": "",
        "last_disposition": "",
        "last_deterministic_disposition": "",
    }


def test_worker_failure_triage_records_failure_pattern_match_without_overriding_disposition(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    FailurePatternLibrary(tmp_path).record_failure(
        failure_class="scaffold",
        summary="Cannot find module better-sqlite3 after npm install",
        disposition="escalate_human",
        role="implementation",
        node="implementation",
        source="worker_failure:prior:implementation:implementation",
    )
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow.")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.approvals.update({"prd": "approved", "rfc": "approved", "security": "approved"})
    state.current_node = "rfc_review_gate"
    state.next_node = "planner"

    def fake_run_job(self, job_id):
        job = json.loads((self.root / job_id / "job.json").read_text(encoding="utf-8"))
        role = job["role"]
        return {
            "job_id": job_id,
            "run_id": job["run_id"],
            "role": role,
            "adapter": job["adapter"],
            "status": "failed" if role == "implementation" else "completed",
            "summary": "native module better-sqlite3 missing after install",
            "failure_class": "unknown" if role == "implementation" else "",
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    result = advance_approved_workflow(state=state, workspace=tmp_path, project_config=project_config)

    assert result.next_node == "blocked"
    action_log = tmp_path / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl"
    actions = [json.loads(line) for line in action_log.read_text(encoding="utf-8").splitlines()]
    triage = [action for action in actions if action["action"] == "triage_decided"]
    assert triage[-1]["decision"] == "block"
    assert triage[-1]["failure_pattern_signature"].startswith("fail:")
    assert triage[-1]["failure_pattern_hits"] == "1"
    assert triage[-1]["failure_pattern_disposition"] == "escalate_human"
    assert triage[-1]["would_route_to"] == "escalate_human"
    assert triage[-1]["shadow_route_source"] == "failure_pattern"
    assert triage[-1]["shadow_route_hit_count"] == "1"
    assert triage[-1]["deterministic_disposition"] == "block"
    patterns = FailurePatternLibrary(tmp_path).list_patterns()
    routed_pattern = next(pattern for pattern in patterns if pattern["routing_outcomes"]["total"] == 1)
    assert routed_pattern["routing_outcomes"]["shadow_only"] == 1
    assert routed_pattern["routing_outcomes"]["last_reason"] == "below_min_hits"
    assert routed_pattern["routing_outcomes"]["last_disposition"] == "block"
    memory_events = RoleMemoryStore(tmp_path / "contentops").list_events("implementation", include_execution=True)
    failure_events = [event for event in memory_events if event["source"] == "worker_failure:run-1:implementation:implementation"]
    assert "pattern_match=" in failure_events[0]["body"]
    assert "pattern_hits=1" in failure_events[0]["body"]
    assert "pattern_disposition=escalate_human" in failure_events[0]["body"]
    assert "would_route_to=escalate_human" in failure_events[0]["body"]
    assert "deterministic_disposition=block" in failure_events[0]["body"]


def test_worker_failure_triage_applies_canary_route_for_high_confidence_network_pattern(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    library = FailurePatternLibrary(tmp_path)
    for index in range(3):
        library.record_failure(
            failure_class="network",
            summary="network socket closed during package install",
            disposition="escalate_human",
            role="implementation",
            node="implementation",
            source=f"worker_failure:prior-{index}:implementation:implementation",
        )
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow.")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.approvals.update({"prd": "approved", "rfc": "approved", "security": "approved"})
    state.current_node = "rfc_review_gate"
    state.next_node = "planner"

    def fake_run_job(self, job_id):
        job = json.loads((self.root / job_id / "job.json").read_text(encoding="utf-8"))
        role = job["role"]
        return {
            "job_id": job_id,
            "run_id": job["run_id"],
            "role": role,
            "adapter": job["adapter"],
            "status": "failed" if role == "implementation" else "completed",
            "summary": "network socket closed during package install",
            "failure_class": "unknown" if role == "implementation" else "",
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    result = advance_approved_workflow(state=state, workspace=tmp_path, project_config=project_config)

    assert result.next_node == "failure_triage"
    assert any(blocker.startswith("NEEDS HUMAN [unknown]:") for blocker in result.blockers)
    action_log = tmp_path / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl"
    actions = [json.loads(line) for line in action_log.read_text(encoding="utf-8").splitlines()]
    triage = [action for action in actions if action["action"] == "triage_decided"]
    assert triage[-1]["decision"] == "escalate_human"
    assert triage[-1]["deterministic_disposition"] == "block"
    assert triage[-1]["canary_route_applied"] == "true"
    assert triage[-1]["canary_route_reason"] == "canary_allowed"
    assert triage[-1]["would_route_to"] == "escalate_human"
    patterns = FailurePatternLibrary(tmp_path).list_patterns()
    assert patterns[0]["routing_outcomes"]["confirmed"] == 1
    assert patterns[0]["routing_outcomes"]["last_reason"] == "canary_allowed"
    assert patterns[0]["routing_outcomes"]["last_disposition"] == "escalate_human"
    memory_events = RoleMemoryStore(tmp_path / "contentops").list_events("implementation", include_execution=True)
    failure_events = [event for event in memory_events if event["source"] == "worker_failure:run-1:implementation:implementation"]
    assert "disposition=escalate_human" in failure_events[0]["body"]
    assert "deterministic_disposition=block" in failure_events[0]["body"]
    assert "canary_route_applied=true" in failure_events[0]["body"]


def test_worker_failure_triage_can_disable_canary_route_with_env_gate(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    library = FailurePatternLibrary(tmp_path)
    for index in range(3):
        library.record_failure(
            failure_class="network",
            summary="network socket closed during package install",
            disposition="escalate_human",
            role="implementation",
            node="implementation",
            source=f"worker_failure:prior-{index}:implementation:implementation",
        )
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow.")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.approvals.update({"prd": "approved", "rfc": "approved", "security": "approved"})
    state.current_node = "rfc_review_gate"
    state.next_node = "planner"
    monkeypatch.setenv("GOODTEAM_G3_CANARY_ROUTING", "0")

    def fake_run_job(self, job_id):
        job = json.loads((self.root / job_id / "job.json").read_text(encoding="utf-8"))
        role = job["role"]
        return {
            "job_id": job_id,
            "run_id": job["run_id"],
            "role": role,
            "adapter": job["adapter"],
            "status": "failed" if role == "implementation" else "completed",
            "summary": "network socket closed during package install",
            "failure_class": "unknown" if role == "implementation" else "",
        }

    monkeypatch.setattr("goodteam_os.graph.WorkerJobStore.run_job", fake_run_job)

    result = advance_approved_workflow(state=state, workspace=tmp_path, project_config=project_config)

    assert result.next_node == "blocked"
    action_log = tmp_path / "state" / "projects" / "CONTENTOPS" / "action-log.jsonl"
    actions = [json.loads(line) for line in action_log.read_text(encoding="utf-8").splitlines()]
    triage = [action for action in actions if action["action"] == "triage_decided"]
    assert triage[-1]["decision"] == "block"
    assert triage[-1]["canary_route_applied"] == "false"
    assert triage[-1]["canary_route_reason"] == "disabled"


def test_non_retryable_worker_failure_does_not_retry(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state("run-1", "CONTENTOPS", "Build a focused content workflow.")
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.approvals.update({"prd": "approved", "rfc": "approved", "security": "approved"})
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


def test_delivery_workflow_stops_when_ui_worker_fails(tmp_path, monkeypatch):
    project_config = write_contentops_project_fixture(tmp_path)
    state = create_initial_state(
        "run-1",
        "CONTENTOPS",
        "Build a polished ContentOps Studio interface that can be used directly.",
    )
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    state.approvals.update({"prd": "approved", "rfc": "approved", "security": "approved"})
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
