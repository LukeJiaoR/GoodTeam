"""Unit tests for project-local Markdown memory (.goodteam/memory/*.md)."""
from __future__ import annotations

import pytest

from goodteam.integrations import memory as mem_store


def test_memory_list_write_append_read_search_and_summary(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()

    initial = mem_store.list_files(proj)
    assert [f["filename"] for f in initial] == ["project.md", "decisions.md", "lessons.md"]
    assert all(f["exists"] is False for f in initial)
    assert mem_store.read_file(proj, "project.md") == ""
    assert mem_store.summary_for_prompt(proj) == ""

    res1 = mem_store.write_file(proj, "project.md", "# Architecture\nSQLite + HTTP/SSE.", mode="replace")
    assert res1["filename"] == "project.md"
    assert res1["content"].endswith("\n")
    assert "SQLite" in mem_store.read_file(proj, "project.md")

    res2 = mem_store.write_file(proj, "project.md", "## Decisions\nUse worktrees.", mode="append")
    assert "# Architecture" in res2["content"]
    assert "Use worktrees." in res2["content"]

    mem_store.write_file(proj, "custom-notes.md", "Remember SSE flush on every event.", mode="replace")
    files = mem_store.list_files(proj)
    names = [f["filename"] for f in files]
    assert names == ["project.md", "decisions.md", "lessons.md", "custom-notes.md"]
    assert files[0]["exists"] is True and files[3]["exists"] is True

    hits = mem_store.search(proj, "worktrees")
    assert len(hits) == 1
    assert hits[0]["filename"] == "project.md"
    assert any("Use worktrees." in line for line in hits[0]["matches"])

    assert mem_store.search(proj, "   ") == []

    summary = mem_store.summary_for_prompt(proj, max_chars=2000)
    assert "### project.md" in summary
    assert "### custom-notes.md" in summary

    # Truncation boundary
    mem_store.write_file(proj, "lessons.md", "A" * 1000, mode="replace")
    short = mem_store.summary_for_prompt(proj, max_chars=220)
    assert "[truncated]" in short


def test_memory_rejects_traversal_and_invalid_inputs(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()

    for bad in ("", "../escape.md", "sub/file.md", ".hidden.md", "notes.txt", "UPPER.md", "a/b.md"):
        with pytest.raises(mem_store.MemoryError):
            mem_store.validate_filename(bad)
        with pytest.raises(mem_store.MemoryError):
            mem_store.write_file(proj, bad, "bad")

    with pytest.raises(mem_store.MemoryError):
        mem_store.write_file(proj, "project.md", "hi", mode="overwrite")
