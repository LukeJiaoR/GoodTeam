#!/usr/bin/env bash

land_provenance_warn() {
  echo "[land] (provenance $*)"
}

land_provenance_extract() {
  local pr="$1"
  local worktree="$2"
  local staging="/tmp/land-provenance-$pr"
  local jobs_src="$worktree/.goodteam/workers/jobs"
  local newest_job=""
  local status_json=""

  export LAND_PROVENANCE_STAGING="$staging"
  export LAND_PROVENANCE_RUN_ID=""
  export LAND_PROVENANCE_JOB_ID=""
  export LAND_PROVENANCE_ROLE=""

  if [ -z "$worktree" ] || [ ! -d "$worktree" ]; then
    rm -rf "$staging" 2>/dev/null || true
    return 0
  fi
  if [ ! -d "$jobs_src" ]; then
    rm -rf "$staging" 2>/dev/null || true
    land_provenance_warn "extract skipped: no worker jobs at $jobs_src"
    return 0
  fi

  rm -rf "$staging" 2>/dev/null || {
    land_provenance_warn "extract could not reset staging dir $staging"
    return 0
  }
  mkdir -p "$staging" 2>/dev/null || {
    land_provenance_warn "extract could not create staging dir $staging"
    return 0
  }
  cp -R "$jobs_src"/. "$staging"/ 2>/dev/null || {
    land_provenance_warn "extract could not copy worker jobs from $jobs_src"
    return 0
  }

  newest_job="$({ find "$jobs_src" -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null | while IFS= read -r job_dir; do
    local_role=""
    if [ -f "$job_dir/status.json" ]; then
      local_role="$("${PY:-python3}" - "$job_dir/status.json" <<'PY' 2>/dev/null || true
import json
import sys
from pathlib import Path

try:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception:
    payload = {}
role = payload.get("role") or payload.get("role_key") or payload.get("roleKey") or ""
print(role if isinstance(role, str) else "")
PY
)"
      priority=1
      [ "$local_role" = "code_review" ] && priority=0
      [ "$local_role" = "review" ] && priority=0
      printf '%s\t%s\t%s\n' "$priority" "$(stat -f '%m' "$job_dir/status.json" 2>/dev/null || stat -c '%Y' "$job_dir/status.json" 2>/dev/null || printf '0')" "$job_dir"
    else
      printf '0\t%s\t%s\n' "$(stat -f '%m' "$job_dir" 2>/dev/null || stat -c '%Y' "$job_dir" 2>/dev/null || printf '0')" "$job_dir"
    fi
  done | sort -k1,1nr -k2,2nr | sed -n '1s/^[^	]*	[^	]*	//p'; } || true)"

  status_json="$newest_job/status.json"
  if [ -n "$newest_job" ] && [ -f "$status_json" ]; then
    eval "$(
      "${PY:-python3}" - "$status_json" <<'PY' 2>/dev/null || true
import json
import shlex
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))

def first_string(*keys):
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""

fields = {
    "LAND_PROVENANCE_RUN_ID": first_string("run_id", "runId"),
    "LAND_PROVENANCE_JOB_ID": first_string("job_id", "jobId", "id"),
    "LAND_PROVENANCE_ROLE": first_string("role", "role_key", "roleKey"),
}
for key, value in fields.items():
    if value:
        print(f"export {key}={shlex.quote(value)}")
PY
    )"
  fi

  return 0
}

land_provenance_airdrop() {
  local pr="$1"
  local root="$2"
  local head_sha="$3"
  local ci_status="${4:-unverified}"
  local staging="${LAND_PROVENANCE_STAGING:-/tmp/land-provenance-$pr}"
  local archive="$root/.goodteam/provenance/pr-$pr"
  local archive_rel=".goodteam/provenance/pr-$pr/"
  local branch=""
  local pre_review="n"
  local pre_review_report="/tmp/pre-review-$pr-report.md"
  local pre_review_archive="$archive/pre-review-report.md"
  local pre_review_archive_rel=".goodteam/provenance/pr-$pr/pre-review-report.md"
  local text=""
  local review_text=""
  local python_bin="${PY:-$root/.venv/bin/python}"

  mkdir -p "$(dirname "$archive")" 2>/dev/null || land_provenance_warn "archive parent create failed: $(dirname "$archive")"
  if [ -d "$staging" ]; then
    rm -rf "$archive" 2>/dev/null || land_provenance_warn "archive reset failed: $archive"
    mkdir -p "$archive" 2>/dev/null && cp -R "$staging"/. "$archive"/ 2>/dev/null || land_provenance_warn "archive copy failed: $staging -> $archive"
  else
    land_provenance_warn "archive skipped: no staged jobs at $staging"
  fi

  branch="$(gh pr view "$pr" --json headRefName --jq .headRefName 2>/dev/null || true)"
  [ -n "$branch" ] || branch="unknown"
  if [ -s "$pre_review_report" ]; then
    pre_review="y"
    mkdir -p "$archive" 2>/dev/null && cp "$pre_review_report" "$pre_review_archive" 2>/dev/null || land_provenance_warn "pre-review report archive failed: $pre_review_report -> $pre_review_archive"
  fi

  text="PR #$pr landed; head_branch=$branch; CI=$ci_status; pre_review_report=$pre_review; squash_merge_sha=$head_sha; archive=$archive_rel"
  if [ "$pre_review" = "y" ] && [ -f "$pre_review_archive" ]; then
    text="$text; pre_review_report_path=$pre_review_archive_rel"
  fi
  if [ -n "${LAND_PROVENANCE_RUN_ID:-}" ]; then
    text="$text; run_id=$LAND_PROVENANCE_RUN_ID"
  fi
  if [ -n "${LAND_PROVENANCE_JOB_ID:-}" ]; then
    text="$text; job_id=$LAND_PROVENANCE_JOB_ID"
  fi
  if [ -n "${LAND_PROVENANCE_ROLE:-}" ]; then
    text="$text; dispatch_role=$LAND_PROVENANCE_ROLE"
  fi

  if [ ! -x "$python_bin" ]; then
    land_provenance_warn "memory append skipped: python not executable at $python_bin"
    return 0
  fi
  PYTHONPATH="$root/src" "$python_bin" -m goodteam_os.cli memory-append \
    --project-root "$root" \
    --role release \
    --horizon mid-term \
    --source "land/pr-$pr" \
    --text "$text" >/dev/null 2>"/tmp/land-provenance-$pr-memory.err" || {
      land_provenance_warn "memory append failed: $(cat "/tmp/land-provenance-$pr-memory.err" 2>/dev/null)"
      return 0
    }

  review_text="PR #$pr land_review_decision=approved_to_land; head_branch=$branch; CI=$ci_status; pre_review_report=$pre_review; squash_merge_sha=$head_sha"
  if [ "$pre_review" = "y" ] && [ -f "$pre_review_archive" ]; then
    review_text="$review_text; pre_review_report=$pre_review_archive_rel"
  fi
  PYTHONPATH="$root/src" "$python_bin" -m goodteam_os.cli memory-append \
    --project-root "$root" \
    --role release \
    --horizon mid-term \
    --source "review/pr-$pr" \
    --text "$review_text" >/dev/null 2>"/tmp/land-provenance-$pr-review-memory.err" || {
      land_provenance_warn "review memory append failed: $(cat "/tmp/land-provenance-$pr-review-memory.err" 2>/dev/null)"
      return 0
    }

  return 0
}
