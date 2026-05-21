from pathlib import Path

from goodteam_os.artifacts import ArtifactStore
from goodteam_os.memory import ProjectMemoryStore, RoleMemoryStore, summarize_markdown
from goodteam_os.nodes.review_gate import gate_next_node
from goodteam_os.projects import load_project_target
from goodteam_os.roles import artifact_contract, context_file_specs, list_role_definitions, role_definition
from goodteam_os.state import AgileDevState
from goodteam_os.tasks import LocalTaskStore


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
    project_config: Path = Path("projects/eastern-birth-chart.local.yaml"),
) -> str:
    return render_artifact_markdown("prd", state, project_config)


def render_takeover_context_markdown(
    state: AgileDevState,
    project_config: Path = Path("projects/eastern-birth-chart.local.yaml"),
) -> str:
    return render_artifact_markdown("takeover", state, project_config)


def render_artifact_markdown(
    artifact_key: str,
    state: AgileDevState,
    project_config: Path = Path("projects/eastern-birth-chart.local.yaml"),
) -> str:
    contract = artifact_contract(artifact_key)
    role = role_definition(str(contract["role"]))
    target = load_project_target(project_config) if project_config.is_file() else {}
    project_name = str(target.get("name", "") or state.project_key or "Configured Project")
    project_key = str(target.get("key", "") or state.project_key)
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
        f"- Project config: `{project_config}`",
        "",
        "## Project Context Read",
        "",
        context_sources,
    ]
    for section in contract.get("sections", []):
        lines.extend(["", f"## {section['title']}", ""])
        for item in section.get("items", []):
            lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def run_adoption_until_next_gate(
    state: AgileDevState,
    workspace: Path,
    project_config: Path = Path("projects/eastern-birth-chart.local.yaml"),
) -> AgileDevState:
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
