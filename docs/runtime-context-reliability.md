# Runtime timeout and context freshness

Date: 2026-11-07. Verification tier: Tier 3 (Worker execution boundary).

The project-level outcome is that a later iteration can start from current
repository understanding without a timed-out Worker continuing to write through
its tool processes. The existing queue, dispatch, continuation and delivery
services remain the execution path.

## Changes

Ordinary CLI Workers previously killed only their direct process at the host
deadline. Both pipe and PTY transports now start an owned process group and reuse
the existing Reviewer/correction termination helper. A timeout becomes a failed
result only after that helper confirms the group has exited. A termination error
propagates with the original running attempt unresolved; it does not authorize
an automatic retry or manufacture a successful exit. Partial pipe/PTY output and
the existing timeout/human-handoff classification are retained.

The project understanding cache previously treated any nonempty
`understanding.md` as current. New `sources.yaml` records bind the generated text
and local source content to SHA-256 digests. Directory sources bind the same
bounded directory-tree or code-skeleton representation used for generation.
Project-local references are resolved against the current project root, including
when the context is read in an isolated workspace.

Changed, missing or unverifiable local sources make the cache unavailable to
`collect_project_context`. Reads retain existing files and use current discovered
sources; the existing explicit generation path refreshes the cache. Old cache
records without source digests also require regeneration. URL/inline sources
remain captured snapshots: a cache read does not fetch the network or claim that
remote content was revalidated. Sources are checked again before publishing a
generated entry.

## Verification

The new representative checks reproduce the previous failures:

- `test_cli_timeout_stops_owned_tool_process_before_recording_failure` launches
  real offline parent/tool processes in a disposable project. Both pipe and PTY
  transports must stop them before returning the original timeout result.
- `test_context_cache_invalidates_changed_sources_and_regenerates_from_current_files`
  changes the source README without changing its length or modification time.
  The old cache must leave Workflow context without a read rewriting it, and
  regeneration must supply the new verification command for the next iteration.

The existing project-iteration, outcome-discovery and recovery checks remain the
two-round execution/learning regression boundary. These fixtures do not claim
real-model prioritization acceptance, merge authority or deployment evidence.

Local verification uses base `d4ddab5e35bb528b04261d51b7827de4233e8e29`
and tested source `2155eb68316cbe0835e9c5edac3f4133f22e8559`. The final
follow-up commit changes this verification record only.

- The three new regression cases failed on the original implementation. Both
  timeout transports and same-size/same-mtime cache regeneration now pass.
- `env -u GOODTEAM_DATABASE_URL -u GOODTEAM_TEST_DATABASE_URL PYTHONPATH=src python -m pytest tests -q --tb=short`:
  **410 passed, 16 skipped**, 8 existing fork deprecation warnings, 548.03 seconds.
  The existing two-round continuation, outcome-discovery and recovery scenarios
  passed. The sandbox process double now accepts and asserts `start_new_session`.
- Node 24.22.1: `npm --prefix frontend test` passed (12 Node and 314 Vitest checks).
  `npm --prefix frontend run lint` and `npm --prefix frontend run build` passed;
  existing bundle-size and ineffective dynamic-import warnings remain.
- `ruff check src/goodteam_os tests/` and `git diff --check`: passed.

The 16 PostgreSQL integration cases require a dedicated disposable database and
are skipped when `GOODTEAM_TEST_DATABASE_URL` is absent. The test command unsets
the production DB setting to keep all other checks inside disposable workspaces.
No paid provider or deployed browser smoke was run for these backend-only
changes; the process regression uses actual local offline tool processes.
GitHub CI remains paused; no candidate publication or Actions dispatch is part
of this change. Local results do not claim remote CI or independent review.
