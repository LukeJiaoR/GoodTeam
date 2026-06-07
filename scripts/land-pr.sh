#!/usr/bin/env bash
#
# land-pr.sh — squash-merge a reviewed PR, then clean up and resync.
#
# The MECHANICAL half of landing a change. The JUDGEMENT half (reviewing the diff,
# deciding it's safe to merge) stays with the human/reviewer — only run this AFTER
# the PR has been reviewed and CI is green.
#
# Usage:
#   scripts/land-pr.sh <PR-number> [--worktree <path>] [--review-rationale <path>] [--no-test]
#
# What it does (idempotent-ish, stops on first failure):
#   1. Verifies the PR's CI checks are green (refuses if not, unless --force).
#   2. gh pr merge <N> --squash --delete-branch
#   3. Removes the given --worktree (if any) and prunes stale worktrees.
#   4. Switches to main, pulls --ff-only.
#   5. Runs the test suite as a post-merge sanity check (skip with --no-test).
#
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"
. "$ROOT/scripts/lib/land-provenance.sh"

PR=""
WORKTREE=""
REVIEW_RATIONALE=""
RUN_TEST=1
FORCE=0
CI_STATUS="unverified"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --worktree) WORKTREE="$2"; shift 2 ;;
    --worktree=*) WORKTREE="${1#*=}"; shift ;;
    --review-rationale) REVIEW_RATIONALE="$2"; shift 2 ;;
    --review-rationale=*) REVIEW_RATIONALE="${1#*=}"; shift ;;
    --no-test) RUN_TEST=0; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) [ -z "$PR" ] && { PR="$1"; shift; } || { echo "unexpected arg: $1" >&2; exit 2; } ;;
  esac
done
[ -n "$PR" ] || { echo "usage: scripts/land-pr.sh <PR-number> [--worktree <path>] [--no-test]" >&2; exit 2; }

echo "[land] PR #$PR — checking CI status..."
if [ "$FORCE" != 1 ]; then
  # gh pr checks exits non-zero if any check is not passing; surface that.
  if ! gh pr checks "$PR" >/tmp/land-checks-$PR.txt 2>&1; then
    echo "[land] CI is not green for PR #$PR — refusing to merge. (--force to override)" >&2
    tail -5 /tmp/land-checks-$PR.txt >&2
    exit 1
  fi
  CI_STATUS="pass"
  echo "[land] CI green."
else
  CI_STATUS="forced-unverified"
fi

# Remove the worktree FIRST: while it's checked out on the PR branch,
# `gh pr merge --delete-branch` cannot delete that local branch.
land_provenance_extract "$PR" "$WORKTREE" || true
if [ -n "$WORKTREE" ] && [ -d "$WORKTREE" ]; then
  echo "[land] removing worktree $WORKTREE ..."
  git worktree remove "$WORKTREE" --force || echo "[land] (worktree remove skipped)"
fi
git worktree prune

echo "[land] squash-merging PR #$PR ..."
gh pr merge "$PR" --squash --delete-branch

echo "[land] syncing main ..."
MAIN_STASH_CREATED=0
MAIN_STASH_POPPED=0
MAIN_STASH_RESTORE_ACTIVE=0
restore_main_stash_on_exit() {
  local status=$?
  trap - EXIT ERR
  if [ "$MAIN_STASH_CREATED" = 1 ] && [ "$MAIN_STASH_POPPED" = 0 ] && [ "$MAIN_STASH_RESTORE_ACTIVE" = 0 ]; then
    echo "[land] restoring stashed local tracked changes before exiting ..."
    if git stash pop; then
      MAIN_STASH_POPPED=1
    else
      echo "[land] stash pop had conflicts while restoring local tracked changes." >&2
      echo "[land] Resolve the conflicts, then run 'git stash drop' only after confirming the local edits are restored." >&2
      exit 1
    fi
  fi
  exit "$status"
}
if ! git diff --quiet --ignore-submodules || ! git diff --cached --quiet --ignore-submodules; then
  echo "[land] stashing local tracked changes before syncing main ..."
  git stash push -m "land-pr.sh: local tracked edits before syncing main for PR #$PR"
  MAIN_STASH_CREATED=1
  trap restore_main_stash_on_exit EXIT ERR
fi
git checkout main
git pull --ff-only
if [ "$MAIN_STASH_CREATED" = 1 ]; then
  echo "[land] restoring stashed local tracked changes ..."
  MAIN_STASH_RESTORE_ACTIVE=1
  if ! git stash pop; then
    echo "[land] stash pop had conflicts while restoring local tracked changes." >&2
    echo "[land] Resolve the conflicts, then run 'git stash drop' only after confirming the local edits are restored." >&2
    exit 1
  fi
  MAIN_STASH_RESTORE_ACTIVE=0
  MAIN_STASH_POPPED=1
  trap - EXIT ERR
fi

LAND_HEAD_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
export LAND_PROVENANCE_REVIEW_RATIONALE="$REVIEW_RATIONALE"
land_provenance_airdrop "$PR" "$ROOT" "$LAND_HEAD_SHA" "$CI_STATUS" || true

if [ "$RUN_TEST" = 1 ]; then
  echo "[land] post-merge sanity: pytest ..."
  PYTHONPATH="$ROOT/src" "$PY" -m pytest tests -q
fi

echo "[land] done. main is at:"
git log --oneline -1
