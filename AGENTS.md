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

## Local Artifacts

Do not delete untracked local output directories such as `output/` unless the
user explicitly confirms they are disposable.
