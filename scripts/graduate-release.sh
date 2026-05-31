#!/usr/bin/env bash
#
# graduate-release.sh — the only gate to advance the pinned release instance.
#
# Preconditions:
# - dev worktree clean
# - PYTHONPATH=src pytest tests -q green
# - ruff check clean
# - human confirmation (--yes or interactive prompt)
#
# Actions:
# - create/move tag at current dev commit
# - move release worktree to the blessed ref
# - reinstall deps when pyproject/lockfiles changed
#
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
RELEASE_DIR="${GOODTEAM_RELEASE_DIR:-$ROOT/../goodteam-release}"
YES=0
TAG=""
REF=""
FORCE_MOVE=0

usage() {
  cat <<'EOF'
Usage: scripts/graduate-release.sh [--ref <tag|commit>] [--tag <vX.Y.Z>] [--yes] [--force-move-tag]

Options:
  --ref <ref>          Blessed ref to pin release to (default: --tag value).
  --tag <tag>          Create or move this tag to current dev HEAD.
  --yes                Skip interactive confirmation.
  --force-move-tag     Allow moving an existing tag.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --ref)
      [ "$#" -ge 2 ] || { echo "missing value for --ref" >&2; exit 2; }
      REF="$2"
      shift 2
      ;;
    --ref=*)
      REF="${1#*=}"
      shift
      ;;
    --tag)
      [ "$#" -ge 2 ] || { echo "missing value for --tag" >&2; exit 2; }
      TAG="$2"
      shift 2
      ;;
    --tag=*)
      TAG="${1#*=}"
      shift
      ;;
    --yes)
      YES=1
      shift
      ;;
    --force-move-tag)
      FORCE_MOVE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -z "$REF" ] && [ -n "$TAG" ]; then
  REF="$TAG"
fi
[ -n "$REF" ] || {
  echo "missing blessed ref: pass --ref <tag|commit> or --tag <vX.Y.Z>" >&2
  exit 2
}

git -C "$RELEASE_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  echo "release instance not found at $RELEASE_DIR" >&2
  echo "run scripts/release-up.sh first" >&2
  exit 1
}

if [ -n "$(git -C "$ROOT" status --porcelain)" ]; then
  echo "dev worktree is dirty; commit/stash before graduation." >&2
  git -C "$ROOT" status --short >&2
  exit 1
fi

if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "missing dev venv at $ROOT/.venv/bin/python" >&2
  exit 1
fi
if [ ! -x "$ROOT/.venv/bin/ruff" ]; then
  echo "missing ruff at $ROOT/.venv/bin/ruff" >&2
  exit 1
fi

echo "[graduate] running preflight tests..."
env PYTHONPATH="$ROOT/src" "$ROOT/.venv/bin/python" -m pytest tests -q
echo "[graduate] running ruff..."
"$ROOT/.venv/bin/ruff" check src/goodteam_os/ tests/

DEV_HEAD="$(git -C "$ROOT" rev-parse HEAD)"

if [ -n "$TAG" ]; then
  if git -C "$ROOT" rev-parse "$TAG^{tag}" >/dev/null 2>&1 || git -C "$ROOT" rev-parse "$TAG^{commit}" >/dev/null 2>&1; then
    if [ "$FORCE_MOVE" != 1 ]; then
      echo "tag $TAG already exists; pass --force-move-tag to move it." >&2
      exit 1
    fi
    git -C "$ROOT" tag -f "$TAG" "$DEV_HEAD"
    echo "[graduate] moved tag $TAG -> $DEV_HEAD"
  else
    git -C "$ROOT" tag "$TAG" "$DEV_HEAD"
    echo "[graduate] created tag $TAG -> $DEV_HEAD"
  fi
fi

TARGET_COMMIT="$(git -C "$ROOT" rev-parse "$REF^{commit}")"

if [ "$YES" != 1 ]; then
  echo ""
  echo "graduate release pin?"
  echo "  release dir: $RELEASE_DIR"
  echo "  from       : $(git -C "$RELEASE_DIR" rev-parse HEAD)"
  echo "  to         : $TARGET_COMMIT ($REF)"
  printf "Type 'yes' to continue: "
  read -r answer
  [ "$answer" = "yes" ] || { echo "aborted."; exit 1; }
fi

OLD_TREE="$(git -C "$RELEASE_DIR" rev-parse HEAD:pyproject.toml 2>/dev/null || true)"
OLD_LOCK="$(git -C "$RELEASE_DIR" rev-parse HEAD:frontend/package-lock.json 2>/dev/null || true)"

git -C "$RELEASE_DIR" checkout "$TARGET_COMMIT"

NEW_TREE="$(git -C "$RELEASE_DIR" rev-parse HEAD:pyproject.toml 2>/dev/null || true)"
NEW_LOCK="$(git -C "$RELEASE_DIR" rev-parse HEAD:frontend/package-lock.json 2>/dev/null || true)"

if [ "$OLD_TREE" != "$NEW_TREE" ] || [ "$OLD_LOCK" != "$NEW_LOCK" ] || [ ! -x "$RELEASE_DIR/.venv/bin/python" ]; then
  echo "[graduate] dependency surfaces changed; reinstalling..."
  rm -rf "$RELEASE_DIR/.venv"
  python3 -m venv "$RELEASE_DIR/.venv"
  "$RELEASE_DIR/.venv/bin/pip" install -e ".[dev]"
fi

echo "[graduate] release pin advanced:"
echo "  ref   : $REF"
echo "  commit: $(git -C "$RELEASE_DIR" rev-parse HEAD)"
