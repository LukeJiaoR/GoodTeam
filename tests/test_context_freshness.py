"""Project iterations must not reuse understanding of changed source files."""
import os

from goodteam_os.context import load_or_generate_context, read_context_entry
from goodteam_os.graph import collect_project_context


def test_context_cache_invalidates_changed_sources_and_regenerates_from_current_files(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    readme = root / "README.md"
    readme.write_text("# Project\n\n- Runtime: Python\n- Verify: pytest\n")
    config = tmp_path / "project.yaml"
    config.write_text(f"key: DEMO\nrepos:\n  root: {root}\n")

    first = load_or_generate_context(project_config=config, root=root, generate=True)
    assert first["generated"] and "Verify: pytest" in first["entry"]["understanding"]
    assert load_or_generate_context(project_config=config, root=root)["source"] == "l1_cache"
    assert any(packet["repo"] == "goodteam_context" for packet in collect_project_context(config))
    cache = root / ".goodteam" / "context"
    retained = {path.name: path.read_bytes() for path in cache.iterdir()}

    before = readme.stat()
    readme.write_text("# Project\n\n- Runtime: NodeJS\n- Verify: vitest\n")
    os.utime(readme, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert readme.stat().st_size == before.st_size  # Content, not mtime/size, is the freshness source.
    stale = load_or_generate_context(project_config=config, root=root)
    assert stale["needs_generation"] and stale["entry"] is None
    assert read_context_entry(root) is None
    current = collect_project_context(config)
    assert not any(packet["repo"] == "goodteam_context" for packet in current)
    assert "vitest" in "\n".join(packet["summary"] for packet in current)
    assert {path.name: path.read_bytes() for path in cache.iterdir()} == retained

    regenerated = load_or_generate_context(project_config=config, root=root, generate=True)
    assert regenerated["generated"] and "Verify: vitest" in regenerated["entry"]["understanding"]
    assert "Verify: pytest" not in regenerated["entry"]["understanding"]
    assert load_or_generate_context(project_config=config, root=root)["source"] == "l1_cache"
