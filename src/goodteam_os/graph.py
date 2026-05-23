from pathlib import Path

from goodteam_os.artifacts import ArtifactStore
from goodteam_os.memory import ProjectMemoryStore, RoleMemoryStore, summarize_markdown
from goodteam_os.nodes.review_gate import gate_next_node
from goodteam_os.projects import load_project_target
from goodteam_os.roles import artifact_contract, context_file_specs, list_role_definitions, role_definition
from goodteam_os.state import AgileDevState
from goodteam_os.tasks import LocalTaskStore
from goodteam_os.workers import WorkerJobStore


DELIVERY_MODE_STANDARDS = {
    "mvp": [
        "Deliver a validation-ready MVP with the smallest useful product surface.",
        "Include a runnable local path, README, deterministic tests, and an explicit evolution path.",
        "A single service or lightweight app is acceptable when it keeps the user workflow clear.",
    ],
    "init_project": [
        "Treat this as a real internet product starter, not a throwaway demo.",
        "Specify frontend/backend separation, API contract, data layer, routing, authentication/session assumptions, and error states.",
        "Include DevOps expectations: local startup, environment variables, CI/test commands, deployment notes, health checks, logging, and rollback basics.",
    ],
    "multi_service": [
        "Treat this as a multi-service internet product with explicit service boundaries.",
        "Define API or event contracts, gateway/router responsibilities, database ownership, async/queue needs, and cross-service failure modes.",
        "Include observability, container orchestration or compose/k8s shape, service-level QA, release coordination, and rollback strategy.",
    ],
}


def collect_project_context(project_config: Path | None = None) -> list[dict[str, str]]:
    if project_config is None:
        return []
    if not project_config.is_file():
        return []

    target = load_project_target(project_config)
    repos = dict(target.get("repos", {}))
    packets = []
    memory_root = select_project_memory_root(repos)
    if memory_root is not None:
        memory = ProjectMemoryStore(memory_root).read_project_memory(str(target.get("key", "")))
        if memory["content"]:
            packets.append(
                {
                    "repo": "project_memory",
                    "path": memory["path"],
                    "purpose": "Project-local GoodTeam memory",
                    "summary": memory["summary"],
                }
            )
    for repo_name, files in context_file_specs().items():
        repo_root = repos.get(repo_name)
        if not repo_root:
            continue
        for spec in files:
            relative_path = str(spec.get("path", ""))
            path = Path(str(repo_root)) / relative_path
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8")
            packets.append(
                {
                    "repo": repo_name,
                    "path": str(path),
                    "purpose": str(spec.get("purpose", relative_path)),
                    "summary": summarize_context_file(content),
                }
            )
    return packets


def select_project_memory_root(repos: dict[object, object]) -> Path | None:
    runtime_root = resolve_project_runtime_root(repos)
    if runtime_root is not None:
        return runtime_root
    for name in ["docs", "backend", "frontend"]:
        value = repos.get(name)
        if value and Path(str(value)).is_dir():
            return Path(str(value))
    return None


def resolve_project_runtime_root(repos: dict[object, object]) -> Path | None:
    roots = [
        Path(str(value))
        for value in repos.values()
        if value and Path(str(value)).is_dir()
    ]
    if not roots:
        return None
    parents = {root.parent for root in roots}
    if len(roots) > 1 and len(parents) == 1:
        return next(iter(parents))
    return roots[0]


def ensure_project_memory(project_config: Path) -> Path | None:
    if not project_config.is_file():
        return None
    target = load_project_target(project_config)
    memory_root = select_project_memory_root(dict(target.get("repos", {})))
    if memory_root is None:
        return None
    return ProjectMemoryStore(memory_root).ensure_project_memory(
        str(target.get("key", "")),
        str(target.get("name", "")),
    )


def ensure_project_task_ledger(project_config: Path) -> Path | None:
    if not project_config.is_file():
        return None
    target = load_project_target(project_config)
    runtime_root = resolve_project_runtime_root(dict(target.get("repos", {})))
    if runtime_root is None:
        return None
    store = LocalTaskStore(runtime_root)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    if not store.path.is_file():
        store.path.write_text('{\n  "version": 1,\n  "next_id": 1,\n  "tasks": []\n}', encoding="utf-8")
    return store.path


def ensure_project_role_memories(project_config: Path) -> dict[str, dict[str, Path]]:
    if not project_config.is_file():
        return {}
    target = load_project_target(project_config)
    runtime_root = resolve_project_runtime_root(dict(target.get("repos", {})))
    if runtime_root is None:
        return {}
    return RoleMemoryStore(runtime_root).ensure_all_role_memories(
        list_role_definitions(),
        str(target.get("name", "")),
    )


def summarize_context_file(content: str) -> str:
    return summarize_markdown(content)


def render_prd_markdown(
    state: AgileDevState,
    project_config: Path | None = None,
) -> str:
    return render_artifact_markdown("prd", state, project_config)


def render_takeover_context_markdown(
    state: AgileDevState,
    project_config: Path | None = None,
) -> str:
    return render_artifact_markdown("takeover", state, project_config)


def render_artifact_markdown(
    artifact_key: str,
    state: AgileDevState,
    project_config: Path | None = None,
) -> str:
    contract = artifact_contract(artifact_key)
    role = role_definition(str(contract["role"]))
    target = load_project_target(project_config) if project_config is not None and project_config.is_file() else {}
    project_name = str(target.get("name", "") or state.project_key or "Configured Project")
    project_key = str(target.get("key", "") or state.project_key)
    delivery_mode = normalize_delivery_mode(getattr(state, "delivery_mode", "") or str(target.get("delivery_mode", "") or "init_project"))
    context = collect_project_context(project_config)
    context_sources = "\n".join(
        f"- `{item['path']}` ({item['purpose']}): {item['summary']}"
        for item in context
    )
    if not context_sources:
        context_sources = "- No project context files were loaded. Treat this artifact as incomplete."

    lines = [
        f"# {contract['title']}",
        "",
        "## Role Contract",
        "",
        f"- Role: {role['name']} (`{role['key']}`)",
        f"- Stable prompt: {role['stable_prompt']}",
        f"- Artifact purpose: {contract['purpose']}",
        "",
        "## Workflow Request",
        "",
        state.idea,
        "",
        "## Target Project",
        "",
        f"- Name: {project_name}",
        f"- Key: `{project_key}`",
        f"- Source type: `{state.source_type}`",
        f"- Project config: `{project_config or 'not configured'}`",
        f"- Delivery mode: `{delivery_mode}`",
        "",
        "## Project Context Read",
        "",
        context_sources,
        "",
        "## Delivery Mode Standard",
        "",
        *[f"- {item}" for item in DELIVERY_MODE_STANDARDS[delivery_mode]],
    ]
    if artifact_key == "prd":
        lines.extend(
            [
                "",
                "## PRD Writing Instructions",
                "",
                "Write a decision-ready PRD, not a template.",
                "",
                "- Use the project evidence as source material; do not merely repeat section headings.",
                "- For every requirement, name the user or stakeholder, problem or opportunity, product behavior, acceptance criteria, non-goals, and evidence source or labeled assumption.",
                "- Prefer concrete product slices over broad roadmap language.",
                "- Call out unknowns explicitly instead of inventing facts.",
                "- Separate workflow orchestration details from the target product scope.",
                "",
                "Before finalizing, self-review the PRD:",
                "",
                "- Does each scope item have testable acceptance criteria?",
                "- Are user value and business value stated separately?",
                "- Are non-goals explicit enough to prevent scope creep?",
                "- Are assumptions labeled?",
                "- Could an architect start an RFC from this without asking what the product actually is?",
                "- If not, rewrite the weak sections.",
                "",
                "## Multi-Role PRD Review",
                "",
                "- Product / Requirement self-check: verify the PRD states users, value, scope, non-goals, assumptions, and testable acceptance criteria.",
                "- Architect feasibility review: verify the PRD is concrete enough to produce an RFC with boundaries, dependencies, contracts, risks, and rollout concerns.",
                "- Review / QA verification review: verify the acceptance criteria are observable, testable, and cover important edge cases or regressions.",
            ]
        )
    for section in contract.get("sections", []):
        lines.extend(["", f"## {section['title']}", ""])
        for item in section.get("items", []):
            lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def render_downstream_artifact_markdown(
    artifact_key: str,
    state: AgileDevState,
    project_config: Path | None = None,
    *,
    worker_results: list[dict[str, object]] | None = None,
) -> str:
    markdown = render_artifact_markdown(artifact_key, state, project_config)
    related = "\n".join(
        f"- {name}: `{path}`"
        for name, path in sorted(state.artifacts.items())
        if name != artifact_key
    )
    if not related:
        related = "- No prior artifacts are registered yet."
    lines = [
        markdown.rstrip(),
        "",
        "## Prior GoodTeam Artifacts",
        "",
        related,
    ]
    if artifact_key == "implementation_handoff":
        results = worker_results or []
        if results:
            lines.extend(["", "## Worker Job Results", ""])
            for result in results:
                lines.append(f"- {result.get('role', 'unknown')} / {result.get('adapter', 'unknown')}: {result.get('status', 'unknown')} - {result.get('summary', '')}")
        else:
            lines.extend(["", "## Worker Job Results", "", "- No worker jobs were executed."])
    if artifact_key == "qa_report":
        lines.extend(
            [
                "",
                "## CONTENTOPS Smoke Verification",
                "",
                "- Verify generated project files exist in the configured runtime root.",
                "- Verify README includes runnable commands.",
                "- Verify source ingestion, organization, and platform drafting behavior are represented by tests or documented commands.",
            ]
        )
    if artifact_key == "release_notes":
        lines.extend(
            [
                "",
                "## Release Decision",
                "",
                "- Release is ready only after QA and code review artifacts exist.",
                "- Human approval is required at the release gate before retro memory closes the run.",
            ]
        )
    if artifact_key == "retro_memory":
        lines.extend(
            [
                "",
                "## Durable Memory Candidates",
                "",
                "- CONTENTOPS E2E uses Codex CLI workers with `gpt-5.5`.",
                "- Keep generated test project repos isolated from real product repos.",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def advance_approved_workflow(
    *,
    state: AgileDevState,
    workspace: Path,
    project_config: Path | None = None,
) -> AgileDevState:
    while True:
        before = (state.current_node, state.next_node, tuple(sorted(state.artifacts)))
        if state.next_node == "architect" and str(state.approvals.get("prd", "")) == "approved":
            state = run_architecture_until_gate(state, workspace, project_config)
        elif state.next_node == "planner" and str(state.approvals.get("rfc", "")) == "approved":
            state = run_delivery_until_release_gate(state, workspace, project_config)
        elif state.next_node == "retro_memory" and str(state.approvals.get("release", "")) == "approved":
            state = run_retro_until_complete(state, workspace, project_config)
        else:
            return state
        after = (state.current_node, state.next_node, tuple(sorted(state.artifacts)))
        if after == before:
            return state
    return state


def run_architecture_until_gate(
    state: AgileDevState,
    workspace: Path,
    project_config: Path | None = None,
) -> AgileDevState:
    store = ArtifactStore(root=workspace)
    rfc_path = store.write_markdown(
        state.run_id,
        "rfc",
        "rfc.md",
        render_downstream_artifact_markdown("rfc", state, project_config),
    )
    state.artifacts["rfc"] = str(rfc_path)
    state.approvals["rfc"] = state.approvals.get("rfc", "pending")
    state.current_node = "rfc_review_gate"
    state.next_node = gate_next_node(state, gate="rfc", approved_next="planner")
    return state


def run_delivery_until_release_gate(
    state: AgileDevState,
    workspace: Path,
    project_config: Path | None = None,
) -> AgileDevState:
    store = ArtifactStore(root=workspace)
    for artifact_key in ["review_report", "task_breakdown", "ui_spec"]:
        path = store.write_markdown(
            state.run_id,
            artifact_key,
            _artifact_filename(artifact_key),
            render_downstream_artifact_markdown(artifact_key, state, project_config),
        )
        state.artifacts[artifact_key] = str(path)

    worker_results = run_implementation_workers(state, project_config)
    failed_workers = [result for result in worker_results if str(result.get("status", "")) != "completed"]
    failed_workers.extend(
        result
        for result in worker_results
        if str(result.get("status", "")) == "completed" and qa_result_reports_failure(result)
    )
    if failed_workers:
        state.current_node = "implementation"
        state.next_node = "blocked"
        state.blockers.extend(
            worker_blocker_message(result)
            for result in failed_workers
        )
        handoff_path = store.write_markdown(
            state.run_id,
            "implementation_handoff",
            _artifact_filename("implementation_handoff"),
            render_downstream_artifact_markdown(
                "implementation_handoff",
                state,
                project_config,
                worker_results=worker_results,
            ),
        )
        state.artifacts["implementation_handoff"] = str(handoff_path)
        return state
    for artifact_key in ["implementation_handoff", "qa_report", "code_review", "release_notes"]:
        path = store.write_markdown(
            state.run_id,
            artifact_key,
            _artifact_filename(artifact_key),
            render_downstream_artifact_markdown(
                artifact_key,
                state,
                project_config,
                worker_results=worker_results if artifact_key == "implementation_handoff" else None,
            ),
        )
        state.artifacts[artifact_key] = str(path)

    state.current_node = "release_review_gate"
    state.next_node = gate_next_node(state, gate="release", approved_next="retro_memory")
    return state


def run_retro_until_complete(
    state: AgileDevState,
    workspace: Path,
    project_config: Path | None = None,
) -> AgileDevState:
    store = ArtifactStore(root=workspace)
    retro_path = store.write_markdown(
        state.run_id,
        "retro_memory",
        "retro-memory.md",
        render_downstream_artifact_markdown("retro_memory", state, project_config),
    )
    state.artifacts["retro_memory"] = str(retro_path)
    state.current_node = "complete"
    state.next_node = ""
    return state


def run_implementation_workers(
    state: AgileDevState,
    project_config: Path | None = None,
) -> list[dict[str, object]]:
    if project_config is None or not project_config.is_file():
        return []
    target = load_project_target(project_config)
    runtime_root = resolve_project_runtime_root(dict(target.get("repos", {})))
    if runtime_root is None:
        return []
    adapter = str(dict(target.get("worker_backend", {})).get("mode", "codex") or "codex")
    if adapter == "manual":
        adapter = "human"
    store = WorkerJobStore(runtime_root)
    jobs = [
        store.create_job(
            run_id=state.run_id,
            role="implementation",
            adapter=adapter,
            task=contentops_implementation_task(getattr(state, "delivery_mode", "init_project")),
            context=worker_context(state),
            constraints=[
                "Keep generated files inside the configured CONTENTOPS runtime root.",
                "Do not touch real product repositories outside this isolated workspace.",
                "Do not store secrets.",
            ],
            expected_artifacts=implementation_expected_artifacts(getattr(state, "delivery_mode", "init_project")),
        ),
        store.create_job(
            run_id=state.run_id,
            role="qa",
            adapter=adapter,
            task="Verify the CONTENTOPS MVP with reproducible commands and record any gaps.",
            context=worker_context(state),
            constraints=[
                "Prefer local deterministic tests.",
                "Do not require external network services.",
                "Do not leave long-running dev servers in the foreground; use bounded smoke checks and clean up background processes.",
            ],
            expected_artifacts=qa_expected_artifacts(getattr(state, "delivery_mode", "init_project")),
        ),
    ]
    return [store.run_job(job["id"]) for job in jobs]


def qa_result_reports_failure(result: dict[str, object]) -> bool:
    if str(result.get("role", "")) != "qa":
        return False
    result_path = result.get("result_path")
    if not result_path:
        return False
    path = Path(str(result_path))
    if not path.is_file():
        return False
    content = path.read_text(encoding="utf-8").lower()
    status_line = next(
        (line.strip() for line in content.splitlines() if line.strip().startswith("status:")),
        "",
    )
    if status_line and any(marker in status_line for marker in ["fail", "blocked", "not ready"]):
        return True
    return "release-blocking" in content and any(marker in content for marker in ["fail", "blocked"])


def worker_blocker_message(result: dict[str, object]) -> str:
    role = str(result.get("role", "worker"))
    if role == "qa" and qa_result_reports_failure(result):
        return "qa worker reported release-blocking failure: see QA result.md"
    return f"{role} worker failed: {result.get('summary', 'unknown failure')}"


def normalize_delivery_mode(value: str) -> str:
    mode = (value or "init_project").strip()
    if mode not in DELIVERY_MODE_STANDARDS:
        return "init_project"
    return mode


def implementation_expected_artifacts(delivery_mode: str) -> list[str]:
    mode = normalize_delivery_mode(delivery_mode)
    if mode == "mvp":
        return ["README.md", "contentops.py", "tests/test_contentops.py"]
    if mode == "multi_service":
        return [
            "README.md",
            "services/api",
            "services/web",
            "database migrations",
            "docker-compose.yml",
            "tests",
            "deployment notes",
        ]
    return [
        "README.md",
        "frontend app",
        "backend API",
        "data layer schema or migrations",
        "router configuration",
        "tests",
        "DevOps/local startup docs",
    ]


def qa_expected_artifacts(delivery_mode: str) -> list[str]:
    mode = normalize_delivery_mode(delivery_mode)
    if mode == "mvp":
        return ["QA notes in worker result", "local test command"]
    if mode == "multi_service":
        return ["service-level QA notes", "contract smoke checks", "compose or orchestration smoke command"]
    return ["API/UI QA notes", "data-layer verification", "local startup and test commands"]


def contentops_implementation_task(delivery_mode: str = "init_project") -> str:
    mode = normalize_delivery_mode(delivery_mode)
    if mode == "mvp":
        return (
            "Create a minimal runnable AI Content Studio MVP for CONTENTOPS. "
            "It must ingest source material, organize it by topic, and draft platform-specific posts "
            "for TikTok, Reddit, X, and LinkedIn. Include a README with verification commands and "
            "a deterministic local test."
        )
    if mode == "multi_service":
        return (
            "Create a production-grade multi-service AI Content Studio scaffold for CONTENTOPS. "
            "Include separate API and web services, data-layer schema or migrations, service routing, "
            "local orchestration, DevOps notes, tests, and README commands. The product must ingest "
            "source material, organize it by topic, and draft posts for TikTok, Reddit, X, and LinkedIn."
        )
    return (
        "Create a production-starter AI Content Studio scaffold for CONTENTOPS. Include a frontend app, "
        "backend API, data layer schema or migrations, router/API contract documentation, environment "
        "examples without secrets, DevOps/local startup notes, tests, and README commands. The product "
        "must ingest source material, organize it by topic, and draft posts for TikTok, Reddit, X, and LinkedIn."
    )


def worker_context(state: AgileDevState) -> str:
    artifacts = "\n".join(f"- {name}: {path}" for name, path in sorted(state.artifacts.items()))
    return "\n".join(
        [
            f"Run: {state.run_id}",
            f"Project: {state.project_key}",
            f"Goal: {state.idea}",
            "Artifacts:",
            artifacts or "- none",
        ]
    )


def _artifact_filename(artifact_key: str) -> str:
    return artifact_key.replace("_", "-") + ".md"


def run_adoption_until_next_gate(
    state: AgileDevState,
    workspace: Path,
    project_config: Path | None = None,
) -> AgileDevState:
    if project_config is None:
        raise ValueError("Project config is required for existing project adoption.")
    store = ArtifactStore(root=workspace)
    memory_path = ensure_project_memory(project_config)
    task_ledger_path = ensure_project_task_ledger(project_config)
    ensure_project_role_memories(project_config)
    takeover_path = store.write_markdown(
        state.run_id,
        "takeover",
        "takeover-context.md",
        render_takeover_context_markdown(state, project_config),
    )
    state.source_type = "existing_project"
    state.source_ref = str(project_config)
    if memory_path is not None:
        state.context_packet_path = str(memory_path)
    if task_ledger_path is not None:
        state.artifacts["task_ledger"] = str(task_ledger_path)
    state.artifacts["takeover"] = str(takeover_path)
    state.approvals["takeover"] = "pending"
    state.current_node = "takeover_review_gate"
    state.next_node = gate_next_node(state, gate="takeover", approved_next="product_requirement")
    return state


def run_until_next_gate(
    state: AgileDevState,
    workspace: Path,
    project_config: Path | None = None,
) -> AgileDevState:
    store = ArtifactStore(root=workspace)
    prd_path = store.write_markdown(
        state.run_id,
        "prd",
        "prd.md",
        render_prd_markdown(state, project_config),
    )
    state.artifacts["prd"] = str(prd_path)
    state.current_node = "prd_review_gate"
    state.next_node = gate_next_node(state, gate="prd", approved_next="architect")
    return state
