from pathlib import Path

from goodteam_os.graph import collect_project_context, render_prd_markdown, run_until_next_gate
from goodteam_os.state import create_initial_state


def test_graph_reaches_prd_gate(tmp_path):
    state = create_initial_state("run-1", "GTO", "Build AI Team OS")

    result = run_until_next_gate(state=state, workspace=tmp_path)

    assert result.current_node == "prd_review_gate"
    assert result.next_node == "await_prd_approval"
    assert "prd" in result.artifacts


def write_project_fixture(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    for repo in [docs, backend, frontend]:
        repo.mkdir()

    (docs / "codex.md").write_text("# Shared Memory\n- Ticket-first handoff.\n", encoding="utf-8")
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


def test_prd_includes_project_context_sections(tmp_path):
    project_config = write_project_fixture(tmp_path)
    state = create_initial_state("run-1", "SAMPLE", "Take over the configured product")

    prd = render_prd_markdown(state, project_config)

    assert "## Project Context Read" in prd
    assert "## Current Project Understanding" in prd
    assert "codex.md" in prd
    assert "Backend overview" in prd
    assert "Secrets, local credentials, generated state, and run artifacts must remain local-only." in prd


def test_collect_project_context_reads_configured_sources(tmp_path):
    project_config = write_project_fixture(tmp_path)
    context = collect_project_context(project_config)
    paths = [item["path"] for item in context]

    assert any(path.endswith("docs/codex.md") for path in paths)
    assert any(path.endswith("backend/README.md") for path in paths)
    assert any(path.endswith("frontend/README.md") for path in paths)
