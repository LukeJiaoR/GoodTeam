# Existing Project Onboarding Track 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the snapshot-bound, read-only repository access foundation that future model onboarding can use without direct target-root access.

**Architecture:** A descriptor-anchored reader owns all target-repository file access. Snapshot and inventory services build immutable content identity and low-cost facts on top of that reader, then a bound workspace store publishes those artifacts and a snapshot-locked observation ledger. A budgeted Context Provider exposes only seven bounded tools; this track deliberately leaves readiness schema 2 and current model-free executor behavior unchanged.

**Tech Stack:** Python 3.11 standard library (dataclasses, hashlib, json, os, stat, subprocess, tomllib), pytest, ruff, existing GoodTeam project-import locking and readiness binding

---

## Scope Guard

This plan implements Track 1 from the approved design:

- repository_snapshot.v1 capture and drift verification;
- repository_inventory.v1 extraction with a scan_existing_project compatibility wrapper;
- bounded list_tree, search_text, read_file_excerpt, inspect_manifest,
  inspect_git_history, inspect_tests, and inspect_recent_changes tools;
- provider budgets and a common observation envelope;
- descriptor-anchored, workspace-only immutable snapshot, inventory, and
  observation persistence; and
- security, read-only, and compatibility tests.

This plan does not add a model tool loop, project_profile.v1, readiness schema 3,
profile review UI, downstream Worker pickup, or incremental profile refresh. No
code in this track may claim that model onboarding has completed.

The six approved invariants remain release gates. Track 1 mechanically enforces
the model-facing repository boundary, common snapshot identity for inventory and
observations, and no target writes or project-command execution. Claim evidence,
`accept_limited`, and later-Worker current-file rereads are deliberately not
simulated here; Tracks 2, 4, and 5 must implement them before the overall feature
can mark an existing project ready.

## File Map

- Create src/goodteam_os/repository_access.py: canonical relative-path policy,
  descriptor traversal, bounded reads, root/entry replacement checks, and shared exclusions.
- Create src/goodteam_os/repository_snapshot.py: clean/dirty Git and non-Git
  snapshot capture, canonical digest, completeness, and drift verification.
- Create src/goodteam_os/repository_inventory.py: extracted scanner constants,
  inventory traversal, command discovery, constraint candidates, and digest.
- Create src/goodteam_os/onboarding_store.py: bound, immutable snapshot and
  inventory artifacts plus snapshot-locked observation JSONL.
- Create src/goodteam_os/repository_context_provider.py: budgets, redaction,
  observation envelopes, exact dispatch, and seven bounded tools.
- Modify src/goodteam_os/project_import.py: expose a descriptor-anchored context
  manager for bound project-state subdirectories.
- Modify src/goodteam_os/project_readiness_executor.py: retain
  scan_existing_project as a compatibility wrapper; do not change readiness steps.
- Create tests/test_repository_access.py.
- Create tests/test_repository_snapshot.py.
- Create tests/test_repository_inventory.py.
- Create tests/test_onboarding_store.py.
- Create tests/test_repository_context_provider.py.
- Modify tests/test_project_readiness_executor.py only for compatibility coverage.
- Create docs/repository-context-provider.md.
- Modify docs/readiness-executors.md.

### Task 1: Add the descriptor-anchored repository access boundary

**Files:**
- Create: src/goodteam_os/repository_access.py
- Create: tests/test_repository_access.py

- [ ] **Step 1: Write failing containment, symlink, and bounded-read tests**

Create tests/test_repository_access.py with:

~~~python
from pathlib import Path

import pytest

from goodteam_os.repository_access import AnchoredRepository, RepositoryAccessError


def root_identity(root: Path) -> dict[str, int]:
    value = root.stat()
    return {"device": value.st_dev, "inode": value.st_ino}


@pytest.mark.parametrize("path", ["/etc/passwd", "../secret", "src/../secret", "./src/app.py"])
def test_reader_rejects_noncanonical_paths(tmp_path: Path, path: str) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        with pytest.raises(RepositoryAccessError, match="canonical repository-relative"):
            repository.read_text(path, max_bytes=128)


def test_reader_never_follows_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    (external / "secret.txt").write_text("TOP_SECRET", encoding="utf-8")
    (root / "linked-file").symlink_to(external / "secret.txt")
    (root / "linked-dir").symlink_to(external, target_is_directory=True)
    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        assert {item.kind for item in repository.list_directory("")} == {"symlink"}
        with pytest.raises(RepositoryAccessError, match="symbolic link"):
            repository.read_text("linked-file", max_bytes=128)
        with pytest.raises(RepositoryAccessError, match="symbolic link"):
            repository.list_directory("linked-dir")


def test_reader_returns_bounded_text_and_identity(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    with AnchoredRepository(root, expected_identity=root_identity(root)) as repository:
        result = repository.read_text("README.md", max_bytes=11)
    assert result.text == "alpha\nbeta\n"
    assert result.truncated is True
    assert result.content_hash.startswith("sha256:")
    assert (result.start_line, result.end_line) == (1, 2)
~~~

Add named tests `test_reader_rejects_nul_as_binary`,
`test_reader_rejects_invalid_utf8`, `test_reader_rejects_root_identity_mismatch`,
and parameterized `test_reader_detects_entry_change_during_read` cases for inode,
size, and mtime_ns. Assert the stable codes are `repository_binary_file`,
`repository_non_text_file`, `repository_root_changed`, and
`repository_entry_changed`, respectively, and that no changed bytes are returned.

- [ ] **Step 2: Run the focused test and verify RED**

~~~bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_repository_access.py -q
~~~

Expected: collection fails because goodteam_os.repository_access does not exist.

- [ ] **Step 3: Implement access types and descriptor operations**

Create these public values:

~~~python
DEFAULT_IGNORED_DIRECTORIES = frozenset({
    ".git", ".hg", ".svn", ".venv", "node_modules", "dist", "build",
    "coverage", "__pycache__", ".next",
})


class RepositoryAccessError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, object] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class RepositoryEntry:
    path: str
    kind: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class RepositoryText:
    path: str
    text: str
    content_hash: str
    size: int
    start_line: int
    end_line: int
    truncated: bool
~~~

Add canonical_relative_path(value, allow_root=False) using PurePosixPath. Accept
only unchanged, non-absolute paths without empty, dot, or parent components;
accept the empty string only with allow_root=True. Rejections use
context_path_rejected and include the raw path.

Implement AnchoredRepository.list_directory(), walk(), read_text(), and
verify_root(). Open root and children with os.open, dir_fd, O_DIRECTORY where
appropriate, and O_NOFOLLOW. Compare lstat-style metadata with fstat before
reading and compare device, inode, mode, size, and mtime_ns after reading. Recheck
root identity before every public return. Read at most max_bytes + 1, reject NUL
or invalid UTF-8, trim an over-limit result to the last complete newline when one
exists (otherwise to the longest valid UTF-8 prefix), and hash the exact returned
unredacted bytes. Never use target-root resolve(), rglob(), or ordinary file
open().

- [ ] **Step 4: Run tests and ruff**

~~~bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_repository_access.py -q
.venv/bin/ruff check src/goodteam_os/repository_access.py tests/test_repository_access.py
~~~

Expected: all pass.

- [ ] **Step 5: Commit**

~~~bash
git add src/goodteam_os/repository_access.py tests/test_repository_access.py
git commit -m "feat(onboarding): add anchored repository reader"
~~~

### Task 2: Capture coherent repository snapshots

**Files:**
- Create: src/goodteam_os/repository_snapshot.py
- Create: tests/test_repository_snapshot.py

- [ ] **Step 1: Write failing clean, dirty, non-Git, and drift tests**

Create a git(root, *args) helper using subprocess.run with argv, captured output,
text mode, and check=True. Add:

~~~python
def test_clean_git_snapshot_is_deterministic(tmp_path: Path) -> None:
    root = init_git_repo(tmp_path / "repo")
    first = capture_repository_snapshot(root, project_key="DEMO")
    second = capture_repository_snapshot(root, project_key="DEMO")
    assert first.schema == "repository_snapshot.v1"
    assert first.snapshot_id == second.snapshot_id
    assert first.source_control["head_sha"] == git(root, "rev-parse", "HEAD")
    assert first.source_control["dirty"] is False
    assert first.content_scope["complete"] is True


def test_dirty_states_each_change_identity(tmp_path: Path) -> None:
    root = init_git_repo(tmp_path / "repo")
    clean = capture_repository_snapshot(root, project_key="DEMO")
    (root / "README.md").write_text("unstaged\n", encoding="utf-8")
    unstaged = capture_repository_snapshot(root, project_key="DEMO")
    git(root, "add", "README.md")
    staged = capture_repository_snapshot(root, project_key="DEMO")
    (root / "new.py").write_text("VALUE = 1\n", encoding="utf-8")
    untracked = capture_repository_snapshot(root, project_key="DEMO")
    assert len({clean.snapshot_id, unstaged.snapshot_id, staged.snapshot_id, untracked.snapshot_id}) == 4


def test_verification_fails_after_drift(tmp_path: Path) -> None:
    root = init_git_repo(tmp_path / "repo")
    snapshot = capture_repository_snapshot(root, project_key="DEMO")
    (root / "README.md").write_text("changed\n", encoding="utf-8")
    with pytest.raises(RepositorySnapshotError) as captured:
        verify_repository_snapshot(root, snapshot)
    assert captured.value.code == "repository_snapshot_changed"
~~~

Add named tests `test_non_git_snapshot_is_deterministic`,
`test_snapshot_records_generated_binary_and_symlink_exclusions`,
`test_ceiling_omissions_make_snapshot_incomplete`,
`test_ignored_node_modules_change_does_not_change_identity`,
`test_staged_and_unstaged_versions_have_distinct_digests`,
`test_capture_rejects_change_between_stability_passes`, and
`test_snapshot_rejects_root_replacement`. Assert policy exclusions leave
`content_scope.complete` true, ceiling omissions set it false with counts, and
capture or verification returns no snapshot after detected drift.

- [ ] **Step 2: Run tests and verify RED**

~~~bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_repository_snapshot.py -q
~~~

Expected: module import failure.

- [ ] **Step 3: Implement snapshot types and capture**

Use:

~~~python
@dataclass(frozen=True)
class SnapshotLimits:
    max_files: int = 20_000
    max_total_bytes: int = 512 * 1024 * 1024
    max_file_bytes: int = 8 * 1024 * 1024


@dataclass(frozen=True)
class RepositorySnapshot:
    schema: str
    snapshot_id: str
    project_key: str
    canonical_root: str
    root_identity: dict[str, int]
    source_control: dict[str, object]
    content_scope: dict[str, object]
    captured_at: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)
~~~

Add RepositorySnapshotError(code, message, details),
capture_repository_snapshot(root, project_key, expected_identity=None,
limits=SnapshotLimits()), and verify_repository_snapshot(root, snapshot, limits).

Capture through AnchoredRepository. Detect Git only with fixed read-only argv.
Record HEAD, symbolic branch, NUL-delimited porcelain-v2 status, recursive
submodule status, the complete `git ls-files --stage -z` index entry listing
(whose blob OIDs bind staged bytes), and anchored content hashes for every
policy-eligible unstaged and untracked path. This keeps a staged version distinct
from a later unstaged edit of the same file. Parse and canonicalize Git paths
before hashing; omit policy-excluded records such as `node_modules` from the
identity-bearing status and dirty flag while recording their exclusion counts.
For non-Git, hash every policy-eligible text file within limits.

Set GIT_OPTIONAL_LOCKS=0, GIT_TERMINAL_PROMPT=0, and LC_ALL=C; use no shell,
captured output, and five-second timeout. Non-repository is the only allowed
fallback; other Git errors raise repository_snapshot_failed. Record exclusion
counts/reasons. Ceiling omissions set complete=False; policy exclusions do not.
Hash sorted compact JSON excluding snapshot_id and captured_at, prefix with
snap_. Build the identity-bearing fields twice under the same limits and reject
`repository_snapshot_changed` if HEAD, status, index, submodule, eligible-path
set, metadata, or content digests differ; never retry into an apparently stable
snapshot. `verify_repository_snapshot()` performs the same stable capture and
compares the resulting ID.

- [ ] **Step 4: Run tests and ruff**

~~~bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_repository_snapshot.py tests/test_repository_access.py -q
.venv/bin/ruff check src/goodteam_os/repository_snapshot.py tests/test_repository_snapshot.py
~~~

Expected: all pass.

- [ ] **Step 5: Commit**

~~~bash
git add src/goodteam_os/repository_snapshot.py tests/test_repository_snapshot.py
git commit -m "feat(onboarding): capture repository snapshots"
~~~

### Task 3: Extract repository inventory

**Files:**
- Create: src/goodteam_os/repository_inventory.py
- Create: tests/test_repository_inventory.py
- Modify: src/goodteam_os/project_readiness_executor.py:41-87,1382-1789
- Modify: tests/test_project_readiness_executor.py:12-20,600-950

- [ ] **Step 1: Write failing inventory and compatibility tests**

~~~python
def test_inventory_is_snapshot_bound_and_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest", "build": "vite build"}}),
        encoding="utf-8",
    )
    (root / "src").mkdir()
    (root / "src" / "main.ts").write_text("export const main = true;\n", encoding="utf-8")
    snapshot = capture_repository_snapshot(root, project_key="DEMO")
    first = build_repository_inventory(root, snapshot=snapshot)
    second = build_repository_inventory(root, snapshot=snapshot)
    assert first["schema"] == "repository_inventory.v1"
    assert first["snapshot_id"] == snapshot.snapshot_id
    assert first["inventory_digest"] == second["inventory_digest"]
    assert {item["command"] for item in first["commands"]} == {"npm run build", "npm run test"}
    assert "src/main.ts" in first["entry_points"]
~~~

Add a digest-change test that captures a second snapshot after changing the
fixture and asserts the two snapshot-bound inventory digests differ. Add a
compatibility test asserting
project_readiness_executor.scan_existing_project retains commands, constraints,
scan, and scan.read_only=True.

- [ ] **Step 2: Run and verify RED**

~~~bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_repository_inventory.py tests/test_project_readiness_executor.py -q
~~~

Expected: new module import failure while existing tests pass alone.

- [ ] **Step 3: Move scanner logic and keep compatibility**

Move constants and scanner helpers into repository_inventory.py. Implement
build_repository_inventory(root, snapshot, max_entries=1500, max_depth=4,
max_read_bytes=256*1024) through AnchoredRepository. Preserve current fields and
add schema, snapshot_id, and inventory_digest. Hash canonical JSON excluding only
inventory_digest. Verify snapshot before and after.

Keep legacy `entry_points`, `constraints`, and `commands` for schema-2 callers,
but add `entrypoint_candidates` and `constraint_candidates` records with path,
basis, and `classification: heuristic`; add the same classification to command
candidates. Inventory convention matches are inputs to later model exploration,
never confirmed architecture or confirmed commands.

Keep:

~~~python
def scan_existing_project(root: Path, *, expected_identity: dict[str, object] | None = None) -> dict[str, object]:
    try:
        snapshot = capture_repository_snapshot(
            root,
            project_key="LEGACY-READINESS",
            expected_identity=expected_identity,
        )
        return build_repository_inventory(root, snapshot=snapshot)
    except (RepositoryAccessError, RepositorySnapshotError) as exc:
        legacy_code = {
            "repository_root_changed": "project_root_changed",
            "repository_entry_changed": "project_entry_changed",
        }.get(exc.code, exc.code)
        raise ReadinessExecutionError(
            legacy_code,
            str(exc),
            details=exc.details,
            recovery=["retry_readiness", "reload_project"],
        ) from exc
~~~

Import/re-export the scanner ceiling constants from `repository_inventory` so
existing imports remain valid. Move the old private `_read_regular_file` race
test to `tests/test_repository_access.py`, where Task 1 covers the same inode,
size, and mtime contract; do not retain the private helper only for a test. Add
compatibility assertions for the translated `project_root_changed` and
`project_entry_changed` codes. Do not rename the schema-2 readiness step.

- [ ] **Step 4: Run regressions and ruff**

~~~bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/test_repository_inventory.py \
  tests/test_project_readiness_executor.py \
  tests/test_project_readiness.py \
  tests/test_project_readiness_web.py -q
.venv/bin/ruff check src/goodteam_os/repository_inventory.py src/goodteam_os/project_readiness_executor.py tests/test_repository_inventory.py tests/test_project_readiness_executor.py
~~~

Expected: all pass.

- [ ] **Step 5: Commit**

~~~bash
git add src/goodteam_os/repository_inventory.py src/goodteam_os/project_readiness_executor.py tests/test_repository_inventory.py tests/test_project_readiness_executor.py
git commit -m "refactor(readiness): extract repository inventory"
~~~

### Task 4: Add workspace-only onboarding persistence

**Files:**
- Modify: src/goodteam_os/project_import.py:131-264,488-553
- Create: src/goodteam_os/onboarding_store.py
- Create: tests/test_onboarding_store.py

- [ ] **Step 1: Write failing binding and isolation tests**

~~~python
def test_store_writes_only_to_goodteam_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("unchanged\n", encoding="utf-8")
    key = import_project(workspace, str(root))["project"]["project_key"]
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    snapshot = capture_repository_snapshot(root, project_key=key)
    OnboardingArtifactStore(workspace, key, root).publish_snapshot(snapshot)
    store = OnboardingObservationStore(workspace, key, root, "job-1", snapshot.snapshot_id)
    path = store.append({"tool_call_id": "obs-1", "snapshot_id": snapshot.snapshot_id, "tool": "list_tree"})
    expected = workspace / "state" / "projects" / key / "onboarding" / "jobs" / "job-1" / "observations.jsonl"
    assert path == expected
    assert store.read_all() == [{"snapshot_id": snapshot.snapshot_id, "tool": "list_tree", "tool_call_id": "obs-1"}]
    after = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert after == before


def test_store_rejects_wrong_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = tmp_path / "repo"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    key = import_project(workspace, str(root))["project"]["project_key"]
    store = OnboardingObservationStore(workspace, key, other, "job-1", "snap-1")
    with pytest.raises(ProjectImportError, match="does not match"):
        store.append({"tool_call_id": "obs-1"})
~~~

Add named tests `test_artifact_store_publishes_immutable_snapshot_and_inventory`,
`test_artifact_store_rejects_inventory_from_another_snapshot`,
`test_artifact_store_rejects_conflicting_republication`,
`test_observation_store_rejects_another_snapshot`,
`test_store_rejects_invalid_job_id`, `test_store_rejects_onboarding_symlink`,
`test_store_detects_directory_replacement`,
`test_concurrent_appends_are_complete_json_records`,
`test_append_fsyncs_before_return`, and
`test_read_all_reports_malformed_json_line`. Assert the canonical artifact paths
are `onboarding/snapshots/<snapshot-id>.json` and
`onboarding/inventories/<snapshot-id>.json`. Republishing the same
identity-bearing snapshot is idempotent even when a fresh capture has a different
`captured_at`; a payload whose recomputed digest does not match its filename
fails closed. Every observation record must match the store's constructor-bound
snapshot.

- [ ] **Step 2: Run and verify RED**

~~~bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_onboarding_store.py -q
~~~

Expected: module import failure.

- [ ] **Step 3: Expose a bound project-state context manager**

Add bound_project_state_directory(workspace, project_key, root, relative_parts,
create) to project_import.py. Normalize the key, validate workspace/root
isolation, hold _workspace_import_lock, validate readiness through
ProjectReadinessStore(workspace, workspace_fd), then open
state/projects/KEY/relative_parts using _open_directory_at.

Reject empty, dot-like, slash-containing, or non-[A-Za-z0-9._-]+ parts. Keep all
descriptors open, verify every parent/name identity at exit, close in reverse,
and translate ELOOP/ENOTDIR through the existing isolation error. This helper
returns only a GoodTeam state descriptor, never a target descriptor.

- [ ] **Step 4: Implement immutable artifact and observation stores**

Use these exact path functions:

~~~python
def snapshot_path(workspace: Path, project_key: str, snapshot_id: str) -> Path:
    return (
        workspace / "state" / "projects" / normalize_project_key(project_key) /
        "onboarding" / "snapshots" / f"{snapshot_id}.json"
    )


def inventory_path(workspace: Path, project_key: str, snapshot_id: str) -> Path:
    return (
        workspace / "state" / "projects" / normalize_project_key(project_key) /
        "onboarding" / "inventories" / f"{snapshot_id}.json"
    )


def observation_path(workspace: Path, project_key: str, job_id: str) -> Path:
    return (
        workspace / "state" / "projects" / normalize_project_key(project_key) /
        "onboarding" / "jobs" / job_id / "observations.jsonl"
    )
~~~

`OnboardingArtifactStore.publish_snapshot()` validates schema, project key,
canonical root, root identity, and recomputed snapshot ID before publishing
sorted compact JSON. `publish_inventory()` requires the matching snapshot
artifact, validates schema, snapshot ID, and recomputed inventory digest, then
publishes the inventory. Open temporary and destination files relative to the
bound state descriptor with `O_NOFOLLOW`; fsync file and parent directory before
return. Validate IDs before using them as path components. Publication is
create-only: an existing snapshot with the same identity-bearing fields is
idempotent regardless of `captured_at`; any digest, project, root, schema, or
inventory collision raises `onboarding_artifact_conflict` without replacement.

`OnboardingObservationStore` requires `snapshot_id` at construction and verifies
that the immutable snapshot artifact exists. append() rejects a missing or
different record snapshot, opens the bound job directory, then observations.jsonl with
O_WRONLY|O_CREAT|O_APPEND|O_NOFOLLOW and mode 0600. Serialize sorted compact JSON
plus LF, write with a complete-write loop, and fsync. Use a process-local
workspace/project/job lock in addition to workspace flock. read_all() uses the
same binding, O_RDONLY|O_NOFOLLOW, regular-file verification, and JSON-object
validation with line numbers.

- [ ] **Step 5: Run tests, ruff, and commit**

~~~bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_onboarding_store.py tests/test_project_import.py -q
.venv/bin/ruff check src/goodteam_os/onboarding_store.py src/goodteam_os/project_import.py tests/test_onboarding_store.py
git add src/goodteam_os/project_import.py src/goodteam_os/onboarding_store.py tests/test_onboarding_store.py
git commit -m "feat(onboarding): persist bound onboarding artifacts"
~~~

Expected: all commands pass.

### Task 5: Add provider budgets and observation envelopes

**Files:**
- Create: src/goodteam_os/repository_context_provider.py
- Create: tests/test_repository_context_provider.py

- [ ] **Step 1: Write failing budget and allowlist tests**

Define a fixture that imports a root, captures and publishes its immutable
snapshot, creates `OnboardingObservationStore` for job-1 bound to that snapshot,
creates `OnboardingBudget`, and returns `RepositoryContextProvider`. The provider
constructor must reject any mismatch among the repository snapshot, persisted
snapshot artifact, and observation-store snapshot. Add:

~~~python
def test_provider_rejects_unknown_tool(provider, observation_store) -> None:
    with pytest.raises(ContextProviderError) as captured:
        provider.call("run_command", {"command": "pytest"}, model_turn=1)
    assert captured.value.code == "context_tool_not_allowed"
    records = observation_store.read_all()
    assert len(records) == 1
    assert records[0]["status"] == "denied"
    assert records[0]["tool"] == "run_command"
    assert records[0]["observations"] == []


def test_budget_fails_closed_at_hard_limits() -> None:
    limits = OnboardingBudgetLimits(max_tool_calls=1, max_total_observation_chars=5)
    budget = OnboardingBudget(limits)
    budget.begin_tool_call("read_file_excerpt")
    budget.record_observation("README.md", 5)
    with pytest.raises(ContextProviderError) as calls:
        budget.begin_tool_call("list_tree")
    assert calls.value.code == "context_tool_limit_exceeded"
    with pytest.raises(ContextProviderError) as chars:
        budget.record_observation("README.md", 1)
    assert chars.value.code == "onboarding_budget_exhausted"
~~~

Add named tests `test_budget_counts_unique_files_once`,
`test_budget_enforces_cumulative_per_file_chars`,
`test_budget_uses_injected_monotonic_clock`,
`test_budget_replaces_estimates_with_authoritative_model_usage`,
`test_budget_rejects_limits_above_server_ceiling`,
`test_budget_requests_close_at_eighty_percent`, and
`test_success_envelope_is_exactly_persisted`. The last test calls a real allowed
tool with `model_turn=2`, compares the returned dictionary to the last JSONL
record, and asserts this exact key set:

~~~python
{
    "schema", "tool_call_id", "tool", "snapshot_id", "model_turn",
    "request", "status", "truncated", "limits", "observations",
    "response_metadata", "budget", "created_at",
}
~~~

- [ ] **Step 2: Run and verify RED**

~~~bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_repository_context_provider.py -q
~~~

Expected: module import failure.

- [ ] **Step 3: Implement limits, accounting, and exact dispatch**

Use:

~~~python
@dataclass(frozen=True)
class OnboardingBudgetLimits:
    max_model_turns: int = 24
    max_tool_calls: int = 80
    max_files_observed: int = 120
    max_total_observation_chars: int = 500_000
    max_single_result_chars: int = 16_000
    max_file_cumulative_chars: int = 48_000
    max_elapsed_seconds: float = 900.0
    max_estimated_input_tokens: int = 180_000
    max_estimated_output_tokens: int = 30_000
    max_cost_usd: float = 5.0
~~~

Treat these values as immutable server ceilings. A caller may supply a separate
requested limits instance at or below every ceiling; reject zero, negative,
non-finite, wrong-type, or above-ceiling values with
`onboarding_budget_invalid` instead of silently widening or clamping them.

Add ContextProviderError(code, message, details). OnboardingBudget exposes
`begin_model_turn(turn_number)`, `begin_tool_call`, `result_char_allowance`,
`record_observation`, `record_model_usage(turn_number, ...)`, and `as_dict`.
Model turn numbers start at one, may repeat across multiple tool calls in the same
turn, and must otherwise increase by one; unique turns enforce
`max_model_turns`. Count all redacted repository-derived string leaves toward
total/result characters and only excerpt/match text toward per-file characters.
Guard accounting with a lock, check or reserve before work, never return negative
allowance, replace estimated usage when authoritative adapter usage arrives, and
set close_requested when any dimension reaches 80 percent.

Register exactly list_tree, search_text, read_file_excerpt, inspect_manifest,
inspect_git_history, inspect_tests, and inspect_recent_changes. Never use getattr
dispatch or accept a callable/argv. `call(tool, request, *, model_turn)` requires
a positive model turn, verifies the immutable snapshot artifact and current
repository snapshot before and after the handler, and creates a
`repository_observation.v1` envelope. It includes the redacted full request,
tool-call ID, tool, snapshot, model turn, status, truncation, applied limits,
observations, response metadata (`observation_count` and returned character
count), current budget, and UTC timestamp. Assign every result item a stable
`observation_id` derived from the tool-call ID plus its zero-based index so later
claim evidence can cite one exact item. Persist the exact success dictionary
before returning it. Denied tools and handler/budget failures persist a
snapshot-bound `denied` or `error` envelope with no observations and then raise;
they never append a success record or expose repository bytes. Exception
messages and details use stable codes plus redacted paths/limits only; never copy
raw repository text, process stdout/stderr, or an unredacted request into an
error.

- [ ] **Step 4: Run focused tests and ruff**

~~~bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_repository_context_provider.py -q -k "budget or unknown_tool or envelope"
.venv/bin/ruff check src/goodteam_os/repository_context_provider.py tests/test_repository_context_provider.py
~~~

Expected: all pass.

- [ ] **Step 5: Commit**

~~~bash
git add src/goodteam_os/repository_context_provider.py tests/test_repository_context_provider.py
git commit -m "feat(onboarding): enforce context provider budgets"
~~~

### Task 6: Implement bounded content tools and redaction

**Files:**
- Modify: src/goodteam_os/repository_context_provider.py
- Modify: tests/test_repository_context_provider.py

- [ ] **Step 1: Add failing content-tool tests**

~~~python
def test_read_excerpt_returns_lines_and_redacts_secrets(provider) -> None:
    result = provider.call("read_file_excerpt", {
        "path": "src/settings.py",
        "start_line": 2,
        "line_count": 3,
    }, model_turn=1)
    item = result["observations"][0]
    assert (item["start_line"], item["end_line"]) == (2, 4)
    assert "sk-live-secret" not in item["text"]
    assert "<redacted" in item["text"]
    assert item["content_hash"].startswith("sha256:")


def test_list_tree_is_bounded_and_excludes_generated_content(provider) -> None:
    result = provider.call(
        "list_tree", {"path": "", "depth": 2, "max_entries": 3}, model_turn=1
    )
    paths = [item["path"] for item in result["observations"]]
    assert len(paths) == 3
    assert not any(path.startswith("node_modules/") for path in paths)
    assert result["truncated"] is True


def test_literal_search_returns_bounded_matches(provider) -> None:
    result = provider.call("search_text", {
        "path": "src",
        "query": "create_order",
        "mode": "literal",
        "max_matches": 5,
    }, model_turn=1)
    assert len(result["observations"]) <= 5
    assert all({"path", "line", "text", "content_hash"} <= set(item) for item in result["observations"])
~~~

Add named tests `test_excerpt_rejects_invalid_line_range`,
`test_search_rejects_empty_or_oversized_query`, `test_regex_search_times_out`,
`test_tools_reject_symlink_and_binary_files`,
`test_tools_reject_sensitive_file_names`, `test_result_is_truncated_to_allowance`,
`test_repeated_excerpt_exhausts_per_file_budget`,
`test_authorization_and_bare_tokens_are_redacted`,
`test_structured_values_are_recursively_redacted`, and
`test_returned_and_persisted_redaction_are_equal`. Assert a rejected or redacted
secret never appears in the return value or any `observations.jsonl` bytes.

- [ ] **Step 2: Run and verify RED**

~~~bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_repository_context_provider.py -q -k "excerpt or list_tree or search"
~~~

Expected: handler-not-implemented failures.

- [ ] **Step 3: Implement content policy and handlers**

Apply tree depth 6, tree entries 500, search matches 100, query length 256, and
file read 256 KiB. Reject `.env`, `.env.*`, `.npmrc`, `.pypirc`, `.netrc`,
`credentials.json`, `credentials.*`, `secrets.*`, `id_rsa`, `id_ed25519`,
`*.pem`, `*.key`, `*.p12`, and `*.pfx` before opening. Record only a stable
`context_path_rejected` error with reason `sensitive_path` and the redacted
relative path.

These are server ceilings, not defaults the model can override. Reject missing,
wrong-type, zero/negative, or above-ceiling request bounds with
`context_tool_limit_exceeded`; never silently clamp a broad request into a call
whose effective scope the model cannot see.

Use one redactor for both returned and persisted text:

~~~python
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|"
    r"cookie|password|passwd|secret|client[_-]?secret)\b"
    r"(\s*[:=]\s*)(?:[\"'][^\"'\r\n]*[\"']|[^\s,;#]+)"
)
AUTHORIZATION_VALUE = re.compile(
    r"(?im)(authorization\s*[:=]\s*)(?:bearer|basic)\s+[^\s,;]+"
)
TOKEN_SHAPE = re.compile(
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|"
    r"sk_(?:live|test)_[A-Za-z0-9_]{8,}|"
    r"gh[opusr]_[A-Za-z0-9]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{12,}|AKIA[A-Z0-9]{16})\b"
)


def redact_observation_text(value: str, *, limit: int) -> tuple[str, bool]:
    masked = AUTHORIZATION_VALUE.sub(r"\1<redacted>", value)
    masked = SECRET_ASSIGNMENT.sub(r"\1\2<redacted>", masked)
    masked = TOKEN_SHAPE.sub("<redacted-token>", masked)
    return masked[:limit], len(masked) > limit
~~~

Add `redact_observation_value()` to recurse through string leaves in request
objects and structured observations, so manifest scripts, commit messages, and
diff lines follow the same policy as excerpts. Tree and excerpt handlers use
AnchoredRepository. Hash exact unredacted excerpt bytes, but return and persist
only redacted text plus `redaction_applied`; do not retain the raw text in an
exception, debug field, or temporary ledger object.

Literal search uses anchored walking and reads. Regex search uses allowlisted
rg --json --no-follow, fixed exclusions, no shell, streamed bounded output, and a
two-second timeout. Terminate at match/character limits and verify root before and
after. Buffer the bounded, redacted result until post-call snapshot verification
succeeds; repository bytes must not reach the caller after a drift failure. If rg
is unavailable, only regex mode returns context_tool_unsupported. Every search
match records path, exact line range, and a SHA-256 hash of the matching
unredacted line bytes.

- [ ] **Step 4: Run provider/access tests and ruff**

~~~bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_repository_context_provider.py tests/test_repository_access.py -q
.venv/bin/ruff check src/goodteam_os/repository_context_provider.py tests/test_repository_context_provider.py
~~~

Expected: all pass.

- [ ] **Step 5: Commit**

~~~bash
git add src/goodteam_os/repository_context_provider.py tests/test_repository_context_provider.py
git commit -m "feat(onboarding): add bounded repository content tools"
~~~

### Task 7: Implement manifest, test, and read-only Git tools

**Files:**
- Modify: src/goodteam_os/repository_context_provider.py
- Modify: tests/test_repository_context_provider.py

- [ ] **Step 1: Add failing structured and Git tests**

~~~python
def test_manifest_is_data_and_never_executed(provider, project_command_marker) -> None:
    result = provider.call("inspect_manifest", {"path": "package.json"}, model_turn=1)
    manifest = result["observations"][0]
    assert manifest["kind"] == "package.json"
    assert manifest["scripts"]["test"] == "vitest"
    assert "write-marker" in manifest["scripts"]["postinstall"]
    assert not project_command_marker.exists()


def test_test_inspection_returns_candidates(provider) -> None:
    result = provider.call(
        "inspect_tests", {"path": "", "max_files": 20}, model_turn=1
    )
    assert all(item["classification"] == "test_candidate" for item in result["observations"])
    assert any(item["path"] == "tests/test_orders.py" for item in result["observations"])


def test_git_history_uses_read_only_argv(provider_factory) -> None:
    recorded = []
    real_popen = subprocess.Popen

    def recording_process(command, **kwargs):
        recorded.append(command)
        return real_popen(command, **kwargs)

    provider = provider_factory(process_factory=recording_process)
    result = provider.call(
        "inspect_git_history", {"path": "src", "limit": 5}, model_turn=1
    )
    assert len(result["observations"]) <= 5
    forbidden = {"add", "commit", "checkout", "reset", "clean"}
    assert recorded and all(command[0] in {"git", "rg"} for command in recorded)
    assert not any(word in forbidden for command in recorded for word in command)
~~~

The provider fixture's package manifest includes an inert `postinstall` string
that would create `project_command_marker` if run; no monkeypatch of global
`subprocess.run` is used because snapshot verification legitimately executes
allowlisted read-only Git inspection. Add named tests
`test_inspect_pyproject_allowlists_fields`, `test_inspect_cargo_allowlists_fields`,
`test_inspect_go_mod_conservatively`, `test_invalid_manifest_returns_invalid`,
`test_non_git_inspection_is_unsupported`, `test_git_history_is_path_scoped`,
`test_recent_changes_covers_staged_unstaged_and_untracked`,
`test_git_output_is_bounded`, `test_invalid_revision_is_rejected`,
`test_git_process_is_terminated_on_timeout`, and
`test_all_structured_tools_leave_target_bytes_and_status_unchanged`.

- [ ] **Step 2: Run and verify RED**

~~~bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_repository_context_provider.py -q -k "manifest or inspect_tests or git_history or recent_changes"
~~~

Expected: missing-handler failures.

- [ ] **Step 3: Implement manifest and test inspection**

Parse known JSON/TOML manifests with json or tomllib and parse `go.mod` with a
conservative line parser. Return only package/module names,
package manager, inert script strings, dependency names, workspace/target names,
build backend, tool names, Go version, and feature names. Invalid content returns
status=invalid without returning raw parser input. Apply recursive redaction to
every returned string. Never invoke scripts, interpreters, package managers,
build backends, plugins, or manifest-declared commands. Each manifest observation
includes its relative path and hash of the exact unredacted manifest bytes.

Classify only conventional tests under tests, test, __tests__, *_test.py,
test_*.py, *.test.*, and *.spec.*. Label every result test_candidate and apply
file and budget limits through anchored traversal. Include `basis:
path_convention`; do not imply that a candidate was parsed or covers a module.

- [ ] **Step 4: Implement fixed read-only Git inspection**

Use one private operation dispatcher that constructs argv internally. Never
accept raw argv. Keep an optional `process_factory` dependency injection on the
provider constructor for tests; callers and tool requests cannot set it. Validate
limits in 1..50, canonicalize path, and accept since_sha only as 7-40 hexadecimal
characters. Allow only internally built `git log`,
`git diff --no-ext-diff --no-textconv`, and
`git ls-files --others --exclude-standard` families, always using `--` before
pathspecs. Disable pagers and color and do not honor manifest scripts or Git
aliases as operation names.

Set GIT_OPTIONAL_LOCKS=0, GIT_TERMINAL_PROMPT=0, and LC_ALL=C. Run with
`subprocess.Popen`, no shell, pipes, and a five-second deadline; read at most the
operation's output allowance plus one byte, terminate then kill on timeout or
overflow, and never accumulate unbounded output in memory. Verify root identity
and snapshot before launch and after process exit, then redact buffered output
before constructing observations. History items include commit SHA; change items
include path, old/new blob identity when available, and hash of exact unredacted
bounded diff bytes. Non-Git returns context_tool_unsupported.

- [ ] **Step 5: Run tests, ruff, and commit**

~~~bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_repository_context_provider.py tests/test_repository_snapshot.py tests/test_onboarding_store.py -q
.venv/bin/ruff check src/goodteam_os/repository_context_provider.py tests/test_repository_context_provider.py
git add src/goodteam_os/repository_context_provider.py tests/test_repository_context_provider.py
git commit -m "feat(onboarding): add structured repository inspection tools"
~~~

Expected: all commands pass.

### Task 8: Document and verify the inactive foundation

**Files:**
- Create: docs/repository-context-provider.md
- Modify: docs/readiness-executors.md

- [ ] **Step 1: Write the source-level guide**

Create docs/repository-context-provider.md with sections for Track 1 status,
target-root boundary, snapshot identity and completeness, seven tools and limits,
immutable workspace snapshot/inventory artifacts, snapshot-locked audit ledger,
stable errors, and Track 2 handoff. State explicitly that Track 1 invokes no
model, executes no project command, and does not satisfy AI onboarding.

Update docs/readiness-executors.md to link the guide and explain that schema 2
still calls the compatibility wrapper. Do not edit generated OpenWiki pages.

- [ ] **Step 2: Run focused Track 1 validation**

~~~bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/test_repository_access.py \
  tests/test_repository_snapshot.py \
  tests/test_repository_inventory.py \
  tests/test_onboarding_store.py \
  tests/test_repository_context_provider.py \
  tests/test_project_readiness_executor.py \
  tests/test_project_readiness.py \
  tests/test_project_readiness_web.py \
  tests/test_project_import.py -q
~~~

Expected: all focused access, provider, import, and readiness tests pass.

- [ ] **Step 3: Run full control-plane validation**

~~~bash
PYTHONPATH=src .venv/bin/python -m pytest tests -q
.venv/bin/ruff check src/goodteam_os tests
git diff --check
~~~

Expected: full backend passes, ruff is clean, and diff check passes. Frontend
checks are not required because Track 1 changes no API or browser contract.

- [ ] **Step 4: Run isolated read-only smoke**

Create a disposable Git fixture and GoodTeam workspace under /private/tmp. Import
it, capture before target bytes and Git status, publish one snapshot and its
inventory, call all seven tools with explicit model-turn numbers, and capture
after state. Assert target bytes and Git status are identical, every successful
result has the captured snapshot ID, snapshot and inventory files contain that
ID, ledger success count equals successful call count, denied/error calls expose
no observations, and an inert manifest marker proves no project command ran.
Record fixture paths and output in PR verification. Remove only the disposable
smoke paths created by this step.

- [ ] **Step 5: Commit documentation**

~~~bash
git add docs/repository-context-provider.md docs/readiness-executors.md
git commit -m "docs(onboarding): document context provider foundation"
~~~

The PR must report Tier 3, contract area "repository identity, bounded target
reads, observation provenance, and readiness compatibility", all commands run,
the isolated fixture, and that real-model Tier 4 smoke belongs to Track 2.

## Track 1 Completion Checklist

- [ ] Exactly seven allowlisted tools exist; there is no shell, project command,
  reflection, raw argv, or unrestricted path escape hatch.
- [ ] Every returned and persisted observation carries one verified snapshot ID.
- [ ] The referenced immutable snapshot and inventory artifacts are present in
  the bound GoodTeam project state and their digests validate.
- [ ] Target content uses descriptor anchoring or fixed read-only provider tools
  with snapshot verification before and after.
- [ ] Returned and persisted text share identical redaction.
- [ ] Inventory extraction preserves schema-2 readiness behavior.
- [ ] Snapshot, inventory, and observation artifacts exist only under the
  GoodTeam workspace.
- [ ] Budgets fail closed before returning over-limit results.
- [ ] Focused tests, full backend, ruff, diff check, and isolated read-only smoke
  pass with recorded evidence.
