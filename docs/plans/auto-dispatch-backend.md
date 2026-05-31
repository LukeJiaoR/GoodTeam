# Plan — Minimal auto-dispatch: BACKEND track (for Codex)

You are implementing the **backend** half of Moat #1 phase 1 in the GoodTeam
repo. A separate QA agent will verify the finished behavior. This track is
backend-only: add a standalone `dispatch` CLI, parallelize worker runs, add the
Gemini CLI adapter, and run verification after each track.

## Workspace

- **Repo root (run all commands from here):** `/Users/luke/WorkSpace/codeAi/GoodTeam`
- **You edit only:** `src/goodteam_os/` and `tests/`
- **You must NOT edit:** `frontend/`, `projects/`, target product repos, or unrelated docs
- **Do not commit.** Leave changes in the working tree for review.
- All paths in this doc are relative to the repo root above.

## Source docs and current code orientation

Read first:

- `docs/plans/auto-dispatch-prd.md`

Current code facts this plan is pinned to:

- `src/goodteam_os/workers.py` already owns the reusable worker primitives:
  - `CliBackendSpec`
  - `CLI_BACKENDS`
  - `BUILTIN_WORKER_MODES`
  - `create_job(...)`
  - `run_job(job_id)`
  - `_run_cli_worker(...)`
  - `_write_back_memory(...)`
  - `_cli_preflight(...)`
  - `DEFAULT_CODEX_PATH`
  - `DEFAULT_CLAUDE_PATH`
- `src/goodteam_os/projects.py` already has `WORKER_MODES`, `check_project_target(...)`, and `update_worker_backend_config(...)`.
- `src/goodteam_os/cli.py` already uses `argparse` subcommands and `build_parser()`.
- `src/goodteam_os/staging.py` already shows the preferred local-command / ssh-command execution shape for verification runs.
- `src/goodteam_os/graph.py` must not be touched for this feature.

## Required behavior

This feature is the smallest useful auto-dispatch slice:

1. a standalone `goodteam-os dispatch` CLI that accepts multiple tracks and runs
   them in parallel;
2. a new Gemini worker adapter using the verified `gemini` CLI invocation;
3. per-track verification, where a failed worker or failed verify escalates the
   track to `needs_human`.

The implementation must reuse the existing worker pipeline:

- `dispatch` must build jobs with `WorkerJobStore.create_job(...)`.
- It must run them with `WorkerJobStore.run_job(...)`.
- It must rely on `CLI_BACKENDS` and the existing M1 write-back behavior.
- It must not reimplement worker process spawning or memory writes.

Expected output shape:

- The CLI returns a consolidated JSON payload.
- Each track report includes job identity, adapter, status, `result.md` path,
  changed files, memory write-back note, verify note, outcome, and summary.
- The top-level payload groups track results into `ok` and `needs_human`.
- A single track failure must not stop the other track from running.

Isolation/safety:

- Dispatch is for isolated/owned repos, not a live shared project workspace.
- Refuse or warn when `--project-root` appears to point at an active project or
  a repo boundary the plan marks as unsafe.
- Keep `graph.py` untouched.

## Exact implementation plan

### Task 1 — add a standalone dispatch CLI entrypoint

Modify `src/goodteam_os/cli.py`.

Add a `dispatch` subcommand in `build_parser()` and wire it into `main()`:

```python
dispatch = subparsers.add_parser("dispatch")
dispatch.add_argument("--project-root", type=Path, required=True)
dispatch.add_argument("--track", action="append", default=[])
dispatch.add_argument("--verify", action="append", default=[])
dispatch.add_argument("--constraints-file", type=Path)
```

The CLI shape must support multiple tracks in a single command. Keep the parser
minimal and explicit rather than inventing a new config language.

Proposed track syntax for v1:

```text
--track <role>:<adapter>:<task-file-or-inline-task>
```

Proposed verify syntax for v1:

```text
--verify <role>:<shell-command>
```

Add a helper in `workers.py` or a new module under `src/goodteam_os/` to parse
those specs into structured track definitions. Keep the parsing logic small and
testable.

Add a CLI handler that:

- parses all `--track` and `--verify` entries;
- loads task content from a file path when the third segment exists on disk;
- otherwise treats it as inline task text;
- calls the dispatcher helper;
- prints the returned JSON payload;
- returns exit code `0` when every track outcome is `ok`, else `1`.

### Task 2 — implement the parallel dispatch runner

Modify `src/goodteam_os/workers.py` or add a new helper module under
`src/goodteam_os/` if that keeps the file focused.

Add a small public dispatcher helper with a signature like:

```python
def dispatch_tracks(
    *,
    project_root: Path,
    tracks: list[dict[str, object]],
    verify_commands: dict[str, str] | None = None,
    constraints_file: Path | None = None,
) -> dict[str, object]:
```

Implementation requirements:

- Use `ThreadPoolExecutor` or an equivalent thread pool.
- Run one worker track per thread.
- Capture per-track exceptions so one track crash does not stop the batch.
- Reuse `WorkerJobStore.create_job(...)` and `run_job(...)`.
- Reuse the existing `job_root` layout and `result.md` / `status.json` files.
- Attach the `memory_writeback` note already written by M1.
- After `run_job(...)` returns, read the job directory for:
  - `result.md`
  - `status.json`
  - changed files if they are already recorded by the worker status, or a
    conservative empty list if not available from the current backend

Dispatch runner responsibilities:

- create all jobs first or at least do not block one track on another;
- run tracks concurrently;
- collect each job's result into a deterministic report list;
- classify a track as `ok` only when the worker completed and verify passed;
- classify a track as `needs_human` when the worker failed or verify failed;
- preserve the original worker status and summary in the report.

Track exceptions should be represented in the report rather than thrown away.
The batch still needs to complete and report the other tracks.

### Task 3 — add the Gemini worker adapter

Modify `src/goodteam_os/workers.py`.

Add a new `CliBackendSpec` for Gemini, modeled on `CLAUDE_BACKEND`:

```python
GEMINI_BACKEND = CliBackendSpec(
    name="gemini",
    which_name="gemini",
    default_path=lambda: DEFAULT_GEMINI_PATH,
    model_default=DEFAULT_GEMINI_MODEL,
    auth_env=("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"),
    capture="stdout",
    label="Gemini CLI",
    build_command=_gemini_build_command,
    summarize=_gemini_summary,
    not_available_message="Gemini CLI is not available. Install gemini or add it to PATH before running gemini workers.",
)
```

Use a new `DEFAULT_GEMINI_PATH` module constant, mirroring the existing
`DEFAULT_CODEX_PATH` / `DEFAULT_CLAUDE_PATH` pattern.

Extend `CliBackendSpec` with a `prompt_via` field so the run loop can make a
small, controlled split between stdin-backed backends and argument-backed
backends. The default stays `"stdin"` for Codex and Claude. Gemini uses
`"arg"`, because its `-p` flag consumes the prompt value directly. In
`_run_cli_worker(...)`, support both modes: keep the existing stdin flow for
Codex/Claude, and when `prompt_via == "arg"` append the prompt as the `-p`
value instead of piping it through `communicate(input=...)`. This is the
smallest allowed expansion of the worker loop, not a new execution model.
Before coding, confirm the Gemini CLI's `-p` shape with `gemini --help`.

The verified command shape must be captured in tests and should follow the PRD:

- non-interactive prompt mode: `-p`
- autonomous approval: `--approval-mode yolo`
- model selection: `--model <m>`
- result captured from stdout

Do **not** invent or reference the Antigravity IDE. This adapter is for the
headless `gemini` CLI only.

The adapter should behave like the other CLI backends:

- `which_name` looks up `gemini`
- `capture="stdout"`
- `run_job(...)` should complete from stdout when the exit code is 0
- `worker_backend_doctor(...)` should report readiness for `gemini` too
- the unit test for the adapter must assert that the prompt is passed as the
  value for `-p`, not swallowed by the later flags

If `projects.WORKER_MODES` or `check_project_target(...)` need a corresponding
update so `gemini` is treated as a valid worker mode, make that small change in
the existing backend config plumbing. Keep the scope tight.

### Task 4 — add verify execution and outcome classification

Add a thin verification helper with a signature like:

```python
def run_track_verify(command: str, *, cwd: Path) -> dict[str, object]:
```

Implementation requirements:

- Run the verify command in the track's project root or declared subpath.
- Use the same shell-backed local-command pattern as `smoke.run_shell_command`
  or `staging._run_ssh_command` uses for a shell command.
- Capture `command`, `cwd`, `exit_code`, `stdout`, and `stderr`.
- Report a boolean `passed` field.
- Include a short `output_tail` field or similar compact tail of output for the
  consolidated JSON report.
- A nonzero exit code or exception must mark the track `needs_human`.

The dispatch report should treat verify failure as escalation, not as a hard
batch failure. The batch is successful only when all track outcomes are `ok`.

### Task 5 — isolation guard

Add a small safety check before dispatch starts.

The check should be a pure, testable helper with a concrete rule:

- resolve `project_root`
- if it is equal to, or nested inside, any configured `projects/*.local.yaml`
  `repos` path, reject it as unsafe
- allow temp, worktree, and dev-workspace paths
- optionally allow `--allow-unsafe` to override the rejection, but keep the
  default safe-by-construction behavior

Keep the rule conservative and easy to explain in the CLI error message.
Do not mutate anything to perform the check.

The guard belongs in the dispatcher entrypoint, not in `graph.py`.

## Test plan

Write tests before implementation where practical. Keep the majority of coverage
on the new pure parsing / orchestration helpers.

### CLI parsing tests

Add tests in `tests/test_cli.py` for:

- `dispatch` subcommand registration
- `--track` parsing
- `--verify` parsing
- exit code `1` when any track outcome is `needs_human`

### Dispatcher unit tests

Add tests in `tests/test_workers.py` or a dedicated dispatcher test module for:

- parallel execution of two fake tracks
- one worker failure does not block the other track
- one verify failure does not block the other track
- both completed track jobs still run and get reports
- top-level report groups jobs into `ok` and `needs_human`
- consolidated JSON includes the per-track `memory_writeback` note already
  attached by M1
- a track exception is captured in the report instead of crashing the whole run
- the report's `changed_files` field is populated from
  `git -C <project_root> diff --name-only` rather than left as a permanent empty
  list

### Gemini adapter tests

Add tests in `tests/test_workers.py` for:

- `CLI_BACKENDS` contains `gemini`
- Gemini command shape includes:
  - `-p`
  - `--approval-mode`
  - `yolo`
  - `--model`
- Gemini result capture uses stdout
- `worker_backend_doctor("gemini")` reports readiness like the other CLI backends

### Verify tests

Add tests for the verify runner and escalation behavior:

- a passing verify marks the track `ok`
- a failing verify marks the track `needs_human`
- the failing verify does not stop the other track
- the verify helper records exit code and output tail

### Isolation guard tests

Add tests for the guard helper:

- project roots that resolve inside configured `projects/*.local.yaml` repos are
  rejected
- isolated temp worktrees are accepted

### Thin integration test

Use fake worker backends and a fake verify runner to exercise the full path:

`dispatch CLI -> track parser -> WorkerJobStore.create_job -> run_job -> M1 write-back -> verify -> consolidated report`

Do not call real Gemini or Codex.

## Acceptance

Run from `/Users/luke/WorkSpace/codeAi/GoodTeam`:

```bash
PYTHONPATH=src python -m pytest tests -q
ruff check src/goodteam_os/ tests/
```

Both commands must be clean before handing off to QA.

## Out of scope

- Frontend work.
- `graph.py`.
- Any new interaction model beyond standalone dispatch.
- Real Gemini/Codex live runs in tests.
- Full collaboration DAG / committee / self-correction loops.
