"""[INPUT]: 项目配置、角色定义和 Worker 证据。
[OUTPUT]: 项目上下文与产物文本。
[POS]: 供项目扫描、历史产物和审查读取。
[PROTOCOL]: Update this header on change, then check AGENTS.md
"""
import json
import os
import re
from pathlib import Path

from goodteam_os import project_runtime
from goodteam_os.memory import ProjectMemoryStore, summarize_markdown
from goodteam_os.projects import load_project_target
from goodteam_os.roles import (
    artifact_contract,
    context_file_specs,
    role_definition,
)
from goodteam_os.state import AgileDevState


# Bounds for single-root `docs/` context gathering. The cap is generous so
# doc-heavy repos (GoodTeam's own docs/ is 40+ files) are not silently
# truncated, while the byte budget keeps a pathological repo from blowing up
# the context packet.
MAX_CONTEXT_DOC_FILES = 200
MAX_CONTEXT_DOC_BYTES = 2_000_000

# Root-level instruction / agent-guidance files (matched case-insensitively).
# These are higher-signal than design docs, so they are loaded before docs/.
ROOT_INSTRUCTION_FILES = (
    "CLAUDE.md",
    "AGENTS.md",
    "AGENT.md",
    "CONTRIBUTING.md",
    "ARCHITECTURE.md",
    "CONTEXT.md",
    "GOODTEAM.md",
)

GENERATED_TAKEOVER_DOC_NAMES = {
    "takeover.md",
    "takeover-context.md",
}


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
    "static_site": [
        "Deliver a static site or landing page with a buildable artifact and local preview path.",
        "Define content structure, routes or sections, responsive behavior, and basic accessibility expectations.",
        "Include deployment notes, README verification commands, and any asset or SEO assumptions.",
    ],
    "cli_tool": [
        "Deliver a command-line tool with explicit argument and flag contracts, help text, and documented exit codes.",
        "Include deterministic tests for command behavior, errors, and edge cases.",
        "Document installation, local run commands, and example invocations in the README.",
    ],
    "library": [
        "Deliver a library or SDK with a clear public API, semantic versioning expectations, and usage documentation.",
        "Include unit tests for exported behavior and examples that do not assume a host application.",
        "Document packaging, compatibility assumptions, and extension points.",
    ],
    "data_pipeline": [
        "Deliver an idempotent data pipeline with explicit stage boundaries and input/output schemas or contracts.",
        "Include sample data that can run locally, reproducible backfill guidance, and deterministic tests.",
        "Document retry behavior, failure handling, and operational verification commands.",
    ],
    "mobile_app": [
        "Deliver a mobile app for the target platform with documented build, run, and test commands.",
        "Define navigation, state management, offline or error states, and platform assumptions.",
        "Include basic automated or manual verification evidence for core user flows.",
    ],
    "feature_increment": [
        "Deliver an incremental feature that integrates with existing contracts, data, and user flows.",
        "Preserve existing behavior, add focused regression tests, and document any compatibility risks.",
        "Include rollout, rollback, or feature-flag notes when the change affects production paths.",
    ],
}


def extract_open_questions(content: str) -> list[str]:
    questions: list[str] = []
    in_section = False
    heading_re = re.compile(r"^\s{0,3}#{1,6}\s+(?P<text>.+?)\s*#*\s*$")
    bullet_re = re.compile(r"^\s*(?:[-*]\s+|\d+[.)]\s+)(?P<text>.+?)\s*$")

    for line in content.splitlines():
        heading = heading_re.match(line)
        if heading:
            heading_text = heading.group("text").strip()
            normalized_heading = heading_text.lower()
            in_section = (
                "open questions" in normalized_heading
                or "questions for you" in normalized_heading
                or "需要你确认的问题" in heading_text
            )
            continue
        if not in_section:
            continue

        item = bullet_re.match(line)
        if not item:
            continue
        question = item.group("text").strip()
        if question:
            questions.append(question)

    return questions


def delivery_standards_for_mode(mode: str, target: dict[str, object]) -> list[str]:
    normalized = normalize_delivery_mode(mode)
    if normalized == "custom":
        configured = target.get("delivery_standards", {})
        if isinstance(configured, dict):
            return [
                str(value).strip()
                for _, value in sorted(configured.items())
                if str(value).strip()
            ]
        if isinstance(configured, list):
            return [str(value).strip() for value in configured if str(value).strip()]
        if isinstance(configured, str):
            return [line.strip() for line in configured.splitlines() if line.strip()]
        return []
    return DELIVERY_MODE_STANDARDS.get(normalized, DELIVERY_MODE_STANDARDS["init_project"])


def delivery_standards_for_state(
    state: AgileDevState,
    mode: str,
    target: dict[str, object],
) -> list[str]:
    if mode == "custom" and state.delivery_standards:
        return state.delivery_standards
    return delivery_standards_for_mode(mode, target)


def collect_project_context(
    project_config: Path | None = None, state: AgileDevState | None = None,
) -> list[dict[str, str]]:
    if project_config is None:
        return []
    if not project_config.is_file():
        return []

    target = load_project_target(project_config)
    repos = dict(target.get("repos", {}))
    if state is not None and state.repository_baseline:
        from goodteam_os.workflow_workspace import WorkflowWorkspaceError, workspace_for_workflow

        source = Path(str(state.repository_baseline.get("source_root", "")))
        retained = workspace_for_workflow(source, state.repository_baseline,
                                           project_key=state.project_key, run_id=state.run_id)
        source_path = Path(os.path.abspath(source))
        mapped = {}
        for name, value in repos.items():
            if not value:
                mapped[name] = value
                continue
            configured = Path(os.path.abspath(Path(str(value)).expanduser()))
            try:
                relative = configured.relative_to(source_path)
            except ValueError as exc:
                raise WorkflowWorkspaceError("Workflow context repository is outside its retained task baseline.") from exc
            mapped[name] = retained / relative
        repos = mapped
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
    single_root = single_root_context_root(repos)
    if single_root is not None:
        from goodteam_os.context import read_context_entry

        entry = read_context_entry(single_root)
        if entry is not None:
            packets.append(
                {
                    "repo": "goodteam_context",
                    "path": str((single_root / ".goodteam" / "context" / "understanding.md").resolve()),
                    "purpose": "GoodTeam project understanding",
                    "summary": str(entry.get("understanding", "")),
                }
            )
        packets.extend(discover_single_root_context(single_root))
        return packets
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


def single_root_context_root(repos: dict[object, object]) -> Path | None:
    if len(repos) != 1:
        return None
    raw_root = next(iter(repos.values()), "")
    if not raw_root:
        return None
    root = Path(str(raw_root))
    return root.resolve() if root.is_dir() else None


def discover_single_root_context(root: Path) -> list[dict[str, str]]:
    packets: list[dict[str, str]] = []
    seen: set[Path] = set()
    for path, purpose in single_root_context_files(root):
        try:
            resolved = path.resolve()
            if resolved in seen or not resolved.is_file():
                continue
            content = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, RuntimeError, ValueError):
            continue
        seen.add(resolved)
        packets.append(
            {
                "repo": "root",
                "path": str(resolved),
                "purpose": purpose,
                "summary": summarize_context_path(resolved, content),
            }
        )
    tree_summary = render_shallow_directory_tree(root)
    if tree_summary:
        packets.append(
            {
                "repo": "root",
                "path": str(root),
                "purpose": "Shallow project directory overview",
                "summary": tree_summary,
            }
        )
    return packets


def single_root_context_files(root: Path) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for path in sorted(root.glob("README*"), key=lambda item: item.name.lower()):
        if path.is_file():
            files.append((path, "Top-level project README"))
    wanted_instructions = {name.lower() for name in ROOT_INSTRUCTION_FILES}
    instruction_files = [
        path
        for path in root.iterdir()
        if path.is_file() and path.name.lower() in wanted_instructions
    ]
    for path in sorted(instruction_files, key=lambda item: item.name.lower()):
        files.append((path, "Project instructions / agent guidance"))
    manifest_names = {
        "package.json",
        "pyproject.toml",
        "go.mod",
        "Cargo.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements.in",
        "Gemfile",
        "composer.json",
        "pom.xml",
        "build.gradle",
        "settings.gradle",
    }
    for path in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if path.is_file() and (path.name in manifest_names or re.fullmatch(r"requirements[-\w]*\.txt", path.name)):
            files.append((path, "Project manifest or dependency file"))
    workflows_root = root / ".github" / "workflows"
    if workflows_root.is_dir():
        for path in sorted(workflows_root.iterdir(), key=lambda item: item.name.lower()):
            if path.is_file() and path.suffix.lower() in {".yml", ".yaml"}:
                files.append((path, "CI workflow"))
    for path in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        lowered = path.name.lower()
        if path.is_file() and (
            lowered.startswith("docker-compose")
            and path.suffix.lower() in {".yml", ".yaml"}
        ):
            files.append((path, "Deployment manifest"))
        if path.is_file() and lowered.startswith("dockerfile"):
            files.append((path, "Container build definition"))
    for pattern in ("*/Dockerfile*", "*/*/Dockerfile*"):
        for path in sorted(root.glob(pattern), key=lambda item: str(item).lower()):
            if path.is_file():
                files.append((path, "Container build definition"))
    docs_root = root / "docs"
    if docs_root.is_dir():
        docs = [
            path
            for path in docs_root.rglob("*")
            if path.is_file()
            and len(path.relative_to(docs_root).parts) <= 2
            and path.name.lower() not in GENERATED_TAKEOVER_DOC_NAMES
        ]
        doc_bytes = 0
        for path in sorted(docs, key=lambda item: str(item.relative_to(docs_root)).lower())[:MAX_CONTEXT_DOC_FILES]:
            try:
                doc_bytes += path.stat().st_size
            except OSError:
                continue
            if doc_bytes > MAX_CONTEXT_DOC_BYTES:
                break
            files.append((path, "Project documentation"))
    return files


def render_shallow_directory_tree(root: Path, *, max_entries: int = 80) -> str:
    ignored = {".git", ".goodteam", ".venv", "__pycache__", "node_modules", "dist", "build", ".next"}
    lines = [f"{root.name}/"]
    count = 0
    try:
        top_level = sorted(root.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return ""
    for path in top_level:
        if count >= max_entries:
            lines.append("- ...")
            break
        if path.name in ignored or path.name.startswith("."):
            continue
        suffix = "/" if path.is_dir() else ""
        lines.append(f"- {path.name}{suffix}")
        count += 1
        if not path.is_dir():
            continue
        try:
            children = sorted(path.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            continue
        for child in children[:8]:
            if count >= max_entries:
                break
            if child.name in ignored or child.name.startswith("."):
                continue
            if child.name.lower() in GENERATED_TAKEOVER_DOC_NAMES:
                continue
            child_suffix = "/" if child.is_dir() else ""
            lines.append(f"  - {child.name}{child_suffix}")
            count += 1
    return "\n".join(lines)


def select_project_memory_root(repos: dict[object, object]) -> Path | None:
    runtime_root = project_runtime.resolve_project_runtime_root(repos)
    if runtime_root is not None:
        return runtime_root
    for name in ["docs", "backend", "frontend"]:
        value = repos.get(name)
        if value and Path(str(value)).is_dir():
            return Path(str(value))
    return None


def summarize_context_file(content: str) -> str:
    return summarize_markdown(content)


def summarize_context_path(path: Path, content: str) -> str:
    if path.parent.name == "workflows" and path.parent.parent.name == ".github":
        workflow_name = next(
            (
                match.group(1).strip()
                for line in content.splitlines()
                if (match := re.match(r"^name:\s*(.+?)\s*$", line))
            ),
            path.stem,
        )
        triggers = sorted(
            {
                match.group(1)
                for line in content.splitlines()
                if (match := re.match(r"^\s{2}(workflow_dispatch|push|pull_request|schedule):", line))
            }
        )
        steps = [
            match.group(1).strip()
            for line in content.splitlines()
            if (match := re.match(r"^\s*-\s+name:\s*(.+?)\s*$", line))
        ]
        secret_names = sorted(set(re.findall(r"secrets\.([A-Z][A-Z0-9_]*)", content)))
        variable_names = sorted(set(re.findall(r"vars\.([A-Z][A-Z0-9_]*)", content)))
        details = [f"Workflow: {workflow_name}"]
        if triggers:
            trigger_text = ", ".join(triggers)
            if triggers == ["workflow_dispatch"]:
                trigger_text += " (manual only)"
            details.append(f"Triggers: {trigger_text}")
        if steps:
            details.append("Steps: " + ", ".join(steps))
        if secret_names:
            details.append("Secret names: " + ", ".join(secret_names))
        if variable_names:
            details.append("Variable names: " + ", ".join(variable_names))
        return " | ".join(details)

    lowered_name = path.name.lower()
    if lowered_name.startswith("docker-compose") and path.suffix.lower() in {".yml", ".yaml"}:
        summary = summarize_context_file(content)
        sensitive_keys = sorted(
            {
                match.group(1)
                for line in content.splitlines()
                if (
                    match := re.match(
                        r"^\s*(MYSQL_ROOT_PASSWORD|MYSQL_PASSWORD|DATABASE_PASSWORD|PASSWORD|SECRET|TOKEN):\s*(.+?)\s*$",
                        line,
                    )
                )
                and match.group(2)
                and not match.group(2).lstrip().startswith(("${", "$"))
            }
        )
        if sensitive_keys:
            warning = "Warning: static sensitive-looking values detected for " + ", ".join(sensitive_keys)
            return f"{summary} | {warning}" if summary else warning
        return summary

    if path.name == "package.json":
        try:
            manifest = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return summarize_context_file(content)
        details = []
        package_name = str(manifest.get("name", "")).strip()
        if package_name:
            details.append(f"Package: {package_name}")
        package_manager = str(manifest.get("packageManager", "")).strip()
        if package_manager:
            details.append(f"Package manager: {package_manager}")
        scripts = manifest.get("scripts", {})
        if isinstance(scripts, dict) and scripts:
            details.append("Scripts: " + ", ".join(sorted(str(key) for key in scripts)))
        engines = manifest.get("engines", {})
        if isinstance(engines, dict) and engines:
            details.append(
                "Engines: "
                + ", ".join(f"{key} {value}" for key, value in sorted(engines.items()))
            )
        if details:
            return " | ".join(details)
    return summarize_context_file(content)


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
    delivery_standards = delivery_standards_for_state(state, delivery_mode, target)
    context = collect_project_context(project_config, state)
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
        *[f"- {item}" for item in delivery_standards],
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
            if artifact_key == "implementation_handoff" and section.get("title") == "Execution Evidence":
                # The downstream renderer appends observed status and the
                # worker's sanitized report below. Do not leave a template
                # instruction that can be mistaken for the evidence itself.
                continue
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
    delivery_standards = delivery_standards_for_state(state, delivery_mode, target)
    context = collect_project_context(project_config, state)
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
    security_assessment = [
        "Authentication and authorization boundaries: verify sensitive endpoints require explicit role checks and session validation.",
        "Data validation and injection defense: enforce parameterized SQL queries, payload schema validation, and input sanitization.",
        "Secrets and credential posture: ensure no tokens, API keys, or private configs are exposed to client bundles or git logs.",
        "Attack surface and third-party dependencies: audit external packages, enforce sandbox limits, and pin trusted versions.",
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
        *[f"- {item}" for item in delivery_standards],
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
        "## Security Assessment & Risk Posture",
        "",
        *[f"- {item}" for item in security_assessment],
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


def format_context_sources(
    context: list[dict[str, str]],
    *,
    summary_limit: int | None = None,
) -> str:
    def displayed_summary(item: dict[str, str]) -> str:
        summary = item["summary"]
        if summary_limit is None or len(summary) <= summary_limit:
            return summary
        return summary[:summary_limit].rstrip() + "…"

    context_sources = "\n".join(
        f"- `{item['path']}` ({item['purpose']}): {displayed_summary(item)}"
        for item in context
    )
    if not context_sources:
        context_sources = "- No project context files were loaded. Treat this artifact as incomplete."
    return context_sources


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
                lines.append(
                    f"- {_worker_artifact_text(result.get('role', 'unknown'))} / "
                    f"{_worker_artifact_text(result.get('adapter', 'unknown'))}: "
                    f"{_worker_artifact_text(result.get('status', 'unknown'))} - "
                    f"{_worker_artifact_text(result.get('summary', ''))}"
                )
        else:
            lines.extend(["", "## Worker Job Results", "", "- No worker jobs were executed."])
        lines.extend(["", "## Execution Evidence", ""])
        lines.extend(format_worker_execution_evidence_lines(results))
    if artifact_key == "qa_report":
        lines.extend(
            [
                "",
                "## Verification Matrix",
                "",
                *format_worker_evidence_lines(results),
            ]
        )
        lines.extend(["", "## Worker Reports", ""])
        lines.extend(format_worker_execution_evidence_lines(results))
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
                "- Release recommendation: pending human review of the exact artifacts and verification evidence.",
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
                "- Rollback is unverified unless the project-specific delivery evidence documents and validates it. Do not reset databases or replace project directories based on this summary.",
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
        role = _worker_artifact_text(result.get("role", "unknown"))
        status = _worker_artifact_text(result.get("status", "unknown"))
        summary = _worker_artifact_text(result.get("summary", ""))
        model = _worker_artifact_text(result.get("model", "") or "")
        result_text = read_worker_result_excerpt(result)
        lines.append(f"- {role}: `{status}` via `{model or 'unspecified model'}` - {summary}")
        for signal in extract_worker_signals(result_text):
            lines.append(f"  - Evidence: {signal}")
    return lines


def format_worker_execution_evidence_lines(worker_results: list[dict[str, object]]) -> list[str]:
    """Render bounded, attributable worker reports into handoff artifacts.

    The worker status summary is deliberately small and does not contain the
    implementation's changed-file or command evidence. Keep the report
    excerpt separate from the status fields so reviewers can distinguish what
    GoodTeam observed from what the worker claimed.
    """
    if not worker_results:
        return ["- No worker execution evidence was attached to this artifact."]

    lines: list[str] = []
    for result in worker_results:
        role = _worker_artifact_text(result.get("role", "unknown"))
        adapter = _worker_artifact_text(result.get("adapter", "unknown"))
        status = _worker_artifact_text(result.get("status", "unknown"))
        lines.extend([
            f"### {role} / {adapter}",
            "",
            f"- Observed status: `{status}`.",
            f"- Observed summary: {_worker_artifact_text(result.get('summary', '')).strip() or 'none reported'}",
        ])
        result_path = _worker_artifact_text(result.get("result_path", "")).strip()
        if result_path:
            lines.append(f"- Worker report path: `{result_path}`.")
        repository_result = result.get("repository_result")
        if isinstance(repository_result, dict):
            lines.extend([
                f"- Retained task cwd: `{_worker_artifact_text(repository_result.get('workspace_path', ''))}`.",
                f"- Task baseline commit: `{_worker_artifact_text(repository_result.get('baseline_commit', ''))}`.",
                f"- Cumulative task result commit: `{_worker_artifact_text(repository_result.get('result_commit', ''))}`.",
                "- Repository changes are cumulative for this task; individual Worker authorship is not inferred.",
            ])
        for field, label in (("changed_files", "Changed files reported"), ("artifacts", "Artifacts reported")):
            values = result.get(field)
            if isinstance(values, list) and values:
                lines.append(f"- {label}:")
                lines.extend(f"  - `{_worker_artifact_text(item)}`" for item in values)
            else:
                lines.append(f"- {label}: none in the worker status record.")

        excerpt = read_worker_result_excerpt(result, max_chars=6000).strip()
        if excerpt:
            lines.extend(["", "#### Worker report (sanitized excerpt)", ""])
            # Quote every line so worker-authored headings cannot be mistaken
            # for GoodTeam-owned evidence sections.
            lines.extend(f"> {line}" if line else ">" for line in excerpt.splitlines())
        else:
            lines.extend(["", "- Worker report content is unavailable."])
        lines.append("")
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
        "- Agent process status does not establish that QA or acceptance passed.",
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
        "- Agent process completion is not a test result or acceptance evidence.",
    ]
    from urllib.parse import urlencode

    qa_items = [item for item in state.delivery_items.values() if item.get("kind") == "qa"]
    if not qa_items:
        lines.append("- QA outcome: unknown. No exact QA report has been retained for this task result.")
    for key, item in state.delivery_items.items():
        lines.extend([
            f"- Retained {item['kind']}: `{_worker_artifact_text(item['relative_path'])}`",
            f"  - Baseline: `{item['baseline_commit']}`; result: `{item['result_commit']}`.",
        ])
        if item.get("kind") == "qa":
            lines.append(f"  - QA outcome (Worker-reported): `{item.get('qa_status', 'unknown')}`; "
                         f"process: `{item.get('process_status', 'unknown')}`; job: `{item.get('job_id', '')}`.")
        if item.get("status") == "available":
            query = urlencode({"project": state.project_key, "run": state.run_id,
                               "key": key, "version": item["version"], "view": "text"})
            lines.append(f"  - Content digest: `{item['version']}`; [open retained text](/api/delivery-artifact?{query}).")
        else:
            lines.append("  - Content unavailable; inspect the retained task evidence before deciding.")
    qa_results = [result for result in worker_results if result.get("role") == "qa"]
    if qa_results:
        lines.extend(["", "### QA command evidence and report (Worker-authored)", ""])
        lines.extend(format_worker_execution_evidence_lines(qa_results))
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
    for item in state.delivery_items.values():
        if item.get("kind") == "qa" and item.get("qa_status") in {"failed", "blocked", "unknown"}:
            caveats.append(f"- Worker-reported QA outcome: `{item['qa_status']}`. Read the retained QA report and exact command results before approval.")
    if "better-sqlite3" in combined:
        caveats.append("- Native module caveat: `better-sqlite3` can require `npm rebuild better-sqlite3` after Node runtime changes.")
    if "no real-browser interactive e2e" in combined or "accessibility" in combined:
        caveats.append("- QA caveat: API/data and component coverage are stronger than full browser interaction/accessibility coverage.")
    if state.blockers:
        caveats.append(f"- Blockers remain: {'; '.join(state.blockers)}")
    if not caveats:
        caveats.append("- Caveats are not exhaustively assessed by this summary. Inspect the attached Worker reports and verification gaps before approval.")
    return caveats


def read_worker_result_excerpt(result: dict[str, object], *, max_chars: int = 3000) -> str:
    result_path = result.get("result_path")
    if not result_path:
        return ""
    path = Path(str(result_path))
    if not path.is_file():
        return ""
    return sanitize_artifact_excerpt(path.read_text(encoding="utf-8", errors="replace"), max_chars=max_chars)


def _worker_artifact_text(value: object) -> str:
    # Sanitize each metadata value before adding Markdown, so an authorization
    # record cannot swallow a later, independently supplied file/evidence item.
    return sanitize_artifact_excerpt(str(value), max_chars=None)


def sanitize_artifact_excerpt(text: str, *, max_chars: int | None = 3000) -> str:
    # Reports are free-form text, not a parsed header map. Redact the complete
    # authorization record, including quoted/escaped keys, multi-part schemes
    # and HTTP folded continuation lines. Guessing the end from a space/comma
    # would retain Bearer credentials or Digest parameters. Adjacent indented
    # lines are deliberately treated as part of that sensitive record.
    authorization = re.compile(
        r"(?im)(?<![\w-])((?:__)?(?:proxy[-_]|http_)?authorization(?:__)?(?![\w-])"
        r"(?:[\\\"'`*\]\)]|[^\S\r\n])*[:=,|>])"
        r"(?:[^\S\r\n]*(?:(?:bearer|basic|digest|negotiate)[^\S\r\n]*)?"
        r"(?:\r\n|\n|\r)[^\r\n]*|[^\r\n]*)"
        r"(?:(?:\r\n|\n|\r)[ \t]+[^\r\n]*)*"
    )
    redacted = authorization.sub(r"\1 [redacted]", text)
    secretish = re.compile(
        r"(?i)(api[_-]?key|access[_-]?token|bot[_-]?token|authorization|secret|password)\s*[:=]\s*\S+"
    )
    redacted = secretish.sub(r"\1: [redacted]", redacted)
    return redacted if max_chars is None else redacted[:max_chars]


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


def normalize_delivery_mode(value: str) -> str:
    mode = (value or "init_project").strip()
    if mode == "custom":
        return mode
    if mode not in DELIVERY_MODE_STANDARDS:
        return "init_project"
    return mode


def worker_context(state: AgileDevState, *, recipient_role: str = "", workspace: Path | None = None) -> str:
    from goodteam_os.context_continuity import task_handoff_context

    artifacts = "\n".join(f"- {name}: {path}" for name, path in sorted(state.artifacts.items()))
    context = "\n".join(
        [
            f"Run: {state.run_id}",
            f"Project: {state.project_key}",
            f"Goal: {state.idea}",
            "Artifacts:",
            artifacts or "- none",
        ]
    )
    if state.delivery_mode == "custom":
        standards = "\n".join(f"- {item}" for item in state.delivery_standards)
        context += (
            "\nRecorded workflow delivery settings (source: workflow_state):\n"
            "Mode: custom\n"
            "These saved fields are not proof of a task-start capture; requested changes remain separate.\n"
            "Delivery standards:\n"
            + (standards or "- No custom standards recorded in this workflow state; do not invent them.")
        )
    if state.change_requests:
        changes = "\n".join(f"- {gate}: {note}" for gate, note in state.change_requests.items())
        context += f"\nRequested changes:\n{changes}"
    context += "\n\n" + task_handoff_context(state, recipient_role=recipient_role, workspace=workspace)
    return context


def _is_worker_meta_summary(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if not lines:
        return True
    first = lines[0].lower()
    if first.startswith("**changed files**") or first.startswith("changed files:") or first.startswith("## changed files"):
        return True
    if first.startswith("**verification**") or first.startswith("verification:"):
        return True
    return False
