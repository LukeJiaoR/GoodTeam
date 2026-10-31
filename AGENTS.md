# GoodTeam Agent Rules

## Delivery-first task selection

The current work scope has **exactly two classes**, recorded in
[GitHub #381](https://github.com/LukeJiaoR/GoodTeam/issues/381)
(2026-10-31 binary pruning). Older plans, strategy notes and progress
logs are history; they are not a parallel task queue.

1. **Required for a real end-to-end delivery**: an existing local Git
   project, Manager-owned intent/constraints, real Worker dispatch,
   bounded execution, independent relevant verification/review/fix,
   accurate artifacts and final human decisions, and safe continuation/
   recovery. Repair concrete failures from [#419](https://github.com/LukeJiaoR/GoodTeam/issues/419)
   and [#540](https://github.com/LukeJiaoR/GoodTeam/issues/540)
   using current source and actual evidence. Prefer an existing workflow
   path (including the existing `fix` profile) over a new execution engine.
2. **Not required for that delivery**: do not initiate or expand it in
   this development line. This includes optional Telegram, external
   Planner/MCP, custom Skill installation/lifecycle and strict Skill
   runtime, workspace-wide Team migration, mobile extras, commercialization,
   universal long-memory work and generalized platform refactoring.
   Already implemented integrations are not permission to strip a shared
   dependency without a tested replacement.

A new implementation task must identify the **real user-facing failing
step, a reproducible exact-version observation, the smallest viable fix,
and how to re-enter the same real delivery**. If there is no such
required outcome or an independently confirmed security defect in the
required path, do not turn speculative hardening into a new task.
Keep the existing approval, trusted-path, isolation, evidence,
idempotency and exact-head CI protections. Pruning is never a
permission bypass, blanket test skip, source deletion or false PASS.

This file contains project-specific rules for automated coding agents working in
this repository.

## Branch And PR Hygiene

Follow `docs/development-branch-hygiene.md` for all branch, PR, and worktree
cleanup.

Default rule: after a PR is merged, delete the remote feature branch, delete the
local feature branch, remove temporary worktrees, and prune stale remote refs.
Report any remaining local artifacts or intentionally retained worktrees.

Do not merge old mixed-history branches wholesale. Audit them commit by commit
against latest `origin/main`, and only keep patches that still have clear net
value.

## Verification Strategy

Follow `docs/testing-strategy.md` when choosing local validation for a PR.
Every PR should name its verification tier, the changed contract area, the
commands that were run, and any skipped validation with a safety reason.

Use focused tests for low-risk changes, but run the full backend suite and
related frontend checks for control-plane work such as execution identity,
ledger writes, approval decisions, evidence snapshots, reviewer provenance,
worker dispatch boundaries, PR/CI binding, or security-sensitive server actions.

Live-path changes need live smoke evidence from an isolated workspace; never run
mutating smoke commands against an active real project workspace.

### Exact-SHA candidate CI

Follow `docs/candidate-validation.md` for publication, dispatch, evidence, and
recovery details.

Do not spend heavy remote CI on every development push. While iterating, run the
focused local checks required by `docs/testing-strategy.md`. Draft PRs may be
opened and updated before candidate CI; a Draft is not evidence of validation or
permission to merge. When preparing the final candidate for merge:

1. Sync with the current `main`, require a clean working tree, and record `git rev-parse HEAD`. The candidate must contain the current `main` HEAD.
2. Publish that committed SHA to a temporary `codex/candidate-*` branch and verify the
   remote ref. Keep an existing ready-for-review PR's head unchanged until
   validation succeeds; Draft PR heads may change during local iteration.
3. Candidate publication automatically dispatches the reviewed `main` workflow
   against that exact SHA. Observe the `Candidate published` and
   `Candidate automation` runs; do not add a mandatory human dispatch step.
   The existing `gh workflow run ci.yml --repo LukeJiaoR/GoodTeam --ref main
   -f "target_sha=<40-char-sha>"` command remains an optional recovery path.
4. Wait for the run to succeed and for commit status
   `merge-candidate/validated` to be green.
5. Only then open a ready-for-review PR, update an existing ready PR, or mark a
   Draft ready with that exact head. Any new commit invalidates the old evidence;
   validate the new SHA before merge. If `main` advances, sync the new base and
   validate the resulting SHA again.

The PR-side `CI / gate` is the authority for whether the current exact head is
eligible to merge. Draft PR events can run this lightweight check and fail while
validation is missing; opening a Draft does not trigger full candidate CI. After
candidate validation, automation refreshes a metadata gate that started before
the successful result. A gate that still fails afterward remains a real blocker.
Never copy a status from
an older SHA or bypass the gate because a previous branch state passed.

Record the candidate SHA, pinned base SHA, run URL, planned job outcomes, and
limitations. A manual run's `headSha` identifies the workflow definition; use
its `target_sha` input and recorded status to identify the tested source.

### Broken default-branch CI

If the reviewed `main` workflow cannot validate its own repair, preserve the
failed result and prepare a minimal CI repair separately from product work.
Follow the recovery procedure in `docs/candidate-validation.md`. Dispatch from
a temporary workflow ref requires explicit user or maintainer authorization for
the reviewed workflow, exact candidate SHA, and current base. Editing these rules
does not grant that authorization. Keep the runner/credential boundary, real
job outcomes, exact-SHA status, and PR gate intact; never fabricate success or
bypass failed checks. Resume normal `--ref main` validation after recovery.

## Local Artifacts

Do not delete untracked local output directories such as `output/` unless the
user explicitly confirms they are disposable.

## CI Runner Selection

Use GitHub-hosted `ubuntu-latest` for every workflow job, including temporary
diagnostic workflows on feature branches. Do not add self-hosted runner
selectors or invoke the retired `goodteam-ci` runner. Keep workflow and job
permissions scoped to each task; code-execution jobs use `contents: read`, and
candidate status writers receive only the status permissions they require.

Do not create GitHub Actions artifacts for routine CI or source transport; read
source by exact GitHub ref instead. OpenWiki stays manual-only on a
GitHub-hosted runner. Keep its provider secrets and repository-write token
scoped to that workflow.

<!-- OPENWIKI:START -->

## OpenWiki

This repository uses OpenWiki for recurring code documentation. Start with `openwiki/quickstart.md`, then follow its links to architecture, workflows, domain concepts, operations, integrations, testing guidance, and source maps.

The OpenWiki GitHub Actions workflow is manual-only. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting an authorized OpenWiki run regenerate them.

<!-- OPENWIKI:END -->
