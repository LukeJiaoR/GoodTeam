# Repository Context Provider

Track 1 is an inactive foundation for existing-project onboarding. It captures and stores a bounded repository snapshot, derives a bounded inventory, and exposes a read-only `RepositoryContextProvider` that can answer seven repository-inspection tool calls against a fixed snapshot. Track 1 does not itself make a project ready based on model understanding.

## Track 1 status and negative scope

Track 1 currently provides:

- descriptor-anchored target access for bounded reads;
- deterministic `repository_snapshot.v1` capture and verification;
- deterministic `repository_inventory.v1` extraction;
- immutable snapshot and inventory publication inside the GoodTeam workspace;
- snapshot-locked observation ledgering for seven public context tools.

Track 1 also deliberately stops there.

**Current non-goals, enforced by source and tests:**

- Track 1 invokes no model.
- Track 1 executes no project command and writes nothing to the imported target.
- Track 1 does not produce `project_profile.v1`.
- Track 1 does not change readiness schema 2 or add a new readiness step.
- Track 1 does not claim AI onboarding completion.
- Track 1 does not make an old project ready based on model understanding.

Tracks 2 and later must supply the missing orchestration and authority: a
Project Onboarding Worker and model tool loop, claim/evidence production,
`accept_limited`, profile review, and the readiness transition. Those later
tracks must not reinterpret a Track 1 inventory or observation ledger as proof
that AI onboarding completed.

The current readiness path still uses bounded inventory extraction plus explicit human confirmation. In `src/goodteam_os/project_readiness_executor.py`, `scan_existing_project()` is only a compatibility wrapper over `capture_repository_snapshot()` and `build_repository_inventory()`.

## Target-root boundary and descriptor anchoring

The provider is bound to one imported project key, one canonical root, one root device/inode identity, one onboarding job, and one `snapshot_id`.

The boundary is enforced in two layers:

- `AnchoredRepository` opens the imported root with descriptor anchoring and rejects non-canonical paths, symlinks, and root replacement.
- `verify_repository_snapshot()` re-captures the repository and fails if the bound root or its snapshot identity drifts.

Tests in `tests/test_repository_access.py`, `tests/test_repository_snapshot.py`, `tests/test_onboarding_store.py`, and `tests/test_repository_context_provider.py` cover the current fail-closed rules:

- repository-relative paths must be canonical;
- the reader never follows symlinks, including intermediate symlinks;
- sensitive paths such as `.env`, `credentials.*`, `secrets.*`, `id_rsa`, `id_ed25519`, `*.pem`, `*.key`, `*.p12`, and `*.pfx` are rejected or omitted;
- target writes stay out of the imported repository and land only in the GoodTeam workspace state tree.

## Repository snapshot identity, scope, completeness, and drift

`capture_repository_snapshot()` publishes `repository_snapshot.v1` with:

- `snapshot_id`;
- `project_key`;
- `canonical_root`;
- `root_identity` (`device`, `inode`);
- `source_control`;
- `content_scope`;
- `path_manifest`;
- `captured_at`.

The snapshot identity is deterministic over the published identity payload, not over `captured_at`. `compute_repository_snapshot_id()` strips `snapshot_id` and `captured_at` before hashing.

The current snapshot behavior is:

- clean Git repositories produce deterministic snapshot IDs;
- dirty Git state changes snapshot identity across clean, unstaged, staged, and untracked variants;
- non-Git repositories still produce deterministic snapshot IDs with `source_control.kind == "none"`;
- drift after capture fails closed as `repository_snapshot_changed`;
- snapshot capture itself fails closed if two consecutive captures differ during the same attempt.

`content_scope.complete` and the `path_manifest` describe whether the snapshot fully represented the eligible content under the configured snapshot limits:

- `max_files = 20_000`
- `max_total_bytes = 512 * 1024 * 1024`
- `max_file_bytes = 8 * 1024 * 1024`

The manifest distinguishes represented entries from excluded or omitted paths. Tests cover deterministic identity, dirty-state sensitivity, and drift rejection in `tests/test_repository_snapshot.py`.

## Seven public tools and their current limits

`RepositoryContextProvider.tool_names()` currently exposes exactly seven public tools:

1. `list_tree`
2. `search_text`
3. `read_file_excerpt`
4. `inspect_manifest`
5. `inspect_git_history`
6. `inspect_tests`
7. `inspect_recent_changes`

The provider rejects any other tool name as `context_tool_not_allowed`.

All successful results are returned and persisted as `repository_observation.v1` envelopes bound to the provider snapshot. Every envelope carries the same `snapshot_id`, the sanitized request, the current budget snapshot, and redacted observations. Denied and error envelopes persist zero observations.

### Shared provider budget ceilings

The provider enforces the current server ceilings from `src/goodteam_os/repository_context_provider.py`:

- `max_model_turns = 24`
- `max_tool_calls = 80`
- `max_files_observed = 120`
- `max_total_observation_chars = 500000`
- `max_single_result_chars = 16000`
- `max_file_cumulative_chars = 48000`
- `max_elapsed_seconds = 900.0`
- `max_estimated_input_tokens = 180000`
- `max_estimated_output_tokens = 30000`
- `max_cost_usd = 5.0`

The budget closes early when any tracked dimension reaches 80% of its ceiling. Track 1 never records model usage itself, but the budget object already reserves space for later authoritative token and cost accounting.

### `list_tree`

Purpose: return bounded tree entries from a repository-relative path.

Request shape:

- `path` optional, defaults to `""`
- `depth` optional, defaults to `1`, maximum `6`
- `max_entries` optional, defaults to `10`, maximum `500`

Current behavior:

- skips default ignored directories such as `.git`, `.venv`, `node_modules`, `dist`, and `build`;
- omits sensitive paths without leaking their names;
- returns `tree_entry` observations with `path` and `entry_kind`;
- truncates honestly when server/result limits are reached.

### `search_text`

Purpose: search text under a repository-relative path.

Request shape:

- `path` required
- `query` required, non-empty, maximum length `256`
- `mode` required, `literal` or `regex`
- `max_matches` required, maximum `100`

Current behavior:

- literal mode walks and reads bounded files inside the anchored repository;
- regex mode shells out only to `rg --json` with `--no-follow` and exclusion globs for ignored and sensitive paths;
- regex mode fails as `context_tool_unsupported` when `rg` is unavailable;
- regex mode times out after 2.0 seconds;
- both modes redact returned matches and omit sensitive paths.

Literal mode also derives tighter per-call scan caps from the result allowance:

- walked entries: `min(500, max_matches * 4, max(1, allowance // 24))`
- scanned files: `min(walk_cap, max_matches * 2, max(1, allowance // 40))`
- read bytes: `min(256 KiB, max(1, allowance * 4))`

### `read_file_excerpt`

Purpose: return a bounded line range from one file.

Request shape:

- `path` required
- `start_line` required, positive integer
- `line_count` required, positive integer

Current behavior:

- reads through `AnchoredRepository.read_text()` with `max_bytes = 256 KiB`;
- rejects requests that extend past the available lines as `context_tool_limit_exceeded`;
- returns one `file_excerpt` observation with `path`, `start_line`, `end_line`, redacted `text`, `content_hash`, and `redaction_applied`.

### `inspect_manifest`

Purpose: parse one supported manifest file as data.

Request shape:

- `path` required

Supported manifest names:

- `package.json`
- `pyproject.toml`
- `Cargo.toml`
- `go.mod`

Current behavior:

- reads through `AnchoredRepository.read_text()` with `max_bytes = 256 KiB`;
- never executes manifest hooks, scripts, package managers, or build backends;
- returns `unsupported` for other paths;
- returns status `invalid` with zero observations for truncated or unparseable supported manifests;
- keeps the parsed shape bounded by the provider result allowance.

### `inspect_git_history`

Purpose: inspect bounded commit history for a Git-backed snapshot.

Request shape:

- `path` optional, defaults to `""`
- `limit` optional, defaults to `20`, maximum `50`

Current behavior:

- only works when `snapshot.source_control.kind == "git"`;
- runs read-only `git log` with hooks disabled, prompts disabled, and global/system Git config suppressed;
- enforces a 5.0 second timeout;
- treats combined process output above the current allowance as `context_tool_limit_exceeded`;
- returns `git_commit` observations with `commit_sha`, `committed_at`, and redacted `message`.

### `inspect_tests`

Purpose: return bounded test-path candidates by convention.

Request shape:

- `path` optional, defaults to `""`
- `max_files` optional, defaults to `20`, maximum `50`

Current behavior:

- walks up to `max(max_files * 20, max_files + 1)` entries;
- classifies files as `test_candidate` only from path conventions;
- does not run any test command or parse test semantics;
- omits sensitive paths.

### `inspect_recent_changes`

Purpose: inspect bounded staged, unstaged, untracked, or since-revision changes for a Git-backed snapshot.

Request shape:

- `path` optional, defaults to `""`
- `limit` optional, defaults to `20`, maximum `50`
- `since_sha` optional, when present must match a 7-40 hex revision

Current behavior:

- only works for Git-backed snapshots;
- shells out to read-only `git diff` and `git ls-files --others --exclude-standard`;
- enforces a 5.0 second timeout;
- omits sensitive paths before diff retrieval;
- returns `git_change` observations with `path`, `change_scope`, `old_blob`, `new_blob`, `diff_hash`, redacted `diff`, `diff_content_kind`, and `diff_truncated`.

### Fixed system Git boundary

Snapshot capture does not resolve Git from caller-controlled `PATH`. It considers
only `/usr/bin/git` and `/bin/git`, canonicalizes the candidate, and requires the
trusted directory chain and executable to be root-owned and not group- or
world-writable. The executable must be a regular executable outside the target
root. Git receives a fixed `/usr/bin:/bin` execution path,
`core.fsmonitor=false`, disabled prompts and optional locks, and isolated
global/system configuration. Snapshot Git output is read through bounded pipes;
stdout and stderr share one aggregate limit, timeout or overflow kills the whole
process group, and target hooks, helpers, aliases, and project commands are not
executed. If a trusted system Git is unavailable, capture fails with the stable
`repository_snapshot_failed` boundary instead of falling back to Homebrew,
`/usr/local`, or the inherited environment.

## Immutable workspace snapshot and inventory artifacts

Track 1 publishes artifacts only under the GoodTeam workspace:

- `state/projects/PROJECT_KEY/onboarding/snapshots/SNAPSHOT_ID.json`
- `state/projects/PROJECT_KEY/onboarding/inventories/SNAPSHOT_ID.json`

`OnboardingArtifactStore` enforces:

- root/project binding before publication;
- immutable JSON publication;
- equivalent snapshot republication allowed only when the stored identity payload matches;
- inventory publication only after the matching snapshot artifact already exists;
- conflicting or malformed existing artifacts fail closed as onboarding artifact errors.

Tests in `tests/test_onboarding_store.py` verify that publication writes nothing into the target root, rejects cross-snapshot inventory publication, and detects malformed or replaced artifact leaves.

## Snapshot-locked JSONL observation ledger and budget accounting

Tool-call records are appended to:

- `state/projects/PROJECT_KEY/onboarding/jobs/JOB_ID/observations.jsonl`

Each record is a `repository_observation.v1` envelope bound to one `snapshot_id`. The observation store rejects appends for another snapshot. The provider also re-verifies the snapshot and the observation-store binding before and after handler execution and before ledger append.

Current envelope properties include:

- `tool_call_id`
- `tool`
- `model_turn`
- `snapshot_id`
- `request`
- `status`
- `observations`
- `truncated`
- `response_metadata`
- `limits`
- `budget`
- `created_at`

Budget accounting is snapshot-scoped and fail-closed:

- successful observations consume total characters and per-file character budgets;
- denied/error envelopes still consume the attempted tool call but store zero observations;
- rejected writes leave budget state unchanged;
- result reservations prevent overcommit until commit or release.

## Stable error families and fail-closed behavior

The current stable families visible from Track 1 are:

- repository access and root/path errors, such as `context_path_rejected`, `repository_root_changed`, and `repository_entry_changed`;
- snapshot errors, especially `repository_snapshot_changed`;
- artifact and ledger errors normalized from project import/store failures, such as `onboarding_snapshot_missing`, `onboarding_artifact_conflict`, `onboarding_observation_invalid`, and `onboarding_snapshot_mismatch`;
- provider request/tool errors, including `context_request_invalid`, `context_tool_not_allowed`, `context_tool_limit_exceeded`, `context_tool_timeout`, `context_tool_invalid_output`, `context_tool_failed`, and `context_tool_unsupported`;
- budget errors, `onboarding_budget_invalid` and `onboarding_budget_exceeded`;
- fallback provider persistence failure, `context_observation_persistence_failed`.

The fail-closed rules verified in current tests are:

- authority/binding failures raise immediately instead of persisting a misleading terminal record;
- denied/error envelopes contain zero observations;
- handler stderr, raw exception strings, raw secret values, and sensitive paths are redacted from returned and persisted data;
- unsupported or malformed handler output is treated as an error, not partial success;
- non-Git snapshots reject Git tools as unsupported rather than inferring replacements.

## Track 2 handoff and deliberately absent pieces

This foundation is intentionally incomplete until a later track adds a real model loop on top of the snapshot-locked provider.

What remains absent today:

- no model-driven tool loop;
- no `project_profile.v1`;
- no model-reviewed command or constraint semantics;
- no readiness schema change beyond the existing schema 2 compatibility path;
- no claim that inventory extraction equals repository understanding;
- no automatic readiness promotion based on model output.

Track 2+ must add the Project Onboarding Worker, authoritative claims and
evidence, `accept_limited`, profile review, and readiness ownership. A later
Worker may use observations as bounded evidence, but before acting on repository
content it must re-read the relevant file from the current repository SHA. It
must not execute or make a readiness decision from stale observation bytes
captured under an older snapshot.

Until those pieces exist, the Repository Context Provider is a bounded, read-only
substrate for future work rather than an active onboarding engine.
