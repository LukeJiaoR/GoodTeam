#!/usr/bin/env bash
#
# dev-up.sh — bring up a known-alive GoodTeam dev/demo instance with seeded data.
#
# Everything lives under ./dev-workspace (gitignored). It NEVER touches real
# project configs (CONTENTOPS/AGENTSMESH) and never runs smoke. Use it to
# actually *look at* the dashboard while developing, and as a stable target for
# QA / browser tooling.
#
# Usage:
#   scripts/dev-up.sh            # seed once (if needed) + serve on :8788
#   scripts/dev-up.sh --reset    # wipe + reseed the demo workspace
#   scripts/dev-up.sh --build    # force-rebuild frontend/dist first
#   scripts/dev-up.sh --port=9000
#
# For frontend hot-reload: keep this running, then in another tab
#   cd frontend && npm run dev      # opens :5173, proxies /api to this server
#
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
WS="$ROOT/dev-workspace"
PORT="${PORT:-8788}"
PY="$ROOT/.venv/bin/python"

RESET=0
BUILD=0
for arg in "$@"; do
  case "$arg" in
    --reset) RESET=1 ;;
    --build) BUILD=1 ;;
    --port=*) PORT="${arg#*=}" ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

run() { env PYTHONPATH="$ROOT/src" "$PY" -m goodteam_os.cli "$@"; }

[ -x "$PY" ] || {
  echo "No venv at $PY" >&2
  echo "Create it: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
}

# 1. Built frontend (single-port serve needs frontend/dist).
if [ "$BUILD" = 1 ] || [ ! -d "$ROOT/frontend/dist" ]; then
  echo "[dev-up] building frontend/dist ..."
  ( cd "$ROOT/frontend" && npm install >/dev/null 2>&1 && npm run build >/dev/null )
fi

# 2. Isolated, seeded demo workspace (idempotent; --reset to rebuild).
[ "$RESET" = 1 ] && rm -rf "$WS"
if [ ! -f "$WS/projects/demo.local.yaml" ]; then
  echo "[dev-up] seeding demo workspace: $WS"
  mkdir -p "$WS/target"
  run init --key DEMO --name "Demo Project" --repo "$WS/target" \
      --worker-mode fake --projects-dir "$WS/projects" --force >/dev/null
  # Start a run so the dashboard shows a real gate / roles / workflow waterfall.
  run start "Build a small demo task tracker app" --project-key DEMO \
      --project-config "$WS/projects/demo.local.yaml" --workspace "$WS" >/dev/null || true
  # Seed a few memory events so retrieval (and the future memory panel) have content.
  env PYTHONPATH="$ROOT/src" "$PY" - "$WS" <<'PYEOF' || true
import sys
from pathlib import Path
from goodteam_os.memory import RoleMemoryStore
store = RoleMemoryStore(Path(sys.argv[1]) / "target")
for role, horizon, text in [
    ("architect", "long-term", "Demo app uses a single-page React UI over a JSON API."),
    ("implementation", "mid-term", "Prefer mature libraries over custom infra for the demo."),
    ("qa", "short-term", "Verify task create/list/complete happy path before release."),
]:
    store.append_memory_event(role, horizon=horizon, text=text, source="dev-up-seed")
print("[dev-up] seeded memory events")
PYEOF
else
  echo "[dev-up] workspace already seeded ($WS); use --reset to rebuild"
fi

# 3. serve resolves dist at <workspace>/frontend/dist, so link the built dist in.
mkdir -p "$WS/frontend"
ln -sfn "$ROOT/frontend/dist" "$WS/frontend/dist"

echo ""
echo "[dev-up] GoodTeam dev instance is alive:"
echo "          Dashboard    : http://localhost:$PORT/"
echo "          DEMO detail  : http://localhost:$PORT/?project=DEMO"
echo "          (Ctrl-C to stop. FE hot-reload: cd frontend && npm run dev → :5173)"
echo ""
run serve --workspace "$WS" --port "$PORT"
