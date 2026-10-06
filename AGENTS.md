# GoodTeam Agent Rules

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
focused local checks required by `docs/testing-strategy.md`. When the branch is
ready to become a pull request candidate:

1. Sync with the current `main`, require a clean working tree, and record `git rev-parse HEAD`. The candidate must contain the current `main` HEAD.
2. Publish that committed SHA to a temporary candidate branch and verify the
   remote ref. Keep an existing PR's head unchanged until validation succeeds.
3. Run the reviewed default-branch workflow against that exact SHA:
   `gh workflow run ci.yml --repo LukeJiaoR/GoodTeam --ref main -f "target_sha=<40-char-sha>"`.
4. Wait for the run to succeed and for commit status
   `merge-candidate/validated` to be green.
5. Only then open/update the PR. Any new commit invalidates the old evidence;
   validate the new SHA before merge. If `main` advances, sync the new base and
   validate the resulting SHA again.

The PR-side `CI / gate` is the authority for whether the current exact head is
eligible to merge. Never copy a status from an older SHA or bypass the gate
because a previous branch state passed.

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

Follow `docs/self-hosted-ci-runner.md` for every workflow, including temporary
diagnostic workflows on feature branches. Read-only Linux x64 diagnostics use
`[self-hosted, linux, x64, goodteam-ci]` with explicit read-only permissions.
Do not create GitHub Actions artifacts for routine CI or source transport; read
source by exact GitHub ref instead. Do not add a GitHub-hosted fallback for
self-hosted code-execution tasks.

OpenWiki stays manual-only on GitHub-hosted runners. Provider secrets,
GitHub Environments, and repository-write tokens must not enter `goodteam-ci`.
Tasks requiring these capabilities need a separately reviewed, isolated runner
environment; none is currently provisioned. The shared x64 runner also cannot
provide native ARM64 or Docker diagnostics.

<!-- OPENWIKI:START -->

## OpenWiki

This repository uses OpenWiki for recurring code documentation. Start with `openwiki/quickstart.md`, then follow its links to architecture, workflows, domain concepts, operations, integrations, testing guidance, and source maps.

The OpenWiki GitHub Actions workflow is manual-only. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting an authorized OpenWiki run regenerate them.

<!-- OPENWIKI:END -->
