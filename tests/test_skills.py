"""Unit tests for unified Skill installer and runtime injector."""
from __future__ import annotations

import io
import subprocess
import zipfile
import pytest

from goodteam.skills.injector import cleanup_staged_skills, stage_and_render
from goodteam.skills.installer import (
    discover_skills,
    ensure_builtins,
    install_github,
    install_local,
    install_zip,
    parse_github_source,
    preview_github,
    preview_zip,
    read_skill_resource,
    uninstall_skill,
)
from goodteam.skills.models import SkillError
from goodteam.store import Store


def _make_zip(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_builtin_skills_and_injector(tmp_path):
    store = Store(state_dir=tmp_path)
    try:
        builtins = ensure_builtins(store)
        ids = {s["id"] for s in builtins}
        assert {"builtin:research", "builtin:coding", "builtin:code-review", "builtin:qa-testing", "builtin:project-memory"} <= ids

        role = store.upsert_role(
            project_id=None,
            key="reviewer",
            name="Reviewer",
            skill_bindings=[
                {"skill_id": "builtin:code-review", "mode": "required"},
                {"skill_id": "builtin:research", "mode": "on_demand"},
            ],
        )
        inj = stage_and_render(store, role, "run-test-1", state_dir=tmp_path)
        assert "builtin:code-review" in inj.required_ids
        assert "builtin:research" in inj.on_demand_ids
        assert "Code Review" in inj.prompt_text
        assert "references/checklist.md" in inj.prompt_text
        assert inj.staged_dir is not None and (inj.staged_dir / "builtin__code-review" / "references" / "checklist.md").is_file()

        cleanup_staged_skills(inj.staged_dir)
        assert not inj.staged_dir.exists()
    finally:
        store.close()


def test_zip_preview_install_and_zip_slip_rejection(tmp_path):
    store = Store(state_dir=tmp_path)
    try:
        bad_zip = _make_zip({"../../evil.txt": "boom"})
        with pytest.raises(SkillError) as exc:
            preview_zip(bad_zip)
        assert exc.value.code == "unsafe_zip_path"

        multi_zip = _make_zip({
            "bundle/skill-a/SKILL.md": "---\nname: alpha-skill\ndescription: Alpha helper\n---\nDo alpha.\n",
            "bundle/skill-b/SKILL.md": "---\nname: beta-skill\ndescription: Beta helper\n---\nDo beta.\n",
            "bundle/skill-b/references/ref.md": "Secret reference\n",
        })
        preview = preview_zip(multi_zip)
        assert len(preview["skills"]) == 2

        installed = install_zip(store, multi_zip, selected_paths=["bundle/skill-b"], state_dir=tmp_path)
        assert len(installed) == 1
        rec = installed[0]
        assert rec["id"] == "zip:beta-skill"
        assert "Secret reference" in read_skill_resource(rec, "references/ref.md")
        with pytest.raises(SkillError):
            read_skill_resource(rec, "../../etc/passwd")
    finally:
        store.close()


def test_github_url_parser_and_local_repo_install(tmp_path):
    url, ref, sub = parse_github_source("anthropics/skills")
    assert url == "https://github.com/anthropics/skills.git"
    assert (ref, sub) == ("", "")

    url2, ref2, sub2 = parse_github_source("https://github.com/org/repo/tree/main/skills/foo")
    assert url2 == "https://github.com/org/repo.git"
    assert (ref2, sub2) == ("main", "skills/foo")

    # Create a real local git repository fixture for offline clone test
    repo = tmp_path / "src_repo"
    (repo / "my-skill").mkdir(parents=True)
    (repo / "my-skill" / "SKILL.md").write_text("---\nname: git-demo\ndescription: From git\n---\nRun git demo.\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)

    store = Store(state_dir=tmp_path / "state")
    try:
        prev = preview_github(repo.as_uri())
        assert prev["skills"][0]["slug"] == "git-demo"
        assert len(prev["commit_sha"]) == 40

        recs = install_github(store, repo.as_uri(), state_dir=tmp_path / "state")
        assert recs[0]["source_type"] == "github"
        assert recs[0]["commit_sha"] == prev["commit_sha"]
    finally:
        store.close()


def test_local_install_discovery_isolation_and_uninstall(tmp_path):
    local_skill = tmp_path / "local_skill"
    local_skill.mkdir()
    (local_skill / "SKILL.md").write_text("---\nname: local-one\ndescription: Local desc\n---\nBody\n")

    proj_dir = tmp_path / "proj"
    disc_skill = proj_dir / ".agents" / "skills" / "proj-rule"
    disc_skill.mkdir(parents=True)
    (disc_skill / "SKILL.md").write_text("---\nname: proj-rule\ndescription: Discovered\n---\nRule\n")

    store = Store(state_dir=tmp_path / "state")
    try:
        proj = store.create_project(name="p", path=str(proj_dir))
        store.upsert_role(project_id=proj["id"], key="coder", name="Coder")

        local_rec = install_local(store, local_skill, state_dir=tmp_path / "state")
        assert local_rec["id"] == "local:local-one"

        discovered = discover_skills(store, project_path=proj_dir, include_user_dirs=False)
        assert len(discovered) == 1
        assert discovered[0]["source_type"] == "discovered"
        # Auto-discovered skills must NOT auto-bind to any role
        assert store.get_role(proj["id"], "coder")["skill_bindings"] == []

        store.upsert_role(
            project_id=proj["id"], key="coder", name="Coder",
            skill_bindings=[{"skill_id": local_rec["id"], "mode": "required"}],
        )
        assert uninstall_skill(store, local_rec["id"], state_dir=tmp_path / "state") is True
        assert store.get_role(proj["id"], "coder")["skill_bindings"] == []
    finally:
        store.close()
