#!/usr/bin/env bash
#
# commit-docs.sh — stage and commit docs-only files, then push.
#
# A thin mechanical wrapper for release/retro_memory docs commits.
# It validates every provided path as docs-only before staging anything.
#
# Usage:
#   scripts/commit-docs.sh --message "<commit message>" [--no-push] <file>...
#
# Options:
#   --message <msg>        Required. Commit subject line.
#   --no-push              Commit but do not push.
#   -h, --help             Show this help.
#
# Rules:
#   - Only docs paths are allowed:
#       * docs/**
#       * *.md at repo root
#   - Any non-doc/code path is rejected.
#   - Stages ONLY explicitly-passed files (never -A).
#
set -euo pipefail

cd "$(dirname "$0")/.."

MESSAGE=""
DO_PUSH=1
FILES=()

is_allowed_doc_path() {
  local path="$1"

  # Normalize leading ./ for matching.
  while [[ "$path" == ./* ]]; do
    path="${path#./}"
  done

  # Explicit hard rejects.
  case "$path" in
    src/*|tests/*|frontend/*|scripts/*|.github/*|pyproject.toml)
      return 1
      ;;
  esac

  # Allow docs/** (including nested markdown files).
  case "$path" in
    docs/*)
      return 0
      ;;
  esac

  # Allow markdown files at repo root only.
  if [[ "$path" == *.md && "$path" != */* ]]; then
    return 0
  fi

  return 1
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --message) MESSAGE="$2"; shift 2 ;;
    --message=*) MESSAGE="${1#*=}"; shift ;;
    --no-push) DO_PUSH=0; shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    --*) echo "unknown arg: $1" >&2; exit 2 ;;
    *) FILES+=("$1"); shift ;;
  esac
done

[ -n "$MESSAGE" ] || { echo "[commit-docs] missing required arg: --message" >&2; exit 2; }
[ "${#FILES[@]}" -gt 0 ] || { echo "[commit-docs] missing required docs file arguments" >&2; exit 2; }

echo "[commit-docs] validating docs-only paths..."
for path in "${FILES[@]}"; do
  if ! is_allowed_doc_path "$path"; then
    echo "[commit-docs] refusing non-doc path: $path" >&2
    echo "[commit-docs] allowed: docs/** and *.md at repo root" >&2
    exit 1
  fi
done

echo "[commit-docs] staging explicit files..."
git add -- "${FILES[@]}"

if git diff --cached --quiet -- "${FILES[@]}"; then
  echo "[commit-docs] no staged changes for provided file(s); nothing to commit."
  exit 0
fi

MSG_FILE="$(mktemp)"
trap 'rm -f "$MSG_FILE"' EXIT
printf '%s\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>\n' "$MESSAGE" >"$MSG_FILE"

echo "[commit-docs] creating commit..."
git commit -F "$MSG_FILE"

if [ "$DO_PUSH" = 1 ]; then
  echo "[commit-docs] pushing current branch..."
  git push
else
  echo "[commit-docs] --no-push set; skipping push."
fi

echo "[commit-docs] done. HEAD is:"
git log --oneline -1
