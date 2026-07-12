#!/usr/bin/env bash
#
# demo-up.sh — bring up a real-worker GoodTeam demo instance for the first-user flow.
#
# Demo workspace defaults to a sibling directory outside this repo so codex
# workers do not walk parent dirs into GoodTeam project memory.
#
# Usage:
#   scripts/demo-up.sh            # seed once (if needed) + serve on :8788
#   scripts/demo-up.sh --reset    # wipe + reseed demo-workspace
#   scripts/demo-up.sh --build    # force-rebuild frontend/dist first
#   scripts/demo-up.sh --port=9000
#
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
DEMO_DIR="${GOODTEAM_DEMO_DIR:-$ROOT/../goodteam-demo}"
WS="$DEMO_DIR/workspace"
PROJECTS_DIR="$WS/projects"
CONFIG_PATH="$PROJECTS_DIR/demo.local.yaml"
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

# 1. Build frontend/dist (single-port serve needs built assets).
if [ "$BUILD" = 1 ] || [ ! -d "$ROOT/frontend/dist" ]; then
  echo "[demo-up] building frontend/dist ..."
  ( cd "$ROOT/frontend" && npm install >/dev/null 2>&1 && npm run build >/dev/null )
fi

# 2. Isolated demo workspace only.
[ "$RESET" = 1 ] && rm -rf "$WS"
mkdir -p "$WS/target" "$PROJECTS_DIR"

echo "[demo-up] preparing demo config in $PROJECTS_DIR"
run init --key DEMO --name "Demo Project" --repo "$WS/target" \
    --worker-mode codex --delivery-mode init_project \
    --projects-dir "$PROJECTS_DIR" --force >/dev/null

# 2b. Require codex backend readiness before any real-worker demo.
doctor_json="$(run doctor --project-config "$CONFIG_PATH")"
ready_and_hint="$(printf '%s\n' "$doctor_json" | "$PY" -c '
import json
import sys
payload = json.load(sys.stdin)
preflight = dict(payload.get("worker_backend_preflight", {}) or {})
ready = bool(preflight.get("ready"))
hint = str(preflight.get("hint", "")).replace("\n", " ").strip()
print(("1" if ready else "0") + "\t" + hint)
')"
codex_ready="${ready_and_hint%%$'\t'*}"
codex_hint="${ready_and_hint#*$'\t'}"
if [ "$codex_ready" != "1" ]; then
  echo "[demo-up] codex backend is not ready: $codex_hint" >&2
  echo "[demo-up] Authenticate codex, then retry (see README backend-setup section)." >&2
  exit 1
fi

# 2c. Seed exactly one pinned demo run to the PRD gate; do not auto-approve.
if [ ! -f "$WS/state/projects/DEMO/current.json" ]; then
  echo "[demo-up] starting pinned demo idea (stops at PRD approval gate)"
  run start "Build a task tracker" --project-key DEMO \
      --project-config "$CONFIG_PATH" --workspace "$WS" >/dev/null
else
  echo "[demo-up] workspace already seeded ($WS); use --reset to rebuild"
fi

# 3. serve resolves application assets from this checkout, independently of $WS.

# 4. Free the port so leftover servers cannot shadow this instance.
existing="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$existing" ]; then
  echo "[demo-up] stopping previous server on :$PORT (pid $existing)"
  kill $existing 2>/dev/null || true
  sleep 1
fi

echo ""
echo "[demo-up] GoodTeam real-worker demo is ready:"
echo "          Dashboard   : http://localhost:$PORT/"
echo "          DEMO detail : http://localhost:$PORT/?project=DEMO"
echo ""
echo "[demo-up] Runbook"
echo "          Open http://localhost:$PORT/?project=DEMO -> Approvals tab -> approve PRD ->"
echo "          approve RFC/plan -> implementation+QA run via real codex -> approve release ->"
echo "          read the audit trail (Artifacts / Approvals / Memory tabs)."
echo ""
run serve --workspace "$WS" --port "$PORT"
