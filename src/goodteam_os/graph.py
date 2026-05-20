from pathlib import Path

from goodteam_os.artifacts import ArtifactStore
from goodteam_os.memory import ProjectMemoryStore, summarize_markdown
from goodteam_os.nodes.review_gate import gate_next_node
from goodteam_os.projects import load_project_target
from goodteam_os.state import AgileDevState


CONTEXT_FILES = {
    "docs": [
        ("codex.md", "Shared long-term memory"),
        ("docs/ai-collaboration.md", "AI collaboration and ownership rules"),
        ("docs/plane-handoff.md", "Plane handoff and tracking rules"),
        ("docs/deployment-current.md", "Current deployment and smoke checks"),
    ],
    "backend": [
        ("README.md", "Backend overview"),
        ("docs/frontend-auth-contract.md", "Frontend-facing auth contract"),
        ("docs/api.md", "Backend API contract"),
    ],
    "frontend": [
        ("GEMINI.md", "Frontend agent conventions"),
        ("README.md", "Frontend overview"),
        ("docs/backend-auth-contract.md", "Frontend mirror of backend auth contract"),
    ],
}


def collect_project_context(project_config: Path = Path("projects/eastern-birth-chart.local.yaml")) -> list[dict[str, str]]:
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
    for repo_name, files in CONTEXT_FILES.items():
        repo_root = repos.get(repo_name)
        if not repo_root:
            continue
        for relative_path, purpose in files:
            path = Path(str(repo_root)) / relative_path
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8")
            packets.append(
                {
                    "repo": repo_name,
                    "path": str(path),
                    "purpose": purpose,
                    "summary": summarize_context_file(content),
                }
            )
    return packets


def select_project_memory_root(repos: dict[object, object]) -> Path | None:
    for name in ["docs", "backend", "frontend"]:
        value = repos.get(name)
        if value and Path(str(value)).is_dir():
            return Path(str(value))
    return None


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


def summarize_context_file(content: str) -> str:
    return summarize_markdown(content)


def render_prd_markdown(
    state: AgileDevState,
    project_config: Path = Path("projects/eastern-birth-chart.local.yaml"),
) -> str:
    context = collect_project_context(project_config)
    context_sources = "\n".join(
        f"- `{item['path']}` ({item['purpose']}): {item['summary']}"
        for item in context
    )
    if not context_sources:
        context_sources = "- No project context files were loaded. This PRD must be treated as incomplete."

    project_label = state.project_key or "the configured project"
    return f"""# PRD

## Background

{state.idea}

## Project Context Read

{context_sources}

## Current Project Understanding

- This PRD was generated from the configured local context packet for `{project_label}`.
- The source list above is the durable evidence for this PRD; reviewer feedback should name missing, stale, or contradictory files.
- Ownership, deployment, auth/session, API contract, and ticketing assumptions must be grounded in the listed source files.
- Cross-repository changes require explicit ownership and handoff notes before implementation.
- Secrets, local credentials, generated state, and run artifacts must remain local-only.

## Goals

- Produce a real takeover artifact based on the configured docs/backend/frontend repositories.
- Identify project ownership boundaries, deployment constraints, auth/API contracts, and ticketing discipline before any code work.
- Propose next Plane-ready work items only after the source docs and current repo state have been inspected.
- Keep the current run read-only for product/requirement review; no product code changes are allowed.

## Non-goals

- Do not modify product code during the product/requirement phase.
- Do not change API contracts, database schema, or UI implementation before RFC approval.
- Do not create release or deployment actions from this PRD alone.
- Do not bypass the configured ticketing/project tracking flow for backend, frontend, deploy, docs, or cross-team work.
- Do not paste or persist secrets in artifacts, Plane, git docs, or chat.

## Users / Stakeholders

- Product owner: reviews scope, goals, and acceptance criteria.
- Engineering roles: use the approved PRD as input for RFC, planning, UI, FE, BE, QA, and release.
- Human reviewer: approves, requests changes, or rejects the PRD at the current gate.
- Backend AI: owns backend/API/deploy/auth contract work.
- Frontend AI: owns frontend/UI/RPC client behavior.
- Planner: converts approved takeover findings into Plane tickets.

## User Stories

- As the product owner, I want GoodTeam to read the configured project docs before proposing next work so review is grounded in project reality.
- As backend owner, I want backend/frontend boundaries preserved so agents do not edit the wrong repository.
- As frontend owner, I want API/auth contract assumptions called out before UI work depends on them.
- As planner, I want takeover findings converted into 0.5-2 day tickets with explicit inputs, outputs, and acceptance criteria.
- As reviewer, I want visible source evidence so I can challenge missing docs, stale docs, or bad conclusions.

## Functional Requirements

- Read the configured docs/backend/frontend repository paths from the project target.
- Include exact source files read in the PRD.
- Summarize ownership, Plane, deployment, auth/API, and frontend/backend sync constraints.
- Preserve artifact paths and state paths for workflow recovery.
- Produce enough context for a follow-up takeover report and Plane ticket proposal.

## Acceptance Criteria

- PRD lists the actual context files read from local configured repos.
- PRD states backend/frontend/docs ownership boundaries.
- PRD states ticket-first and cross-team handoff requirements when source docs define them.
- PRD states deployment entrypoints and smoke-check expectations when source docs define them.
- PRD states auth/session and frontend API constraints at a reviewable level when source docs define them.
- Reviewer can point to missing or stale source documents and request changes.
- If approved, the next Architecture step must generate RFC/takeover artifacts from this context, not from chat memory.

## Success Metrics

- Reviewer can tell which source files influenced the PRD.
- Reviewer can give concrete feedback on missing source context.
- Architect can produce an RFC/takeover design without rescanning unrelated files.
- Planner can create Plane ticket proposals tied to the takeover findings.
- Future runs can recover from `state/current.json` and persisted artifacts.

## Edge Cases

- Existing docs contradict repo implementation.
- Ticket comments contain newer decisions than git docs.
- Backend/frontend auth contract mirrors drift.
- Dev deployment state differs from `deployment-current.md`.
- Worker CLIs are logged in locally but not invocable from the orchestrator process.
- Human reviewer requests changes after approval or after downstream artifacts have been generated.

## Risks

- Docs may be stale; architecture must verify against repo state before implementation.
- PRD approval without a real takeover report can still push ambiguity downstream.
- Creating Plane tickets without reading current ticket comments can duplicate work.
- Cross-editing FE/BE repos without explicit ownership can break team boundaries.
- Deployment or auth changes can affect frontend behavior and require sync labels/comments.

## Open Questions

- Should the next step be a takeover report before RFC, or should RFC include takeover findings?
- Which tickets are currently active and must be linked before task planning?
- Are docs or ticket comments considered authoritative when they conflict?
- Should GoodTeam create new Plane tickets automatically or first produce ticket proposals for review?
- Which repos may GoodTeam modify in this run, if any?

## Reviewer Feedback Prompts

- Are the listed context files sufficient for taking over `{project_label}`?
- Which source document is missing or stale?
- Are the backend/frontend ownership boundaries correct?
- Should the next artifact be a takeover report, RFC, or Plane ticket proposal?
- Should the workflow remain read-only until a human approves ticket creation?
"""


def render_takeover_context_markdown(
    state: AgileDevState,
    project_config: Path = Path("projects/eastern-birth-chart.local.yaml"),
) -> str:
    context = collect_project_context(project_config)
    context_sources = "\n".join(
        f"- `{item['path']}` ({item['purpose']}): {item['summary']}"
        for item in context
    )
    if not context_sources:
        context_sources = "- No project context files were loaded. Check the project config before approving takeover."

    return f"""# Takeover Context

## Goal

{state.idea}

## Project

- Key: `{state.project_key}`
- Source type: existing project
- Project config: `{project_config}`

## Context Files Read

{context_sources}

## Takeover Checklist

- Confirm docs/backend/frontend repository paths are correct.
- Confirm durable docs and ticketing rules are current.
- Confirm backend/frontend ownership boundaries before dispatching workers.
- Confirm deployment, auth/session, API contract, and smoke-test facts from source files.
- Confirm generated artifacts, state, local configs, credentials, and run history stay local-only unless explicitly exported.

## Findings To Review

- Missing context files must be called out before PRD generation.
- Stale or contradictory docs must be resolved before RFC generation.
- Existing tickets and repo state should be read before task planning.
- Any product-code changes require explicit downstream tickets and role ownership.

## Recommended Next Step

Approve this takeover context only if the listed sources are enough for a grounded PRD. Request changes if more docs, tickets, or repository checks are required first.
"""


def run_adoption_until_next_gate(
    state: AgileDevState,
    workspace: Path,
    project_config: Path = Path("projects/eastern-birth-chart.local.yaml"),
) -> AgileDevState:
    store = ArtifactStore(root=workspace)
    memory_path = ensure_project_memory(project_config)
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
    state.artifacts["takeover"] = str(takeover_path)
    state.approvals["takeover"] = "pending"
    state.current_node = "takeover_review_gate"
    state.next_node = gate_next_node(state, gate="takeover", approved_next="product_requirement")
    return state


def run_until_next_gate(
    state: AgileDevState,
    workspace: Path,
    project_config: Path = Path("projects/eastern-birth-chart.local.yaml"),
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
