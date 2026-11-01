# GoodTeam Agent Rules

## Project-iteration-first task selection

**One execution path for new Manager work (2026-11-01):** Manager recommendations
are read-only until explicitly queued. Send chosen project task IDs to the
existing Task Assistant queue. A confirmed single draft can use the existing
Workflow; a confirmed batch uses the Task Assistant batch adapter and the
existing `dispatch_tracks` isolated Worker pool (up to 10 concurrently).
The original LocalTaskStore card owns the task; the queue's immutable
confirmation binds its Workflow or dispatch Run. Worker output and fan-in
produce review evidence, NOT automatically done. Manual and authorized
automatic batches use the same queue adapter. Do not create
a second Manager-specific dispatcher, task ledger or execution state machine.
Goal continuation remains an authorized adapter to Workflow; historical
Operator/direct-dispatch entries remain readable and functional. The disconnected
AI Onboarding runner was retired; existing project import/readiness/inventory
are the product's project discovery path. Do not restore the fake-only
parallel-plan projection as a substitute for real isolated concurrent Workers.

The product target is **an autonomous, continuing AI engineering team for an
entire project**, not just an issue executor. The only current two-class
development scope is [#381](https://github.com/LukeJiaoR/GoodTeam/issues/381):
(1) essential for repeatable project-level iterations, or (2) not essential.
Historical design plans, progress logs and model/role playbooks do not create
a parallel work queue.

1. **Essential**: understand an existing repository and its goals, history,
   issues, risks and pending work; Manager autonomously identifies and
   prioritizes the next iteration, chooses and coordinates Workers, verifies
   results through task-relevant tests and independent review/fix, updates
   project memory/backlog, and selects the following iteration based on
   observed outcomes. The project moves across *multiple distinct iterations*
   without the user manually supplying each next Issue. Preserve necessary
   scoped human gates, authorization, failure recovery and honest artifacts.
2. **Not essential**: do not expand optional Telegram, external Planner/MCP,
   complete Skill marketplaces/runtime/version flows, workspace-wide Team
   migrations, mobile extras, billing, broad multi-provider matrices or
   speculative control-plane frameworks unless a demonstrated dependency
   blocks the actual project-iteration loop. Do not delete working shared
   dependencies without proof and regressions.

A new implementation task must name the **project-level user outcome**, the
existing code and observed gap, the smallest change, and a test that shows
the Manager can *pick, execute, learn and pick again*. One successfully
implemented user-specified Issue is necessary evidence, but is **not**
acceptance of project-level autonomy. [#419](https://github.com/LukeJiaoR/GoodTeam/issues/419)
retains the real product acceptance record; [#540](https://github.com/LukeJiaoR/GoodTeam/issues/540)
already implements bounded continuation inside one approved Goal, not
open-ended project prioritization. Prefer extending/reusing these paths and
the existing `fix` profile, not creating a second workflow engine.

Preserve all existing approval, trusted-path, isolation, review provenance,
idempotency, exact-head CI, cost/scope bounds and fail-closed protections.
Within approved project scope, avoid unnecessary per-task user decisions;
outside it, never infer permission to merge, deploy or spend without the
specified authorization.

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

**Lean test budget (2026-10-31):** The owner intentionally reduced test
source by approximately 90%. Keep a single meaningful representative case per
distinct feature contract; avoid rebuilding multiple near-identical permutations
or historical large suites. See `docs/testing-strategy.md` for the retained
smoke inventory. This reduces safety regression coverage, not permissions:
preserve independent review, bounded real-Worker evidence, fail-closed paths,
and explicit merge/release authority. The GitHub CI pause below remains in force.

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

**CI PAUSED — 2026-10-31 (explicit owner decision):** GitHub-hosted automatic
PR CI, candidate-publication and candidate-reconciliation workflows are
temporarily disabled. The manual workflow definitions are retained for later
reactivation. Do **not** publish candidate branches or dispatch manual
GitHub Actions CI for ordinary development during this pause. The
exact-SHA candidate workflow steps below are suspended until the owner
explicitly re-enables CI. Continue focused local/unit, lint, and isolated
smoke checks appropriate to the change; report observed results, skipped
checks and unverified risks truthfully. An old green SHA is never proof for
a new head. Keep approval, isolation, secrets, review and source-identity
requirements. Merging during the pause needs an explicit, evidence-informed
maintainer decision; do not imply that CI passed.

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
