#!/usr/bin/env bash
#
# release-up.sh — bring up a pinned, stable GoodTeam release instance outside
# the dev repo. This instance runs serve/dispatch against dev worktrees.
#
# Usage:
#   scripts/release-up.sh
#   scripts/release-up.sh --ref v1.0.0
#   scripts/release-up.sh --port 8899
#   scripts/release-up.sh --reinstall
#
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PORT="${PORT:-8788}"
REINSTALL=0
REF=""
RELEASE_DIR="${GOODTEAM_RELEASE_DIR:-$ROOT/../goodteam-release}"

usage() {
  cat <<'EOF'
Usage: scripts/release-up.sh [--ref <tag|commit>] [--reinstall] [--port <port>]

Options:
  --ref <ref>        Pinned ref for initial creation (default: latest v* tag).
  --reinstall        Recreate .venv + reinstall python deps.
  --port <port>      Serve port (default: 8788 or $PORT).
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
    --reinstall)
      REINSTALL=1
      shift
      ;;
    --port)
      [ "$#" -ge 2 ] || { echo "missing value for --port" >&2; exit 2; }
      PORT="$2"
      shift 2
      ;;
    --port=*)
      PORT="${1#*=}"
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

abs_path() {
  local input="$1"
  if [ -d "$input" ]; then
    (cd "$input" && pwd -P)
    return
  fi
  local parent
  parent="$(dirname "$input")"
  local name
  name="$(basename "$input")"
  echo "$(cd "$parent" && pwd -P)/$name"
}

REPO_REAL="$(cd "$ROOT" && pwd -P)"
RELEASE_REAL="$(abs_path "$RELEASE_DIR")"

if [ "$RELEASE_REAL" = "$REPO_REAL" ] || [[ "$RELEASE_REAL"/ == "$REPO_REAL/"* ]]; then
  echo "release dir must be outside repo root: $REPO_REAL" >&2
  echo "current release dir: $RELEASE_REAL" >&2
  exit 1
fi

if [ -z "$REF" ]; then
  REF="$(git -C "$ROOT" tag --list 'v*' --sort=-v:refname | head -n 1 || true)"
fi
[ -n "$REF" ] || {
  echo "no v* tag found; pass --ref <tag|commit>" >&2
  exit 1
}

TARGET_COMMIT="$(git -C "$ROOT" rev-parse "$REF^{commit}" 2>/dev/null || true)"
[ -n "$TARGET_COMMIT" ] || {
  echo "unknown ref: $REF" >&2
  exit 1
}

install_runtime() {
  local py="$RELEASE_DIR/.venv/bin/python"
  if [ "$REINSTALL" = 1 ] && [ -d "$RELEASE_DIR/.venv" ]; then
    rm -rf "$RELEASE_DIR/.venv"
  fi
  if [ ! -x "$py" ]; then
    python3 -m venv "$RELEASE_DIR/.venv"
  fi
  "$RELEASE_DIR/.venv/bin/pip" install -e ".[dev]"
}

if ! git -C "$RELEASE_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "[release-up] creating release worktree at $RELEASE_DIR (ref=$REF)"
  mkdir -p "$(dirname "$RELEASE_DIR")"
  git -C "$ROOT" worktree add "$RELEASE_DIR" "$REF"
  install_runtime
else
  CURRENT_COMMIT="$(git -C "$RELEASE_DIR" rev-parse HEAD)"
  if [ "$CURRENT_COMMIT" != "$TARGET_COMMIT" ]; then
    echo "existing release instance is pinned to a different ref:" >&2
    echo "  current: $CURRENT_COMMIT" >&2
    echo "  target : $TARGET_COMMIT ($REF)" >&2
    echo "use scripts/graduate-release.sh to move the release pin." >&2
    exit 1
  fi
  [ "$REINSTALL" = 0 ] || install_runtime
fi

PY="$RELEASE_DIR/.venv/bin/python"
[ -x "$PY" ] || {
  echo "release venv missing: $PY" >&2
  echo "rerun with --reinstall" >&2
  exit 1
}

if [ ! -d "$RELEASE_DIR/frontend/dist" ]; then
  echo "[release-up] building frontend/dist at release ref ..."
  (cd "$RELEASE_DIR/frontend" && npm install >/dev/null 2>&1 && npm run build >/dev/null)
fi

WS="$RELEASE_DIR/dev-workspace"
if [ ! -f "$WS/projects/demo.local.yaml" ]; then
  echo "[release-up] seeding release workspace: $WS"
  mkdir -p "$WS/target"
  env PYTHONPATH="$RELEASE_DIR/src" "$PY" -m goodteam_os.cli init \
    --key DEMO --name "Release Demo" --repo "$WS/target" \
    --worker-mode fake --projects-dir "$WS/projects" --force >/dev/null
  env PYTHONPATH="$RELEASE_DIR/src" "$PY" -m goodteam_os.cli start \
    "Bootstrap release dashboard health check" \
    --project-key DEMO \
    --project-config "$WS/projects/demo.local.yaml" \
    --workspace "$WS" >/dev/null || true
  env PYTHONPATH="$RELEASE_DIR/src" "$PY" - "$WS" <<'PYEOF' || true
import sys
from pathlib import Path
from goodteam_os.smoke import _approve_until_complete
try:
    _approve_until_complete(Path(sys.argv[1]), "DEMO")
    print("[release-up] DEMO run advanced to complete")
except Exception as exc:
    print(f"[release-up] note: could not fully advance DEMO run: {exc}")
PYEOF
fi

mkdir -p "$WS/frontend"
ln -sfn "$RELEASE_DIR/frontend/dist" "$WS/frontend/dist"

echo ""
echo "[release-up] release instance ready:"
echo "  repo    : $RELEASE_DIR"
echo "  ref     : $REF ($TARGET_COMMIT)"
echo "  serve   : http://localhost:$PORT/"
echo "  demo    : http://localhost:$PORT/?project=DEMO"
echo ""
echo "[release-up] dispatch example (from release instance to dev worktree):"
echo "  env PYTHONPATH=\"$RELEASE_DIR/src\" \"$PY\" -m goodteam_os.cli dispatch \\"
echo "    --project-root /abs/path/to/dev-worktree \\"
echo "    --track backend:codex:\"implement change\" --verify backend:\"PYTHONPATH=src .venv/bin/python -m pytest tests -q\""
echo ""

env PYTHONPATH="$RELEASE_DIR/src" "$PY" -m goodteam_os.cli serve --workspace "$WS" --port "$PORT"
