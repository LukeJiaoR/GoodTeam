#!/usr/bin/env bash
#
# pre-review.sh — dispatch an independent READ-ONLY mechanical pre-review job for a PR.
#
# Sediments the MECHANICAL half of review. Human/Claude keeps judgement on product
# decisions. This script NEVER merges and NEVER changes code.
#
# Usage:
#   scripts/pre-review.sh <PR-number> [--base main] [--worktree <path>]
#
# Arguments:
#   <PR-number>            Required. The PR to mechanically pre-review.
#
# Options:
#   --base <branch>        Base branch to diff against (default: main).
#   --worktree <path>      Existing dev worktree to run read-only review in.
#                          Default: create throwaway worktree off PR head.
#   --allow-unsafe         Pass through to dispatch only if needed by root policy.
#   -h, --help             Show this help.
#
# What it does (stops on first failure):
#   1. Resolves PR head branch via gh.
#   2. Fetches origin and computes file list + full patch (base...head).
#   3. Writes review input artifact to /tmp and builds a read-only review task file.
#   4. Dispatches exactly one code_review track via goodteam-os dispatch.
#   5. Prints where the job result.md will be written for the human reviewer.
#
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"

PR=""
BASE="main"
WORKTREE=""
ALLOW_UNSAFE=0
THROWAWAY_WORKTREE=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --base) BASE="$2"; shift 2 ;;
    --base=*) BASE="${1#*=}"; shift ;;
    --worktree) WORKTREE="$2"; shift 2 ;;
    --worktree=*) WORKTREE="${1#*=}"; shift ;;
    --allow-unsafe) ALLOW_UNSAFE=1; shift ;;
    -h|--help) sed -n '2,31p' "$0"; exit 0 ;;
    --*) echo "[pre-review] unknown arg: $1" >&2; exit 2 ;;
    *) [ -z "$PR" ] && { PR="$1"; shift; } || { echo "[pre-review] unexpected arg: $1" >&2; exit 2; } ;;
  esac
done

[ -n "$PR" ] || { echo "usage: scripts/pre-review.sh <PR-number> [--base main] [--worktree <path>]" >&2; exit 2; }
[ -x "$PY" ] || {
  echo "[pre-review] No venv python at $PY" >&2
  echo "[pre-review] Create it: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
}

echo "[pre-review] resolving PR #$PR head branch..."
HEAD_REF="$(gh pr view "$PR" --json headRefName -q .headRefName)"
if [ -z "$HEAD_REF" ]; then
  echo "[pre-review] failed to resolve headRefName for PR #$PR" >&2
  exit 1
fi
echo "[pre-review] head branch: $HEAD_REF"

echo "[pre-review] fetching origin..."
git fetch origin

BASE_REF="origin/$BASE"
HEAD_REMOTE_REF="origin/$HEAD_REF"

echo "[pre-review] computing diff: $BASE_REF...$HEAD_REMOTE_REF"
FILES="$(git diff --name-only "$BASE_REF...$HEAD_REMOTE_REF")"
PATCH="$(git diff "$BASE_REF...$HEAD_REMOTE_REF")"

DIFF_FILE="/tmp/pre-review-$PR.diff"
TASK_FILE="/tmp/pre-review-$PR-task.md"

{
  echo "PR: #$PR"
  echo "Base: $BASE_REF"
  echo "Head: $HEAD_REMOTE_REF"
  echo
  echo "=== Changed Files ==="
  if [ -n "$FILES" ]; then
    printf '%s\n' "$FILES"
  else
    echo "(no changed files)"
  fi
  echo
  echo "=== Full Patch ==="
  if [ -n "$PATCH" ]; then
    printf '%s\n' "$PATCH"
  else
    echo "(empty patch)"
  fi
} > "$DIFF_FILE"

echo "[pre-review] wrote review diff artifact: $DIFF_FILE"

cat > "$TASK_FILE" <<EOF_TASK
# Mechanical pre-review for PR #$PR (READ-ONLY)

You are the code_review worker. Perform an independent, mechanical pre-review only.

Hard constraints:
- Read-only: do NOT modify any files.
- Do NOT commit, push, or merge anything.
- Do NOT make product/judgement calls (human reviewer owns those).
- Output only the mechanical report with findings and evidence.

Required checks:
1. Lane/scope: changed files appear in-scope; flag obviously wrong files touched.
2. Contract/field alignment: obvious call/field/key mismatches, broken imports/references.
3. Obvious bugs: null/nil/None handling, off-by-one, unhandled error paths, dead refs.
4. Test sufficiency: whether changed behavior appears covered by changed/new tests.

Output format:
- Findings ordered by severity, each with file:line citation from the diff.
- If no findings, state "No mechanical findings" and residual risk/gaps.

Review input follows.

--- BEGIN DIFF ARTIFACT ---
$(cat "$DIFF_FILE")
--- END DIFF ARTIFACT ---
EOF_TASK

echo "[pre-review] wrote review task file: $TASK_FILE"

cleanup() {
  if [ -n "$THROWAWAY_WORKTREE" ] && [ -d "$THROWAWAY_WORKTREE" ]; then
    echo "[pre-review] cleaning throwaway worktree: $THROWAWAY_WORKTREE"
    git worktree remove "$THROWAWAY_WORKTREE" --force || true
  fi
}
trap cleanup EXIT

if [ -z "$WORKTREE" ]; then
  SAFE_HEAD="${HEAD_REF//\//-}"
  WORKTREE="$ROOT/../goodteam-pre-review-pr-$PR-$SAFE_HEAD"
  THROWAWAY_WORKTREE="$WORKTREE"
  if [ -e "$WORKTREE" ]; then
    echo "[pre-review] removing stale throwaway worktree path: $WORKTREE"
    rm -rf "$WORKTREE"
  fi
  echo "[pre-review] creating throwaway worktree from origin/$HEAD_REF: $WORKTREE"
  git worktree add --detach "$WORKTREE" "origin/$HEAD_REF"
else
  echo "[pre-review] using existing worktree: $WORKTREE"
fi

TRACK="code_review:codex:$TASK_FILE"
CMD=(env PYTHONPATH=src "$PY" -m goodteam_os.cli dispatch
  --project-root "$WORKTREE"
  --track "$TRACK")
if [ "$ALLOW_UNSAFE" = 1 ]; then
  CMD+=(--allow-unsafe)
fi

echo "[pre-review] dispatching read-only review track: $TRACK"
echo "[pre-review] command: ${CMD[*]}"
"${CMD[@]}"

REPORT_SRC=""
REPORT_GLOB="$WORKTREE/.goodteam/workers/jobs"/*/result.md
shopt -s nullglob
for candidate in $REPORT_GLOB; do
  if [ -z "$REPORT_SRC" ] || [ "$candidate" -nt "$REPORT_SRC" ]; then
    REPORT_SRC="$candidate"
  fi
done
shopt -u nullglob

REPORT_COPY="/tmp/pre-review-$PR-report.md"
if [ -n "$REPORT_SRC" ] && [ -f "$REPORT_SRC" ]; then
  cp "$REPORT_SRC" "$REPORT_COPY"
  echo "[pre-review] review report location:"
  echo "[pre-review]   $REPORT_COPY"
else
  echo "[pre-review] warning: no review report found at $WORKTREE/.goodteam/workers/jobs/*/result.md"
  echo "[pre-review] review report location: unavailable"
fi

echo "[pre-review] review is read-only: no merge, no code changes, no commits."
