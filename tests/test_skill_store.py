"""Skill store tests use actual files/locks/process failures, never a model."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import multiprocessing
import os
from pathlib import Path
import shutil

import pytest

from goodteam_os.skills import (
    PackageFile, SkillConflict, SkillError, SkillScope, SkillSource, SkillStore,
    capture_package, package_from_files,
)

MAIN = b"---\nname: example\ndescription: A user-supplied method.\n---\nRead notes/rule.txt.\n"


def make_package(note=b"v1"):
    return package_from_files(
        [PackageFile("SKILL.md", MAIN), PackageFile("notes/rule.txt", note),
         PackageFile("script.sh", b"#!/bin/sh\nexit 37\n", True), PackageFile("blob.bin", b"\x00\xff")],
        ["notes", "empty-directory"],
    )


@pytest.fixture
def store(tmp_path):
    return SkillStore(tmp_path / "store", SkillScope("user", "alice"))


@pytest.fixture
def origin():
    return SkillSource("local", "/explicit/user-owned/source")


def index_path(store):
    return store.root / store.scope.namespace / "index.json"


def test_publish_read_metadata_and_original_binary_bytes(store, origin):
    package = make_package()
    result = store.publish(package, origin, command_key="install-1")
    assert result.installed.name == "example"
    assert result.version.package_digest == package.digest
    assert result.version.metadata["description"] == "A user-supplied method."
    assert result.revision == 1 and not result.replayed
    assert store.read_resource(result.installed.skill_id, result.version.version_id, "blob.bin") == b"\x00\xff"
    assert store.read_resource(result.installed.skill_id, result.version.version_id, "script.sh").endswith(b"exit 37\n")
    assert store.get_version(result.installed.skill_id, result.version.version_id) == result.version
    assert store.list_installed() == (result.installed,)
    assert store.list_versions(result.installed.skill_id) == (result.version,)
    assert (store.root / store.scope.namespace / "objects" / package.digest[7:] / "content/empty-directory").is_dir()


def test_duplicate_request_replays_original_receipt_and_conflicts_on_new_inputs(store, origin):
    original = store.publish(make_package(), origin, command_key="same")
    before = index_path(store).read_bytes()
    replay = store.publish(make_package(), origin, command_key="same")
    assert replay.replayed and replay.revision == original.revision
    assert index_path(store).read_bytes() == before
    with pytest.raises(SkillConflict) as conflict:
        store.publish(make_package(b"different"), origin, command_key="same")
    assert conflict.value.code == "operation_conflict"
    assert index_path(store).read_bytes() == before
    other_request = store.publish(make_package(), origin, command_key="another")
    assert other_request.version == original.version
    assert len(store.list_versions(original.installed.skill_id)) == 1


def test_two_sources_with_same_name_and_bytes_do_not_overwrite_each_other(store, origin):
    first = store.publish(make_package(), origin, command_key="first")
    second = store.publish(make_package(), replace(origin, locator="/different/source"), command_key="second")
    assert first.installed.skill_id != second.installed.skill_id
    assert first.version.version_id != second.version.version_id
    assert first.version.package_digest == second.version.package_digest
    assert len(store.list_installed()) == 2
    objects = store.root / store.scope.namespace / "objects"
    assert len(list(objects.iterdir())) == 1  # Only storage deduplicates, not ownership/source records.


def test_scope_isolation_is_not_bypassed_by_shared_content(store, origin):
    first = store.publish(make_package(), origin, command_key="same")
    other = SkillStore(store.root, SkillScope("user", "bob"))
    assert other.list_installed() == ()
    with pytest.raises(SkillError) as error:
        other.read_resource(first.installed.skill_id, first.version.version_id, "SKILL.md")
    assert error.value.code == "version_missing"
    second = other.publish(make_package(), origin, command_key="same")
    assert second.installed.skill_id != first.installed.skill_id
    assert second.version.package_digest == first.version.package_digest


def test_new_versions_never_change_old_package_or_pins(store, origin):
    first = store.publish(make_package(), origin, command_key="one")
    store.pin(first.installed.skill_id, first.version.version_id, holder_id="task:1/reviewer", command_key="pin")
    second = store.publish(make_package(b"v2"), origin, command_key="two")
    assert second.installed.skill_id == first.installed.skill_id
    assert len(second.installed.version_ids) == 2
    assert store.read_resource(first.installed.skill_id, first.version.version_id, "notes/rule.txt") == b"v1"
    assert store.read_resource(second.installed.skill_id, second.version.version_id, "notes/rule.txt") == b"v2"
    assert len(store.retention_pins(first.installed.skill_id, first.version.version_id)) == 1


def test_imported_bytes_survive_source_edit_and_deletion(store, tmp_path):
    source = tmp_path / "example"
    source.mkdir()
    (source / "SKILL.md").write_bytes(MAIN)
    captured = capture_package(source)
    (source / "SKILL.md").write_text("unapproved later bytes")
    result = store.publish(captured, SkillSource("local", str(source)), command_key="captured")
    shutil.rmtree(source)
    assert store.read_resource(result.installed.skill_id, result.version.version_id, "SKILL.md") == MAIN


def test_retention_holders_cannot_be_silently_retargeted_or_released(store, origin):
    one = store.publish(make_package(), origin, command_key="one")
    two = store.publish(make_package(b"two"), origin, command_key="two")
    sid, vid = one.installed.skill_id, one.version.version_id
    first = store.pin(sid, vid, holder_id="task:1", command_key="pin")
    replay = store.pin(sid, vid, holder_id="task:1", command_key="pin")
    assert replay["replayed"] and replay["revision"] == first["revision"]
    with pytest.raises(SkillConflict):
        store.pin(sid, two.version.version_id, holder_id="task:1", command_key="retarget")
    with pytest.raises(SkillConflict):
        store.release_pin(sid, two.version.version_id, holder_id="task:1", command_key="wrong-release")
    released = store.release_pin(sid, vid, holder_id="task:1", command_key="release")
    assert released["released"]
    assert store.retention_pins(sid, vid) == ()
    assert store.release_pin(sid, vid, holder_id="task:1", command_key="release")["replayed"]
    assert store.read_resource(sid, vid, "SKILL.md") == MAIN  # No automatic GC.


def test_concurrent_identical_installs_have_one_effect(store, origin):
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda _i: store.publish(make_package(), origin, command_key="shared"), range(12)))
    assert sum(not item.replayed for item in results) == 1
    assert len({item.version.version_id for item in results}) == 1
    assert len(store.list_installed()) == 1
    assert json.loads(index_path(store).read_text())["revision"] == 1


def test_source_and_metadata_claims_are_validated_before_publication(store, origin):
    original = make_package()
    with pytest.raises(SkillError):
        store.publish(replace(original, metadata_json='{"name":"forged"}'), origin, command_key="bad")
    assert store.list_installed() == ()


def test_precommit_failure_leaves_no_bindable_partial_install_and_retry_recovers(store, origin, monkeypatch):
    real_write = store._write_index

    def fail_before(*_args):
        raise OSError("injected metadata failure")

    monkeypatch.setattr(store, "_write_index", fail_before)
    with pytest.raises(SkillError):
        store.publish(make_package(), origin, command_key="interrupted")
    assert store.list_installed() == ()
    monkeypatch.setattr(store, "_write_index", real_write)
    recovered = store.publish(make_package(), origin, command_key="interrupted")
    assert recovered.revision == 1
    assert len(store.list_installed()) == 1


def test_committed_operation_with_lost_response_replays_without_second_mutation(store, origin, monkeypatch):
    real_write = store._write_index

    def fail_after(*args):
        real_write(*args)
        raise OSError("injected lost response")

    monkeypatch.setattr(store, "_write_index", fail_after)
    with pytest.raises(SkillError):
        store.publish(make_package(), origin, command_key="lost-response")
    monkeypatch.setattr(store, "_write_index", real_write)
    replay = store.publish(make_package(), origin, command_key="lost-response")
    assert replay.replayed and replay.revision == 1
    assert len(store.list_installed()) == 1


def _crash_after_object(root, scope, source):
    class CrashingStore(SkillStore):
        def _write_index(self, *_args):
            os._exit(23)
    CrashingStore(Path(root), scope).publish(make_package(), source, command_key="crash")


def test_actual_process_crash_releases_lock_and_does_not_publish_half_install(store, origin):
    process = multiprocessing.get_context("fork").Process(target=_crash_after_object, args=(str(store.root), store.scope, origin))
    process.start()
    process.join(timeout=10)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail("Crash fixture did not exit")
    assert process.exitcode == 23
    assert store.list_installed() == ()
    result = store.publish(make_package(), origin, command_key="crash")
    assert result.revision == 1
    assert len(store.list_installed()) == 1


def test_staging_failure_cleans_only_its_own_resources(store, origin, tmp_path, monkeypatch):
    from goodteam_os.skills import store as module
    original = module.write_new
    unrelated = tmp_path / "retain"
    unrelated.write_text("keep")

    def fail_manifest(fd, name, data, **kwargs):
        if name == "manifest.json":
            raise OSError("injected staging failure")
        return original(fd, name, data, **kwargs)

    monkeypatch.setattr(module, "write_new", fail_manifest)
    with pytest.raises(SkillError):
        store.publish(make_package(), origin, command_key="bad")
    assert store.list_installed() == ()
    assert list((store.root / store.scope.namespace / "objects").iterdir()) == []
    assert unrelated.read_text() == "keep"


def test_missing_or_tampered_bytes_never_fall_back_to_another_version(store, origin):
    first = store.publish(make_package(), origin, command_key="one")
    store.publish(make_package(b"v2"), origin, command_key="two")
    target = store.root / store.scope.namespace / "objects" / first.version.package_digest[7:] / "content/notes/rule.txt"
    target.chmod(0o600)
    target.write_bytes(b"corrupted")
    with pytest.raises(SkillError) as error:
        store.get_version(first.installed.skill_id, first.version.version_id)
    assert error.value.code == "package_corrupt"


def test_unknown_schema_and_index_symlinks_are_not_overwritten(store, origin, tmp_path):
    store.publish(make_package(), origin, command_key="one")
    path = index_path(store)
    data = json.loads(path.read_text())
    data["schema_version"] = "future.v99"
    path.write_text(json.dumps(data))
    original = path.read_bytes()
    with pytest.raises(SkillError) as error:
        store.publish(make_package(), origin, command_key="two")
    assert error.value.code == "unsupported_schema"
    assert path.read_bytes() == original
    path.unlink()
    outside = tmp_path / "outside-index"
    outside.write_bytes(original)
    path.symlink_to(outside)
    with pytest.raises(SkillError):
        store.list_installed()
    assert outside.read_bytes() == original


def test_declared_worker_writable_root_cannot_contain_package_store(tmp_path):
    with pytest.raises(SkillError) as error:
        SkillStore(tmp_path / "repo/.goodteam/packages", SkillScope("user", "alice"), worker_roots=(tmp_path / "repo",))
    assert error.value.code == "unsafe_store_root"
    assert not (tmp_path / "repo").exists()


def test_git_provenance_revision_changes_without_losing_original_bytes(store):
    source = SkillSource("git", "https://example.org/repository.git", "skills/example", "a" * 40)
    first = store.publish(make_package(), source, command_key="a")
    second = store.publish(make_package(), replace(source, revision="b" * 40), command_key="b")
    assert first.installed.skill_id == second.installed.skill_id
    assert first.version.version_id != second.version.version_id
    assert first.version.package_digest == second.version.package_digest
    assert store.get_version(first.installed.skill_id, first.version.version_id).source.revision == "a" * 40


def test_restrictive_umask_does_not_rewrite_executable_intent(store, origin):
    previous = os.umask(0o177)
    try:
        result = store.publish(make_package(), origin, command_key="restrictive-umask")
    finally:
        os.umask(previous)
    assert store.get_version(result.installed.skill_id, result.version.version_id).package_digest == make_package().digest
    script = store.root / store.scope.namespace / "objects" / result.version.package_digest[7:] / "content/script.sh"
    assert script.stat().st_mode & 0o777 == 0o500


def test_nested_empty_directories_survive_full_store_roundtrip(store, origin):
    package = package_from_files([PackageFile("SKILL.md", MAIN)], ["a", "a/b", "a/b/c"])
    result = store.publish(package, origin, command_key="nested-directories")
    assert store.get_version(result.installed.skill_id, result.version.version_id).package_digest == package.digest
    folder = store.root / store.scope.namespace / "objects" / package.digest[7:] / "content/a/b/c"
    assert folder.is_dir()


def test_replay_returns_fixed_receipt_version_and_a_separate_current_install_view(store, origin):
    first = store.publish(make_package(), origin, command_key="first")
    second = store.publish(make_package(b"second"), origin, command_key="second")
    replay = store.publish(make_package(), origin, command_key="first")
    assert replay.replayed and replay.version == first.version and replay.revision == first.revision
    assert replay.installed == second.installed  # Current state is not an active-version selection.
