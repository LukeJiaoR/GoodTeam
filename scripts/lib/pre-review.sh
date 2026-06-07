#!/usr/bin/env bash

pre_review_is_auth_failure() {
  local status_path="$1"
  [ -f "$status_path" ] || return 1
  "${PY:-python3}" - "$status_path" <<'PY'
import json
import sys
from pathlib import Path

try:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)
text = " ".join(
    str(payload.get(key, ""))
    for key in ("failure_class", "summary", "error", "disposition")
).lower()
if "auth" in text or "403" in text or "forbidden" in text or "unauthorized" in text:
    raise SystemExit(0)
raise SystemExit(1)
PY
}

pre_review_mtime() {
  local path="$1"
  local value=""

  value="$(stat -c '%Y' "$path" 2>/dev/null || true)"
  case "$value" in
    ""|*[!0-9]*)
      value="$(stat -f '%m' "$path" 2>/dev/null || true)"
      ;;
  esac
  case "$value" in
    ""|*[!0-9]*) printf '0' ;;
    *) printf '%s' "$value" ;;
  esac
}

pre_review_latest_status() {
  local worktree="$1"
  local invocation_id="${2:-}"
  local jobs_root="$worktree/.goodteam/workers/jobs"
  [ -d "$jobs_root" ] || return 1
  find "$jobs_root" -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null |
    while IFS= read -r job_dir; do
      local status_path="$job_dir/status.json"
      [ -f "$status_path" ] || continue
      pre_review_job_matches_invocation "$job_dir" "$invocation_id" || continue
      printf '%s\t%s\n' "$(pre_review_mtime "$status_path")" "$status_path"
    done |
    sort -nr |
    sed -n '1s/^[^	]*	//p'
}

pre_review_job_matches_invocation() {
  local job_dir="$1"
  local invocation_id="${2:-}"
  [ -n "$invocation_id" ] || return 0
  local task_path="$job_dir/task.md"
  [ -f "$task_path" ] || return 1
  grep -Fq "$invocation_id" "$task_path"
}

pre_review_select_report() {
  local worktree="$1"
  local invocation_id="${2:-}"
  local jobs_root="$worktree/.goodteam/workers/jobs"
  [ -d "$jobs_root" ] || return 1

  find "$jobs_root" -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null |
    while IFS= read -r job_dir; do
      local status_path="$job_dir/status.json"
      local result_path="$job_dir/result.md"
      [ -f "$status_path" ] || continue
      [ -s "$result_path" ] || continue
      pre_review_job_matches_invocation "$job_dir" "$invocation_id" || continue
      "${PY:-python3}" - "$status_path" "$result_path" <<'PY'
import json
import sys
from pathlib import Path

status_path = Path(sys.argv[1])
result_path = Path(sys.argv[2])
try:
    payload = json.loads(status_path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(0)
role = payload.get("role") or payload.get("role_key") or payload.get("roleKey") or ""
status = payload.get("status") or ""
if role != "code_review" or status != "completed":
    raise SystemExit(0)
print(f"{status_path.stat().st_mtime:.0f}\t{result_path}")
PY
    done |
    sort -nr |
    sed -n '1s/^[^	]*	//p'
}

pre_review_fallback_adapters() {
  local primary="$1"
  case "$primary" in
    codex) printf '%s\n' codex claude agy ;;
    claude|anthropic) printf '%s\n' claude agy codex ;;
    agy|antigravity) printf '%s\n' agy claude codex ;;
    *) printf '%s\n' "$primary" codex claude agy ;;
  esac | awk '!seen[$0]++'
}
