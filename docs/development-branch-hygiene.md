# Development Branch Hygiene

This document defines the default branch and PR cleanup rules for GoodTeam.
It exists to prevent stale feature branches, squash-merged branch confusion, and
large old branches being merged after their useful patch has already landed.

## Core Rule

Every PR merge must end with branch cleanup:

1. Confirm the PR is merged.
2. Delete the remote feature branch.
3. Delete the local feature branch.
4. Remove any temporary worktree for that branch.
5. Prune stale remote-tracking refs or delete them explicitly.
6. Report the final branch, PR, and workspace state.

Do not leave merged branches around as implicit memory. The PR and merge commit
are the durable record.

## Before Merging

Only merge when all of these are true:

- The PR is not draft.
- GitHub reports the PR as mergeable.
- Required checks are complete and successful.
- The PR scope matches the intended change.
- Local or CI verification has been reported honestly.
- The branch does not include unrelated historical commits.

If a branch is old or mixed, do not merge it wholesale. Audit it by patch:

```bash
git log --reverse --oneline origin/main..origin/<branch>
git cherry-pick -n <sha>
git diff --stat
git diff
```

Then reset or use a fresh temporary worktree before the next commit. Keep only
patches that still produce a clear net diff on latest `origin/main`.

## Squash Merge Caveat

Most GoodTeam PRs are squash-merged. After a squash merge, the original branch
commit is often not an ancestor of `origin/main`, so this command can be
misleading:

```bash
git merge-base --is-ancestor origin/<branch> origin/main
```

Use GitHub PR state as the source of truth for a branch that has a PR:

```bash
gh pr list --repo LukeJiaoR/GoodTeam --state all --head <branch>
gh pr view <number> --repo LukeJiaoR/GoodTeam --json state,mergedAt,mergeCommit,statusCheckRollup
```

If the PR is `MERGED`, and the functionality is present on `main`, the branch is
a cleanup candidate even when `merge-base --is-ancestor` returns false.

## Preferred Merge Command

When using GitHub CLI, prefer deleting the remote branch as part of the merge:

```bash
gh pr merge <number> --repo LukeJiaoR/GoodTeam --squash --delete-branch
```

If the remote branch was not deleted during merge, delete it after confirmation:

```bash
git push origin --delete <branch>
```

If SSH is unstable, use the GitHub API:

```bash
gh api -X DELETE repos/LukeJiaoR/GoodTeam/git/refs/heads/<branch>
```

## Local Cleanup

After the remote branch is gone:

```bash
git fetch --prune origin
git branch -d <branch>
```

If the branch was squash-merged, `git branch -d` may refuse because the exact
commit is not an ancestor of `main`. In that case, confirm the PR is merged, then
delete locally:

```bash
git branch -D <branch>
```

If the branch has a worktree, remove the worktree before deleting the branch:

```bash
git worktree remove <path>
git branch -D <branch>
```

Only use `--force` for temporary audit worktrees or explicitly confirmed cleanup.

## Final State Report

Every merge/cleanup turn should report:

- Open PR list.
- Remote branch list or relevant deleted refs.
- Current local branch.
- Dirty workspace status.
- Remaining worktrees that were intentionally kept.
- Any untracked local artifacts that were not deleted.

Do not delete untracked output directories, screenshots, or generated artifacts
unless the user explicitly confirms they are disposable.

## Keep / Merge / Delete Decision

Use this decision rule for old branches:

- **Merge:** the branch has an open PR, green checks, clear scope, and no unrelated history.
- **Cherry-pick:** only a small subset has value; create a clean branch from latest `origin/main`.
- **Delete:** the PR is already merged, the useful patch landed elsewhere, or a per-commit audit shows no net value.
- **Keep:** the branch has active unmerged work, unresolved review/CI, or uncertain product value.

When uncertain, keep the branch and write a short audit note rather than merging
or deleting blindly.
