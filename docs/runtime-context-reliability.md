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

Local checks use the working-tree patch on base
`16e0d57415deeeaf80e17c60645677820b21ef97`:

- The three new regression cases failed on the original implementation. The
  Worker checks now pass for both transports; the context regeneration case
  passes with a same-size/same-mtime source edit.
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_execution_sandbox_integration.py -q --tb=short`:
  3 passed after updating its process double to accept and assert the new
  `start_new_session` transport argument.
- `npm --prefix frontend test` with Node 24.22.1: 12 Node and 313 Vitest checks
  passed. Frontend lint and build passed; existing bundle-size and ineffective
  dynamic-import warnings remain.
- `.venv/bin/ruff check src/goodteam_os tests/` and `git diff --check`: passed.
- `env -u GOODTEAM_DATABASE_URL -u GOODTEAM_TEST_DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests -q --tb=short`:
  409 passed, 12 skipped, 1 failed in 536.03 seconds, with 8 existing fork
  deprecation warnings. The only failure was the sandbox test double's old
  signature described above. Its three-case file passed after that test-only
  repair; production code was unchanged. This is a full run plus a focused
  repair check, not one completely green full-suite invocation. The two-round
  continuation, outcome-discovery and recovery scenarios passed in this run.

The 12 PostgreSQL integration cases require a dedicated disposable database and are
skipped when `GOODTEAM_TEST_DATABASE_URL` is absent. The test command unsets the
production DB setting to keep all other checks inside disposable workspaces.
No paid provider or deployed browser smoke is required for these backend-only
changes; the process regression uses actual local offline tool processes.
GitHub CI remains paused; no candidate publication or Actions dispatch is part
of this change.
