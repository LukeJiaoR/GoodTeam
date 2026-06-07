#!/usr/bin/env bash
#
# dispatch-track.sh — create/reuse a dev worktree and dispatch one implementation track.
#
# A thin mechanical wrapper around:
#   1) git worktree add ... main
#   2) goodteam-os dispatch --track <role>:<adapter>:<taskfile>
#
# Usage:
#   scripts/dispatch-track.sh --name <slug> --task <taskfile> [options]
#
# Options:
#   --name <slug>           Required. Name used for defaults.
#   --task <taskfile>       Required. Track task file path (passed through to dispatch).
#   --role <role>           Track role (default: implementation).
#   --adapter <adapter>     Track adapter (default: codex).
#   --constraints <file>    Optional constraints file for dispatch.
#   --research-report <file> Optional research report to archive with the job.
#   --verify <cmd>          Optional verify command (as role-scoped verify).
#   --branch <name>         Worktree branch (default: feat/<name>).
#   --worktree <path>       Worktree path (default: ../goodteam-dev-<name> from repo root).
#   --allow-unsafe          Pass through to dispatch.
#   -h, --help              Show this help.
#
# What it does (idempotent-ish, stops on first failure):
#   1. Resolve defaults from --name.
#   2. Create worktree if missing; otherwise reuse existing path.
#   3. Run goodteam-os dispatch for one track, streaming its JSON output.
#   4. Print next-step hints for review/landing.
#
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"

NAME=""
TASK=""
ROLE="implementation"
ADAPTER="codex"
CONSTRAINTS=""
RESEARCH_REPORT=""
VERIFY_CMD=""
BRANCH=""
WORKTREE=""
ALLOW_UNSAFE=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --name=*) NAME="${1#*=}"; shift ;;
    --task) TASK="$2"; shift 2 ;;
    --task=*) TASK="${1#*=}"; shift ;;
    --role) ROLE="$2"; shift 2 ;;
    --role=*) ROLE="${1#*=}"; shift ;;
    --adapter) ADAPTER="$2"; shift 2 ;;
    --adapter=*) ADAPTER="${1#*=}"; shift ;;
    --constraints) CONSTRAINTS="$2"; shift 2 ;;
    --constraints=*) CONSTRAINTS="${1#*=}"; shift ;;
    --research-report) RESEARCH_REPORT="$2"; shift 2 ;;
    --research-report=*) RESEARCH_REPORT="${1#*=}"; shift ;;
    --verify) VERIFY_CMD="$2"; shift 2 ;;
    --verify=*) VERIFY_CMD="${1#*=}"; shift ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --branch=*) BRANCH="${1#*=}"; shift ;;
    --worktree) WORKTREE="$2"; shift 2 ;;
    --worktree=*) WORKTREE="${1#*=}"; shift ;;
    --allow-unsafe) ALLOW_UNSAFE=1; shift ;;
    -h|--help) sed -n '2,31p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

[ -n "$NAME" ] || { echo "missing required arg: --name" >&2; exit 2; }
[ -n "$TASK" ] || { echo "missing required arg: --task" >&2; exit 2; }
[ -x "$PY" ] || {
  echo "No venv python at $PY" >&2
  echo "Create it: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
}

[ -n "$BRANCH" ] || BRANCH="feat/$NAME"
[ -n "$WORKTREE" ] || WORKTREE="$ROOT/../goodteam-dev-$NAME"

if [ ! -e "$WORKTREE" ]; then
  echo "[dispatch-track] creating worktree: $WORKTREE (branch $BRANCH from main)"
  git worktree add "$WORKTREE" -b "$BRANCH" main
else
  echo "[dispatch-track] reusing existing worktree: $WORKTREE"
fi

if [ ! -e "$WORKTREE/.venv" ] && [ ! -L "$WORKTREE/.venv" ]; then
  echo "[dispatch-track] linking worktree venv bin to main repo venv"
  mkdir "$WORKTREE/.venv"
  ln -s "$ROOT/.venv/bin" "$WORKTREE/.venv/bin"
fi

TRACK="$ROLE:$ADAPTER:$TASK"
VERIFY_ARG="$ROLE:$VERIFY_CMD"

CMD=(env PYTHONPATH=src "$PY" -m goodteam_os.cli dispatch
  --project-root "$WORKTREE"
  --track "$TRACK")

if [ -n "$CONSTRAINTS" ]; then
  CMD+=(--constraints-file "$CONSTRAINTS")
fi
if [ -n "$RESEARCH_REPORT" ]; then
  CMD+=(--research-report "$RESEARCH_REPORT")
fi
if [ -n "$VERIFY_CMD" ]; then
  CMD+=(--verify "$VERIFY_ARG")
fi
if [ "$ALLOW_UNSAFE" = 1 ]; then
  CMD+=(--allow-unsafe)
fi

echo "[dispatch-track] dispatching track: $TRACK"
echo "[dispatch-track] command: ${CMD[*]}"
"${CMD[@]}"

echo "[dispatch-track] next: review changes in worktree: $WORKTREE"
echo "[dispatch-track] when approved, land via scripts/land-pr.sh"
