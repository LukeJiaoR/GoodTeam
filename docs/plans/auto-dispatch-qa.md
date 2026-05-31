# Plan — Minimal auto-dispatch: QA track (verification only)

You are the **QA / verification** agent for GoodTeam's minimal auto-dispatch
feature. The backend agent should already have implemented a standalone
`dispatch` CLI, parallel worker execution, Gemini adapter plumbing, and
verify-based escalation. Your job is verification only.

## Rules

- **Verification only — do not edit feature code.**
- **Do not commit.**
- Do not touch `frontend/`; this QA is backend-first with a single browser-free
  workflow.
- Use a throwaway workspace under `/private/tmp`.
- Do not call real Gemini or Codex CLIs in this pass.
- If a step fails, capture the exact symptom and identify the likely owner:
  backend (`src/goodteam_os/workers.py`, `src/goodteam_os/cli.py`) or test
  setup.

## Workspace

- **Repo root (run all commands from here):** `/Users/luke/WorkSpace/codeAi/GoodTeam`
- **Throwaway QA workspace:** `/private/tmp/goodteam-auto-dispatch-qa`

## Expected behavior to keep in mind

- `dispatch` runs tracks in parallel.
- A worker failure escalates only that track to `needs_human`; the other track
  still runs.
- A verify failure also escalates only that track to `needs_human`; the other
  track still runs.
- `ok` means the worker completed and verify passed.
- The M1 memory write-back note stays attached to successful worker results.
- Same-run propagation is not part of this feature and must not be treated as a
  failure.
- The Gemini adapter exists in code, but QA should not invoke it directly in
  this pass.

## Baseline verification

Run the backend agent's required commands first:

```bash
cd /Users/luke/WorkSpace/codeAi/GoodTeam
PYTHONPATH=src python -m pytest tests -q
ruff check src/goodteam_os/ tests/
```

Both should pass before the live dispatch check below.

## Live dispatch check with two fake tracks

Use a throwaway project root and fake worker / verify behavior. This should not
call the real Codex or Gemini CLIs.

Run:

```bash
cd /Users/luke/WorkSpace/codeAi/GoodTeam
rm -rf /private/tmp/goodteam-auto-dispatch-qa
PYTHONPATH=src python - <<'PY'
import json
import subprocess
import sys
from pathlib import Path

workspace = Path("/private/tmp/goodteam-auto-dispatch-qa")
workspace.mkdir(parents=True, exist_ok=True)

project_root = workspace / "target"
project_root.mkdir(parents=True, exist_ok=True)

track_a = workspace / "backend-track.md"
track_b = workspace / "frontend-track.md"
track_a.write_text("Implement backend flow.", encoding="utf-8")
track_b.write_text("Implement frontend flow.", encoding="utf-8")

dispatch_args = [
    "dispatch",
    "--project-root",
    str(project_root),
    "--track",
    f"backend:fake:{track_a}",
    "--track",
    f"frontend:fake:{track_b}",
    "--verify",
    "backend:echo backend-ok",
    "--verify",
    "frontend:exit 1",
]

completed = subprocess.run(
    [sys.executable, "-m", "goodteam_os.cli", *dispatch_args],
    capture_output=True,
    text=True,
    check=False,
)
print("exit_code", completed.returncode)
payload = json.loads(completed.stdout)
print("all_ok", payload["all_ok"])
print("outcomes", [job["outcome"] for job in payload["jobs"]])
PY
```

Pass criteria:

- The command exits with `1` because one verify fails.
- The printed JSON payload parses successfully.
- Both tracks are present in the JSON payload.
- The backend track is `ok`.
- The frontend track is `needs_human`.
- The failing verify is recorded for the frontend track.
- The passing track keeps its worker result and memory write-back note.
- One track's failure does not prevent the other track from running.

## Verify-specific checks

Confirm the report distinguishes worker failure from verify failure:

- If you force a fake worker failure, that track becomes `needs_human`.
- If you force a verify failure, that track becomes `needs_human`.
- The other track still completes and is reported.

## Gemini adapter shape check

Do not call Gemini. Instead, inspect the backend report or unit-test fixtures and
confirm the command shape that the backend is expected to build:

- `-p`
- `--approval-mode yolo`
- `--model`
- stdout capture

If the backend implementation does not expose those flags in tests or report
fixtures, that is a backend issue.

## Isolation guard check

Use a temp worktree-like directory and confirm the dispatch guard accepts it.
Then point the guard at a path that resolves inside one of the configured
`projects/*.local.yaml` repo roots and confirm it rejects it with a clear
message. The rejection is the expected result of the pure safety rule, not a
failure of dispatch itself.

## Report format

Produce a short markdown report with:

- Baseline command results.
- Dispatch check PASS/FAIL and the key printed lines.
- Verify escalation PASS/FAIL.
- Gemini shape check PASS/FAIL.
- Isolation guard PASS/FAIL.
- Any failure symptoms with the likely owning file.
- Explicit note that same-run non-propagation was treated as expected behavior.

## Cleanup

```bash
rm -rf /private/tmp/goodteam-auto-dispatch-qa
```
