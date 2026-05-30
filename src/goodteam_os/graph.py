import re
from pathlib import Path

from goodteam_os.artifacts import ArtifactStore
from goodteam_os.memory import ProjectMemoryStore, RoleMemoryStore, summarize_markdown
from goodteam_os.nodes.review_gate import gate_next_node
from goodteam_os.projects import load_project_target
from goodteam_os.roles import artifact_contract, context_file_specs, list_role_definitions, role_definition
from goodteam_os.staging import run_staging_backend, staging_backend_enabled
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
    contract = artifact_contract("prd")
    role = role_definition(str(contract["role"]))
    target = load_project_target(project_config) if project_config is not None and project_config.is_file() else {}
    project_name = str(target.get("name", "") or state.project_key or "Configured Project")
    project_key = str(target.get("key", "") or state.project_key)
    delivery_mode = normalize_delivery_mode(getattr(state, "delivery_mode", "") or str(target.get("delivery_mode", "") or "init_project"))
    context = collect_project_context(project_config)
    context_sources = format_context_sources(context)
    is_contentops = _is_contentops_request(state, project_name, project_key, context)
    if is_contentops:
        decision = (
            "Build ContentOps Studio v1 as a usable local product workflow for turning reusable brand "
            "briefs and captured source material into organized topics, platform-specific drafts, "
            "reviewable publishing work, and verification evidence."
        )
        users = [
            "Content creators and operators who need one place to keep brand voice, source references, topics, and draft variants aligned.",
            "Reviewers or founders who need evidence that a draft came from approved inputs before it reaches TikTok, Reddit, X, or LinkedIn.",
            "Implementers who need a bounded v1 that fits the existing Fastify/Zod/SQLite + React/Vite/Vitest stack.",
        ]
        scope = [
            "Reusable brand brief: create and update a persistent brief with audience, voice, positioning, banned claims, preferred calls to action, and platform notes.",
            "Source capture: save URLs, pasted notes, quotes, and provenance metadata so every idea has traceable input material.",
            "Topic organization: group sources into topics with status, priority, tags, and enough context for drafting.",
            "Multi-platform draft generation: produce editable draft variants for TikTok, Reddit, X, and LinkedIn from a topic and brand brief.",
            "Review/publish board: show drafts moving through review, changes requested, approved, scheduled, and published-ready columns.",
            "Verification evidence: attach source links, brief version, generation inputs, reviewer decision, and test/smoke evidence to each ready draft.",
            "Reuse-first technical constraint: prefer mature open-source components and the existing Fastify/Zod/SQLite + React/Vite/Vitest stack over custom infrastructure unless the RFC proves a gap.",
        ]
        non_goals = [
            "No direct autopublishing to social platforms in v1; export/copy-ready outputs are enough.",
            "No custom workflow engine, queue system, auth provider, or database infrastructure unless the approved RFC shows the current stack cannot support the v1 workflow.",
            "No broad analytics suite, paid media management, or multi-tenant agency controls in v1.",
        ]
        acceptance = [
            "A user can create a reusable brand brief, edit it, and see draft generation use the latest saved brief values.",
            "A user can capture source material with provenance and assign it to a topic without losing the original source reference.",
            "A user can organize topics by status/tag/priority and select one topic to generate platform-specific drafts.",
            "The product creates editable drafts for TikTok, Reddit, X, and LinkedIn that preserve source evidence and brand-brief references.",
            "The review/publish board exposes every draft state and supports moving an item through review decisions without data loss.",
            "Each approved draft displays verification evidence: source capture, brand brief snapshot or version, topic, platform, reviewer decision, and relevant test/smoke result.",
            "Implementation evidence includes deterministic backend/frontend tests and a build or smoke command using Fastify/Zod/SQLite + React/Vite/Vitest unless an approved RFC records a mature replacement.",
        ]
        risks = [
            "Draft generation quality may be weak without clear prompt and source-selection rules; RFC should define deterministic local behavior first.",
            "Verification evidence can become noisy if every draft stores too much raw source text; keep summaries and provenance structured.",
            "Open-source component reuse needs license and maintenance checks before adoption.",
        ]
    else:
        decision = (
            f"Define the next v1 product slice for {project_name} from the loaded project evidence, "
            "then hand that slice to architecture with explicit scope, non-goals, and testable outcomes."
        )
        users = [
            "Primary product users affected by the next roadmap slice identified in project memory and repository docs.",
            "Project maintainers who need clear boundaries before architecture and implementation work begins.",
            "Reviewers who need observable acceptance criteria rather than workflow-only instructions.",
        ]
        scope = [
            "Use the loaded project memory, README, roadmap, and API/frontend context as the source of truth for the next product slice.",
            "Prioritize product behavior that can be implemented and verified inside the configured delivery mode.",
            "Keep workflow orchestration, GoodTeam approvals, and local state management outside the target product scope unless explicitly requested.",
        ]
        non_goals = [
            "Do not rewrite unrelated subsystems discovered in context unless they block the approved v1 slice.",
            "Do not treat project takeover, approval routing, or GoodTeam internal mechanics as user-facing product requirements.",
            "Do not invent unstated external integrations, credentials, or deployment targets.",
        ]
        acceptance = [
            "The approved PRD names the user, value, scope, non-goals, and risks for the next product slice.",
            "Each v1 scope item has observable acceptance criteria that QA can verify with local tests, smoke checks, or documented review evidence.",
            "Architecture can produce an RFC from this PRD without asking what product behavior should be built.",
            "Delivery uses the configured project stack and delivery-mode standards unless the RFC records a justified exception.",
        ]
        risks = [
            "Project context may be stale or incomplete; unresolved contradictions must be carried into RFC open questions.",
            "Roadmap wording may be broader than a safe v1 slice; keep implementation bounded to the acceptance criteria.",
            "External services, secrets, and tracker-only decisions require human confirmation before implementation.",
        ]

    lines = [
        f"# {contract['title']}",
        "",
        "## Product Decision",
        "",
        decision,
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
        "",
        "## Users And Value",
        "",
        *[f"- {item}" for item in users],
        "",
        "## V1 Scope",
        "",
        *[f"- {item}" for item in scope],
        "",
        "## Non-Goals",
        "",
        *[f"- {item}" for item in non_goals],
        "",
        "## Acceptance Criteria",
        "",
        *[f"- {item}" for item in acceptance],
        "",
        "## Risks And Open Questions",
        "",
        *[f"- {item}" for item in risks],
        "",
        "## Review Readiness",
        "",
        f"- Product / Requirement: this PRD commits {project_name} to a bounded v1 product slice.",
        "- Architect: produce an RFC only for the approved scope and record any component reuse decisions.",
        "- Review / QA: verify each acceptance criterion with observable product evidence before release.",
        "",
        "## Role Contract",
        "",
        f"- Role: {role['name']} (`{role['key']}`)",
        f"- Artifact purpose: {contract['purpose']}",
        "",
    ]
    return "\n".join(lines)


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
    if artifact_key == "rfc":
        return render_rfc_markdown(state, project_config)

    contract = artifact_contract(artifact_key)
    role = role_definition(str(contract["role"]))
    target = load_project_target(project_config) if project_config is not None and project_config.is_file() else {}
    project_name = str(target.get("name", "") or state.project_key or "Configured Project")
    project_key = str(target.get("key", "") or state.project_key)
    delivery_mode = normalize_delivery_mode(getattr(state, "delivery_mode", "") or str(target.get("delivery_mode", "") or "init_project"))
    context = collect_project_context(project_config)
    context_sources = format_context_sources(context)

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
    if artifact_key not in {"qa_report", "code_review", "release_notes"}:
        for section in contract.get("sections", []):
            lines.extend(["", f"## {section['title']}", ""])
            for item in section.get("items", []):
                lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def render_rfc_markdown(
    state: AgileDevState,
    project_config: Path | None = None,
) -> str:
    contract = artifact_contract("rfc")
    role = role_definition(str(contract["role"]))
    target = load_project_target(project_config) if project_config is not None and project_config.is_file() else {}
    project_name = str(target.get("name", "") or state.project_key or "Configured Project")
    project_key = str(target.get("key", "") or state.project_key)
    delivery_mode = normalize_delivery_mode(getattr(state, "delivery_mode", "") or str(target.get("delivery_mode", "") or "init_project"))
    context = collect_project_context(project_config)
    is_contentops = _is_contentops_request(state, project_name, project_key, context)

    if is_contentops:
        architecture = (
            "Evolve the existing Fastify/Zod/SQLite backend and React/Vite/Vitest frontend into "
            "ContentOps Studio v1. Add the smallest durable domain model needed for brand briefs, "
            "source capture, topic organization, multi-platform drafts, review status, and evidence. "
            "Prefer mature open-source UI/data utilities or existing project helpers before adding custom infrastructure."
        )
        data_model = [
            "`brand_briefs`: singleton or versioned rows for audience, voice, positioning, banned claims, CTA guidance, platform notes, and timestamps.",
            "`source_materials`: captured URL/text/note records with provenance, author/source label, summary, tags, and created/updated timestamps.",
            "`topics`: work items that group sources with title, status, priority, tags, brief reference, and drafting context.",
            "`topic_sources`: join table between topics and source_materials when many-to-many grouping is needed.",
            "`drafts`: platform-specific draft variants with platform, body, status, topic id, brief version/id, reviewer notes, and timestamps.",
            "`draft_evidence`: structured evidence rows for source ids, brief snapshot/version, generation input summary, reviewer decision, and verification command/result references.",
        ]
        api_contract = [
            "`GET /api/brand-brief` returns the active reusable brand brief.",
            "`POST /api/brand-brief` validates and upserts the reusable brand brief.",
            "`POST /api/source-materials` captures source material with provenance and optional topic assignment.",
            "`GET /api/topics` returns topics with source and draft counts for organization views.",
            "`POST /api/topics` creates a topic with status, priority, tags, and selected source ids.",
            "`POST /api/topics/:topicId/drafts` generates or regenerates TikTok, Reddit, X, and LinkedIn drafts using the topic, selected sources, and active brand brief.",
            "`GET /api/drafts` supports review board filters by platform, status, topic, and priority.",
            "`PATCH /api/drafts/:draftId/status` records review/publish board transitions and reviewer notes.",
            "`GET /api/drafts/:draftId/evidence` returns source, brief, topic, decision, and verification evidence for a draft.",
        ]
        frontend = [
            "Add a Brand Brief editor view or panel with saved state and validation feedback.",
            "Extend source capture so a user can save provenance, attach sources to topics, and see capture errors.",
            "Add topic organization controls for status, tags, priority, and source associations.",
            "Add a draft generation workflow that shows TikTok, Reddit, X, and LinkedIn variants as editable outputs.",
            "Add a review/publish board using the existing React app; use mature open-source board/form/table utilities only when they reduce custom code and pass project fit.",
            "Show verification evidence next to approved drafts so reviewers can inspect source and brief lineage.",
        ]
        verification = [
            "Backend Vitest tests cover Zod validation, brand brief upsert, source capture, topic grouping, draft generation, status transitions, and evidence retrieval.",
            "Frontend Vitest tests cover brief editing, source capture, topic organization, draft cards, board transitions, and evidence display.",
            "`npm run test` and `npm run build` pass from the target project root with SQLite using a deterministic local database path.",
            "A smoke path creates a brand brief, captures a source, creates a topic, generates drafts, moves one draft through review, and confirms evidence is visible.",
        ]
        risks = [
            "If a kanban/editor/table library is added, verify license, package health, bundle impact, and whether it fits Vite/React without heavy integration work.",
            "Keep generated draft logic deterministic for local tests; external AI service calls should remain out of v1 unless configured without secrets in repo.",
            "Schema migration must preserve existing seeded starter data or document a reset path for local development.",
        ]
    else:
        architecture = (
            f"Build the approved {project_name} v1 slice inside the configured `{delivery_mode}` delivery mode, "
            "keeping ownership boundaries and project context as the source of truth."
        )
        data_model = [
            "Use existing persistence models when they already support the approved PRD behavior.",
            "Add only the fields and migrations needed for the approved v1 scope.",
            "Record assumptions or missing context before introducing new infrastructure.",
        ]
        api_contract = [
            "Expose or update the minimum API routes needed by the approved user flow.",
            "Validate request/response payloads with the project's existing validation pattern.",
            "Document error states and rollback behavior for any state-changing endpoint.",
        ]
        frontend = [
            "Implement the approved user flow in the existing frontend structure.",
            "Reuse existing components, routing, state management, and test utilities before adding dependencies.",
            "Cover loading, empty, error, and success states that map to acceptance criteria.",
        ]
        verification = [
            "Add deterministic tests for each state-changing path and primary UI flow.",
            "Run the configured project test/build commands.",
            "Record smoke evidence that maps directly to the PRD acceptance criteria.",
        ]
        risks = [
            "Loaded context may be stale; carry unclear product or API decisions into implementation tasks as explicit assumptions.",
            "Avoid unrelated refactors while touching shared contracts.",
        ]

    lines = [
        f"# {contract['title']}",
        "",
        "## Architecture Decision",
        "",
        architecture,
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
        format_context_sources(context),
        "",
        "## Delivery Mode Standard",
        "",
        *[f"- {item}" for item in DELIVERY_MODE_STANDARDS[delivery_mode]],
        "",
        "## Data Model",
        "",
        *[f"- {item}" for item in data_model],
        "",
        "## API Contract",
        "",
        *[f"- {item}" for item in api_contract],
        "",
        "## Frontend And UX Contract",
        "",
        *[f"- {item}" for item in frontend],
        "",
        "## Verification Plan",
        "",
        *[f"- {item}" for item in verification],
        "",
        "## Rollout And Rollback",
        "",
        "- Keep v1 local-first and deterministic; migrations must be reversible or paired with a documented local reset.",
        "- Do not store secrets in configs, state, artifacts, or generated docs.",
        "- Release only after QA and code review evidence map back to the PRD acceptance criteria.",
        "",
        "## Risks And Open Questions",
        "",
        *[f"- {item}" for item in risks],
        "",
        "## Role Contract",
        "",
        f"- Role: {role['name']} (`{role['key']}`)",
        f"- Artifact purpose: {contract['purpose']}",
        "",
    ]
    return "\n".join(lines)


def format_context_sources(context: list[dict[str, str]]) -> str:
    context_sources = "\n".join(
        f"- `{item['path']}` ({item['purpose']}): {item['summary']}"
        for item in context
    )
    if not context_sources:
        context_sources = "- No project context files were loaded. Treat this artifact as incomplete."
    return context_sources


def _is_contentops_request(
    state: AgileDevState,
    project_name: str,
    project_key: str,
    context: list[dict[str, str]],
) -> bool:
    haystack = "\n".join(
        [
            state.idea,
            state.project_key,
            project_name,
            project_key,
            *[item.get("summary", "") for item in context],
            *[item.get("path", "") for item in context],
        ]
    ).lower()
    return "contentops" in haystack or "content studio" in haystack or "content studio" in haystack


def render_downstream_artifact_markdown(
    artifact_key: str,
    state: AgileDevState,
    project_config: Path | None = None,
    *,
    worker_results: list[dict[str, object]] | None = None,
) -> str:
    markdown = render_artifact_markdown(artifact_key, state, project_config)
    results = worker_results or []
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
                "## Verification Matrix",
                "",
                *format_worker_evidence_lines(results),
            ]
        )
        lines.extend(format_contentops_acceptance_evidence(results))
        lines.extend(format_ui_delivery_acceptance_evidence(results))
    if artifact_key == "code_review":
        lines.extend(
            [
                "",
                "## Review Findings",
                "",
                *format_code_review_evidence_lines(results),
            ]
        )
    if artifact_key == "release_notes":
        evidence = format_release_evidence_lines(state, results)
        lines.extend(
            [
                "",
                "## Release Decision",
                "",
                "- Release recommendation: approve for local starter release with documented caveats.",
                "- Human approval is required at the release gate before retro memory closes the run.",
                "",
                "## Verification Evidence",
                "",
                *evidence,
                "",
                "## Known Caveats",
                "",
                *format_release_caveats_lines(state, results),
                "",
                "## Rollback",
                "",
                "- For the local starter, rollback by restoring the previous project directory or resetting the generated SQLite database from documented local setup steps.",
                "- Do not roll forward with release approval if a QA result contains `release-blocking`, `fail`, or `blocked` status.",
            ]
        )
    if artifact_key == "retro_memory":
        lines.extend(
            [
                "",
                "## Durable Memory Candidates",
                "",
                "- CONTENTOPS E2E uses Codex CLI workers with `gpt-5.3-codex` for programming tasks.",
                "- Keep generated test project repos isolated from real product repos.",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def format_worker_evidence_lines(worker_results: list[dict[str, object]]) -> list[str]:
    if not worker_results:
        return ["- No worker verification evidence was attached to this artifact."]
    lines: list[str] = []
    for result in worker_results:
        role = str(result.get("role", "unknown"))
        status = str(result.get("status", "unknown"))
        summary = str(result.get("summary", ""))
        model = str(result.get("model", "") or "")
        result_text = read_worker_result_excerpt(result)
        lines.append(f"- {role}: `{status}` via `{model or 'unspecified model'}` - {summary}")
        for signal in extract_worker_signals(result_text):
            lines.append(f"  - Evidence: {signal}")
    return lines


def format_contentops_acceptance_evidence(worker_results: list[dict[str, object]]) -> list[str]:
    combined = "\n".join(read_worker_result_excerpt(result) for result in worker_results)
    normalized = combined.lower()
    checks = [
        ("Reusable brand brief", ["brand brief"]),
        ("Source capture", ["source capture", "source material"]),
        ("Topic organization", ["topic create", "topics", "topic organization"]),
        ("Multi-platform drafts", ["4 platforms", "tiktok", "reddit", "linkedin"]),
        ("Review/publish board", ["draft status", "review", "status transition"]),
        ("Verification evidence", ["evidence retrieval", "verification evidence", "draft evidence"]),
    ]
    lines = ["", "## PRD Acceptance Evidence", ""]
    for label, needles in checks:
        status = "covered" if any(needle in normalized for needle in needles) else "not evidenced in worker summary"
        lines.append(f"- {label}: {status}.")
    return lines


def format_ui_delivery_acceptance_evidence(worker_results: list[dict[str, object]]) -> list[str]:
    ui_results = [result for result in worker_results if str(result.get("role", "")) == "ui"]
    lines = ["", "## UI Delivery Evidence", ""]
    if not ui_results:
        return [*lines, "- UI delivery worker did not run; do not approve release for user-facing products without a direct UI implementation pass."]
    combined = "\n".join(read_worker_result_excerpt(result) for result in ui_results).lower()
    checks = [
        ("Direct frontend implementation", ["changed frontend", "frontend files", "src/", "css"]),
        ("Homepage/first-screen usability", ["homepage", "brand brief", "first screen"]),
        ("Review board visual usability", ["review board", "draft board", "draft cards"]),
        ("Evidence dossier readability", ["evidence dossier", "verification", "provenance"]),
        ("Browser screenshot evidence", ["screenshot", ".png", "browser"]),
    ]
    for label, needles in checks:
        status = "covered" if any(needle in combined for needle in needles) else "not evidenced in UI worker summary"
        lines.append(f"- {label}: {status}.")
    return lines


def format_code_review_evidence_lines(worker_results: list[dict[str, object]]) -> list[str]:
    if not worker_results:
        return ["- No release-blocking findings can be assessed without worker evidence."]
    lines = [
        "- No release-blocking worker failure was reported by GoodTeam.",
        "- Implementation and QA worker summaries are attached below for reviewer traceability.",
    ]
    combined = "\n".join(read_worker_result_excerpt(result) for result in worker_results).lower()
    if "no real-browser interactive e2e" in combined:
        lines.append("- Residual risk: browser interaction coverage is lower than API/data coverage because no real-browser interactive E2E run was executed.")
    if "ui delivery worker did not run" in combined:
        lines.append("- Release blocker: direct UI delivery evidence is missing for the user-facing product.")
    if "better-sqlite3" in combined:
        lines.append("- Operational caveat: `better-sqlite3` may require `npm rebuild better-sqlite3` after Node ABI changes.")
    lines.extend(["", "## Worker Evidence", ""])
    lines.extend(format_worker_evidence_lines(worker_results))
    return lines


def format_release_evidence_lines(state: AgileDevState, worker_results: list[dict[str, object]]) -> list[str]:
    lines = [
        f"- Run: `{state.run_id}`",
        f"- Project: `{state.project_key}`",
        f"- Delivery mode: `{getattr(state, 'delivery_mode', '') or 'init_project'}`",
    ]
    if worker_results:
        lines.extend(format_worker_evidence_lines(worker_results))
    else:
        for key in ["implementation_handoff", "qa_report", "code_review", "staging_report"]:
            artifact_path = state.artifacts.get(key)
            if artifact_path:
                lines.append(f"- {key}: `{artifact_path}`")
        if not any(key in state.artifacts for key in ["implementation_handoff", "qa_report", "code_review", "staging_report"]):
            lines.append("- No implementation, QA, code review, or staging evidence is registered yet.")
    return lines


def format_release_caveats_lines(state: AgileDevState, worker_results: list[dict[str, object]]) -> list[str]:
    combined = "\n".join(read_worker_result_excerpt(result) for result in worker_results).lower()
    caveats: list[str] = []
    if "better-sqlite3" in combined:
        caveats.append("- Native module caveat: `better-sqlite3` can require `npm rebuild better-sqlite3` after Node runtime changes.")
    if "no real-browser interactive e2e" in combined or "accessibility" in combined:
        caveats.append("- QA caveat: API/data and component coverage are stronger than full browser interaction/accessibility coverage.")
    if state.blockers:
        caveats.append(f"- Blockers remain: {'; '.join(state.blockers)}")
    if not caveats:
        caveats.append("- No unresolved caveats were reported in attached worker summaries.")
    return caveats


def read_worker_result_excerpt(result: dict[str, object], *, max_chars: int = 3000) -> str:
    result_path = result.get("result_path")
    if not result_path:
        return ""
    path = Path(str(result_path))
    if not path.is_file():
        return ""
    return sanitize_artifact_excerpt(path.read_text(encoding="utf-8", errors="replace"), max_chars=max_chars)


def sanitize_artifact_excerpt(text: str, *, max_chars: int = 3000) -> str:
    secretish = re.compile(
        r"(?i)(api[_-]?key|access[_-]?token|bot[_-]?token|authorization|secret|password)\s*[:=]\s*\S+"
    )
    redacted = secretish.sub(r"\1: [redacted]", text)
    return redacted[:max_chars]


def extract_worker_signals(text: str) -> list[str]:
    if not text.strip():
        return []
    signals: list[str] = []
    patterns = [
        r"`npm run test[^`]*`[^.\n]*(?:passed|pass|16/16)[^.\n]*",
        r"`npm run build[^`]*`[^.\n]*(?:passed|pass|succeeded)[^.\n]*",
        r"PASS live API smoke[^.\n]*",
        r"Draft generation returns exactly 4 platforms[^.\n]*",
        r"Changed frontend files[^.\n]*",
        r"Homepage Brand Brief first screen[^.\n]*",
        r"Review board draft cards[^.\n]*",
        r"Browser screenshot evidence[^\n]*",
        r"no real-browser interactive E2E[^.\n]*",
        r"`better-sqlite3`[^.\n]*",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            signal = " ".join(match.group(0).split())
            if signal and signal not in signals:
                signals.append(signal)
    if not signals:
        for line in text.splitlines():
            clean = line.strip(" -")
            if clean and any(marker in clean.lower() for marker in ["pass", "verification", "risk", "coverage gap"]):
                signals.append(clean)
            if len(signals) >= 5:
                break
    return signals[:8]


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
        elif state.next_node == "release" and str(state.approvals.get("staging", "")) == "approved":
            state = run_release_until_gate(state, workspace, project_config)
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
    for artifact_key in ["implementation_handoff", "qa_report", "code_review"]:
        path = store.write_markdown(
            state.run_id,
            artifact_key,
            _artifact_filename(artifact_key),
            render_downstream_artifact_markdown(
                artifact_key,
                state,
                project_config,
                worker_results=worker_results,
            ),
        )
        state.artifacts[artifact_key] = str(path)

    if staging_backend_enabled(source_type=state.source_type, project_config=project_config):
        return run_staging_until_gate(state, workspace, project_config)

    return run_release_until_gate(state, workspace, project_config, worker_results=worker_results)


def run_staging_until_gate(
    state: AgileDevState,
    workspace: Path,
    project_config: Path | None = None,
) -> AgileDevState:
    store = ArtifactStore(root=workspace)
    if project_config is None or not project_config.is_file():
        raise ValueError("Project config is required for staging deployment.")

    staging_result = run_staging_backend(project_config=project_config)
    staging_path = store.write_markdown(
        state.run_id,
        "staging_report",
        _artifact_filename("staging_report"),
        render_staging_report_markdown(state, project_config, staging_result),
    )
    state.artifacts["staging_report"] = str(staging_path)
    if str(staging_result.get("status", "")) != "passed":
        failed_step = str(staging_result.get("failed_step", "") or "deploy")
        state.current_node = "implementation"
        state.next_node = "blocked"
        evidence_gap = str(staging_result.get("evidence_gap", "") or "")
        if failed_step == "acceptance" and evidence_gap:
            state.blockers.append(f"staging acceptance failed: {evidence_gap}")
        else:
            state.blockers.append(f"staging {failed_step} failed")
        return state

    state.current_node = "staging_review_gate"
    state.next_node = gate_next_node(state, gate="staging", approved_next="release")
    return state


def run_release_until_gate(
    state: AgileDevState,
    workspace: Path,
    project_config: Path | None = None,
    *,
    worker_results: list[dict[str, object]] | None = None,
) -> AgileDevState:
    store = ArtifactStore(root=workspace)
    release_path = store.write_markdown(
        state.run_id,
        "release_notes",
        _artifact_filename("release_notes"),
        render_downstream_artifact_markdown("release_notes", state, project_config, worker_results=worker_results),
    )
    state.artifacts["release_notes"] = str(release_path)
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


def resolve_role_adapter(target: dict[str, object], role: str) -> str:
    """Pick the worker backend for a single role: per-role `workers.<role>`
    overrides the global `worker_backend.mode`, defaulting to codex. This is what
    lets a project run, say, implementation on claude and qa on codex."""
    default_mode = str(dict(target.get("worker_backend", {})).get("mode", "codex") or "codex")
    per_role = str(dict(target.get("workers", {})).get(role, "") or "").strip()
    adapter = per_role or default_mode
    if adapter == "manual":
        adapter = "human"
    return adapter


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
    store = WorkerJobStore(runtime_root)
    jobs = [
        store.create_job(
            run_id=state.run_id,
            role="ui",
            adapter=resolve_role_adapter(target, "ui"),
            model=ui_delivery_worker_model(state),
            task=contentops_ui_delivery_task(getattr(state, "delivery_mode", "init_project")),
            context=worker_context(state),
            constraints=[
                "Directly implement UI changes in the configured frontend project; do not stop at ui-spec.md.",
                "Keep generated files inside the configured CONTENTOPS runtime root.",
                "Do not touch real product repositories outside this isolated workspace.",
                "Do not store secrets.",
                "Capture browser screenshot evidence when a local frontend can run.",
            ],
            expected_artifacts=ui_delivery_expected_artifacts(getattr(state, "delivery_mode", "init_project")),
        ),
        store.create_job(
            run_id=state.run_id,
            role="implementation",
            adapter=resolve_role_adapter(target, "implementation"),
            model=worker_model_for_role(state, "implementation"),
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
            adapter=resolve_role_adapter(target, "qa"),
            model=worker_model_for_role(state, "qa"),
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


def ui_delivery_worker_model(state: AgileDevState) -> str:
    return (
        worker_model_for_role(state, "frontend")
        or worker_model_for_role(state, "implementation")
        or worker_model_for_role(state, "ui")
        or "gpt-5.3-codex"
    )


def worker_model_for_role(state: AgileDevState, role: str) -> str:
    model = str(dict(state.role_models.get(role, {})).get("primary", "")).strip()
    if ":" in model:
        return model.split(":", 1)[1].strip()
    return model


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
    return ["API/UI QA notes", "visual acceptance notes", "data-layer verification", "local startup and test commands"]


def ui_delivery_expected_artifacts(delivery_mode: str) -> list[str]:
    mode = normalize_delivery_mode(delivery_mode)
    if mode == "mvp":
        return ["frontend files", "visual acceptance notes"]
    if mode == "multi_service":
        return ["web service frontend files", "design system or component updates", "browser screenshot evidence"]
    return ["frontend files", "design system or CSS updates", "browser screenshot evidence", "visual acceptance notes"]


def contentops_ui_delivery_task(delivery_mode: str = "init_project") -> str:
    mode = normalize_delivery_mode(delivery_mode)
    scope = "the MVP interface" if mode == "mvp" else "the production-starter interface"
    return (
        f"directly implement a polished ContentOps Studio UI for {scope}; do not stop at documentation. "
        "Improve the homepage/Brand Brief experience, source/topic creation flow, review board, draft cards, "
        "and evidence dossier so the product is immediately usable without reading documentation. "
        "Use the existing React/Vite frontend and mature installed components or project-local helpers before "
        "adding dependencies. Include responsive layout, clear empty/loading/error states, accessible controls, "
        "professional typography/spacing/color, and screenshot-backed visual acceptance. "
        "Record changed frontend files, browser URL/ports used, screenshots, and unresolved UI risks."
    )


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


def render_staging_report_markdown(
    state: AgileDevState,
    project_config: Path | None,
    staging_result: dict[str, object],
) -> str:
    markdown = render_downstream_artifact_markdown("staging_report", state, project_config)
    lines = [
        markdown.rstrip(),
        "",
        "## Staging Execution Summary",
        "",
        f"- Status: {staging_result.get('status', '')}",
        f"- SSH target: `{staging_result.get('ssh_target', '')}`",
        f"- Remote dir: `{staging_result.get('remote_dir', '')}`",
        f"- Git ref: `{staging_result.get('git_ref', '')}`",
        f"- Public URL: `{staging_result.get('public_url', '')}`",
        f"- Evidence level: `{staging_result.get('evidence_level', '')}`",
        f"- Evidence sufficient: `{staging_result.get('evidence_sufficient', '')}`",
    ]
    failed_step = str(staging_result.get("failed_step", "") or "")
    if failed_step:
        lines.append(f"- Failed step: `{failed_step}`")
    evidence_gap = str(staging_result.get("evidence_gap", "") or "")
    if evidence_gap:
        lines.append(f"- Evidence gap: {evidence_gap}")
    lines.extend(["", "## Command Results", ""])
    for raw_step in staging_result.get("steps", []):
        step = dict(raw_step)
        lines.extend(
            [
                f"### {step.get('label', '')}",
                "",
                f"- Exit code: `{step.get('exit_code', '')}`",
                f"- Timeout: `{step.get('timeout', '')}` seconds",
                f"- Fatal pattern: {step.get('fatal_pattern', '') or '(none)'}",
                f"- Stdout: {step.get('stdout', '') or '(empty)'}",
                f"- Stderr: {step.get('stderr', '') or '(empty)'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


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
