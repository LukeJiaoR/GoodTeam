import json
from pathlib import Path

import pytest

from goodteam_os.skills.library import SkillLibraryService
from goodteam_os.skills.models import SkillConflict, SkillError


def write_skill(root: Path, *, body: str = "Check persisted state carefully.", extra: bool = False) -> Path:
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: custom-review\ndescription: Review persisted state changes.\n---\n" + body + "\n",
        encoding="utf-8",
    )
    references = root / "references"
    references.mkdir()
    (references / "rules.md").write_text("Always cite the changed file.\n", encoding="utf-8")
    if extra:
        scripts = root / "scripts"
        scripts.mkdir()
        marker = root.parent / "script-ran"
        script = scripts / "do-not-run.sh"
        script.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
        script.chmod(0o700)
    return root


def test_preview_captures_bytes_then_install_does_not_reopen_source(tmp_path):
    source = write_skill(tmp_path / "outside-skill")
    service = SkillLibraryService(tmp_path / "state")

    preview = service.preview_local(str(source), command_key="preview-1")
    assert preview["status"] == "awaiting_confirmation"
    assert preview["metadata"]["name"] == "custom-review"
    assert {item["path"] for item in preview["manifest"]["files"]} == {"SKILL.md", "references/rules.md"}

    # The user-visible preview is a fixed proposal. Source edits after preview
    # must not swap the bytes that confirmation installs.
    (source / "SKILL.md").write_text(
        "---\nname: custom-review\ndescription: changed\n---\nCHANGED\n",
        encoding="utf-8",
    )
    installed = service.install(preview["proposal_id"])
    assert installed["status"] == "installed"

    library = service.list_library()
    assert len(library["skills"]) == 1
    version = library["skills"][0]["versions"][0]
    assert version["metadata"]["description"] == "Review persisted state changes."
    assert version["package_digest"] == preview["package_digest"]
    assert version["diagnostics"] == preview["diagnostics"]


def test_preview_replay_is_stable_and_key_cannot_change_source(tmp_path):
    first = write_skill(tmp_path / "first")
    second = write_skill(tmp_path / "second")
    service = SkillLibraryService(tmp_path / "state")

    initial = service.preview_local(str(first), command_key="same-key")
    replay = service.preview_local(str(first), command_key="same-key")
    assert replay == initial
    with pytest.raises(SkillConflict) as exc:
        service.preview_local(str(second), command_key="same-key")
    assert exc.value.code == "operation_conflict"


def test_install_replay_does_not_duplicate_version(tmp_path):
    source = write_skill(tmp_path / "skill")
    service = SkillLibraryService(tmp_path / "state")
    preview = service.preview_local(str(source), command_key="preview")

    first = service.install(preview["proposal_id"])
    second = service.install(preview["proposal_id"])
    assert second == first
    assert len(service.list_library()["skills"][0]["versions"]) == 1


def test_install_never_executes_package_script(tmp_path):
    source = write_skill(tmp_path / "skill", extra=True)
    marker = tmp_path / "script-ran"
    service = SkillLibraryService(tmp_path / "state")

    preview = service.preview_local(str(source), command_key="preview")
    service.install(preview["proposal_id"])
    assert not marker.exists()


def test_preview_rejects_missing_or_unsafe_package(tmp_path):
    service = SkillLibraryService(tmp_path / "state")
    with pytest.raises(SkillError) as exc:
        service.preview_local(str(tmp_path / "missing"), command_key="missing")
    assert exc.value.code == "source_unavailable"

    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "not-skill.txt").write_text("no")
    with pytest.raises(SkillError) as exc:
        service.preview_local(str(broken), command_key="broken")
    assert exc.value.code == "skill_missing"


def test_public_payload_does_not_expose_absolute_source_path(tmp_path):
    source = write_skill(tmp_path / "private" / "my-skill")
    service = SkillLibraryService(tmp_path / "state")
    preview = service.preview_local(str(source), command_key="preview")
    assert preview["source"] == {"kind": "local", "label": "my-skill"}
    assert str(source) not in json.dumps(preview)

    service.install(preview["proposal_id"])
    payload = service.list_library()
    assert str(source) not in json.dumps(payload)


def test_library_store_refuses_worker_workspace_before_creating_library_state(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_root = workspace / ".goodteam-state"
    with pytest.raises(SkillError) as exc:
        SkillLibraryService(state_root, worker_roots=(workspace,))
    assert exc.value.code == "unsafe_store_root"
    assert not (state_root / "skills").exists()
