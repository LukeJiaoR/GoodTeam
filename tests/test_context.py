from pathlib import Path

from goodteam_os.graph import collect_project_context


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


def test_read_context_entry_returns_cached_entry(tmp_path):
    from goodteam_os.context import read_context_entry

    context_dir = tmp_path / ".goodteam" / "context"
    context_dir.mkdir(parents=True)
    (context_dir / "understanding.md").write_text("# Understanding\n\nCached.", encoding="utf-8")
    (context_dir / "index.md").write_text("- Topic -> README.md\n", encoding="utf-8")
    (context_dir / "sources.yaml").write_text(
        "generated_at: 2026-06-17T00:00:00Z\nsources:\n  - path: README.md\n",
        encoding="utf-8",
    )

    entry = read_context_entry(tmp_path)

    assert entry == {
        "understanding": "# Understanding\n\nCached.",
        "index": "- Topic -> README.md\n",
        "sources": {
            "generated_at": "2026-06-17T00:00:00Z",
            "sources": [{"path": "README.md"}],
        },
        "source": "l1_cache",
    }


def test_read_context_entry_returns_none_when_cache_missing(tmp_path):
    from goodteam_os.context import read_context_entry

    assert read_context_entry(tmp_path) is None


def test_estimate_context_tokens_counts_content_and_paths(tmp_path):
    from goodteam_os.context import estimate_context_tokens

    source_file = tmp_path / "README.md"
    source_file.write_text("abcdefgh", encoding="utf-8")

    estimate = estimate_context_tokens(
        [
            {"content": "abcd"},
            {"path": str(source_file)},
            {"path": str(tmp_path / "missing.md"), "content": "xy"},
        ]
    )

    assert estimate == {"bytes": 14, "est_tokens": 3, "source_count": 3}


def test_build_code_skeleton_returns_tree_and_signatures_without_bodies(tmp_path):
    from goodteam_os.context import build_code_skeleton

    package = tmp_path / "src" / "demo"
    package.mkdir(parents=True)
    (package / "app.py").write_text(
        "class Service:\n"
        "    def method(self):\n"
        "        return 'secret-body'\n"
        "\n"
        "def top_level(name):\n"
        "    return name\n",
        encoding="utf-8",
    )
    (tmp_path / "ui.ts").write_text(
        "export function renderApp() { return 'hidden'; }\n"
        "const local = 1;\n",
        encoding="utf-8",
    )

    skeleton = build_code_skeleton(tmp_path)

    assert tmp_path.name + "/" in skeleton
    assert "src/" in skeleton
    assert "app.py" in skeleton
    assert "class Service:" in skeleton
    assert "def top_level(name):" in skeleton
    assert "export function renderApp()" in skeleton
    assert "secret-body" not in skeleton
    assert "return name" not in skeleton


def test_generate_context_entry_writes_deterministic_cache(tmp_path):
    from goodteam_os.context import generate_context_entry, read_context_entry

    source = tmp_path / "README.md"
    source.write_text("# Demo\n\nA useful project.\n", encoding="utf-8")

    result = generate_context_entry(
        root=tmp_path,
        sources=[{"path": str(source), "purpose": "Top-level README", "summary": "Demo summary"}],
    )

    assert result["source"] == "generated"
    assert Path(result["understanding_path"]).is_file()
    assert Path(result["index_path"]).is_file()

    entry = read_context_entry(tmp_path)
    assert entry is not None
    assert "Top-level README" in entry["understanding"]
    assert "Demo summary" in entry["understanding"]
    assert "Top-level README" in entry["index"]
    assert str(source) in entry["index"]
    assert entry["sources"]["sources"][0]["path"] == str(source)


def test_load_or_generate_context_l1_hit_does_not_generate(tmp_path):
    from goodteam_os.context import load_or_generate_context

    context_dir = tmp_path / ".goodteam" / "context"
    context_dir.mkdir(parents=True)
    (context_dir / "understanding.md").write_text("Cached understanding.", encoding="utf-8")

    def fail_generator(prompt: str) -> str:
        raise AssertionError(f"unexpected generation: {prompt}")

    result = load_or_generate_context(root=tmp_path, project_config=None, generate=True, generator=fail_generator)

    assert result["entry"]["understanding"] == "Cached understanding."
    assert result["estimate"] is None
    assert result["generated"] is False
    assert result["source"] == "l1_cache"


def test_load_or_generate_context_estimates_without_writing_when_generate_false(tmp_path):
    from goodteam_os.context import load_or_generate_context

    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Demo\n\nContext seed.", encoding="utf-8")
    config = _write_single_root_config(tmp_path, project)

    result = load_or_generate_context(root=project, project_config=config, generate=False)

    assert result["entry"] is None
    assert result["generated"] is False
    assert result["needs_generation"] is True
    assert result["estimate"]["bytes"] > 0
    assert not (project / ".goodteam" / "context" / "understanding.md").exists()


def test_load_or_generate_context_generate_true_writes_cache(tmp_path):
    from goodteam_os.context import load_or_generate_context

    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Demo\n\nContext seed.", encoding="utf-8")
    config = _write_single_root_config(tmp_path, project)

    result = load_or_generate_context(root=project, project_config=config, generate=True)

    assert result["generated"] is True
    assert result["entry"]["source"] == "l1_cache"
    assert "Top-level project README" in result["entry"]["understanding"]
    assert (project / ".goodteam" / "context" / "understanding.md").is_file()


def test_collect_project_context_prepends_l1_entry_for_single_root(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Demo\n\nRoot overview.", encoding="utf-8")
    context_dir = project / ".goodteam" / "context"
    context_dir.mkdir(parents=True)
    (context_dir / "understanding.md").write_text("# Cached\n\nGoodTeam knows this.", encoding="utf-8")
    config = _write_single_root_config(tmp_path, project)

    context = collect_project_context(config)

    assert context[0]["repo"] == "goodteam_context"
    assert context[0]["purpose"] == "GoodTeam project understanding"
    assert context[0]["path"] == str((context_dir / "understanding.md").resolve())
    assert "GoodTeam knows this." in context[0]["summary"]
    assert any(item["path"] == str((project / "README.md").resolve()) for item in context[1:])
