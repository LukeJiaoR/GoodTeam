# P0-1 First Runnable Dogfood

This dogfood path proves the current execution runtime can intake a GitHub issue
fixture, create deterministic execution identity, enqueue a safe worker job,
write ledger events, and replay the run without external network access.

It is intentionally not a production-complete real-adapter proof. The default
adapter is `noop`; `fake` is also safe. Real `codex`, `claude`, and `gemini`
adapter runs require separate credential and CLI validation.

## Prerequisites

- Run from the GoodTeam repository root.
- Use a local checkout with `src/goodteam_os` present.
- Use `.venv/bin/python` when available, or any Python that can import
  `goodteam_os` with `PYTHONPATH=src`.
- No GitHub network access or token is required for the default path.

## Command Sequence

```bash
scripts/p0_1_dogfood.sh
```

The script selects Python in this order:

1. `PYTHON`, when set.
2. repo-local `.venv/bin/python`, when executable.
3. `python3`.
4. `python`.

It exports `PYTHONPATH=src` and runs:

```bash
python -m goodteam_os.execution.dogfood --project-root "$REPO_ROOT" --adapter noop
```

To use the `fake` safe adapter instead:

```bash
GOODTEAM_DOGFOOD_ADAPTER=fake scripts/p0_1_dogfood.sh
```

## Expected Files Created

The default command writes only local GoodTeam runtime state under `.goodteam/`:

- `.goodteam/executions/<execution_id>/bindings.json`
- `.goodteam/executions/<execution_id>/task.json`
- `.goodteam/executions/<execution_id>/run.json`
- `.goodteam/executions/<execution_id>/events.jsonl`
- `.goodteam/workers/jobs/<worker_job_id>/job.json`
- `.goodteam/workers/jobs/<worker_job_id>/task.md`
- `.goodteam/workers/jobs/<worker_job_id>/context.md`
- `.goodteam/workers/jobs/<worker_job_id>/result.md`
- `.goodteam/workers/jobs/<worker_job_id>/status.json`
- `.goodteam/memory/roles/implementation/*.md`

The deterministic identity for issue `LukeJiaoR/GoodTeam#225` is:

- task id: `task_ac94f46d1c87e29cc99ff55ca3a48f1d`
- execution id: `exec_7655aa3eda146a76df1e6505cf23d604`

Re-running the command reuses the existing execution and worker job. It should
not append duplicate ledger events after the first run has completed.

## Expected Ledger Events

For the default first run, `events.jsonl` should contain ordered transitions:

1. `intake_created`
2. `worker_job_created`
3. `execution_started`
4. `execution_completed`

The script prints the execution id, worker job id, ledger file paths, event
count, and transition summary.

## Failure Recovery

- If Python cannot import `goodteam_os`, run from the repository root or set
  `PYTHONPATH=src`.
- If the local runtime state is malformed, move the specific execution and
  worker job directories aside, then re-run the script:

```bash
mv .goodteam/executions/exec_7655aa3eda146a76df1e6505cf23d604 /tmp/goodteam-p0-1-execution.bak
```

- If only the worker result is incomplete, inspect
  `.goodteam/workers/jobs/<worker_job_id>/status.json` and `result.md`.
- Do not delete unrelated `.goodteam/` data or `output/` directories as part of
  this recovery path.

## Switching To A Real Adapter

The script accepts an adapter through `GOODTEAM_DOGFOOD_ADAPTER`:

```bash
GOODTEAM_DOGFOOD_ADAPTER=codex scripts/p0_1_dogfood.sh
GOODTEAM_DOGFOOD_ADAPTER=claude scripts/p0_1_dogfood.sh
GOODTEAM_DOGFOOD_ADAPTER=gemini scripts/p0_1_dogfood.sh
```

Because the dogfood run is idempotent, changing `GOODTEAM_DOGFOOD_ADAPTER`
does not rewrite an existing `noop` or `fake` execution. Use a clean dogfood
workspace or move the deterministic execution directory and matching worker job
aside before testing a real adapter.

Before using a real adapter, verify the relevant CLI is installed, authenticated,
and safe to run in the current checkout. A real adapter may invoke external CLIs,
read credentials, and make network calls through that CLI. The P0-1 dogfood
default does not validate those production paths.

## Fake Versus Real

Real in this first run:

- deterministic task and execution ids
- `ExecutionOrchestrator.intake_github_issue`
- `ExecutionLedger` task/run/event writes
- `WorkerJobStore` job creation
- worker job execution through the built-in `noop` or `fake` adapter
- ledger replay from disk

Fake or noop in this first run:

- GitHub issue fetch uses a local in-process fixture for issue `#225`
- `noop` and `fake` do not invoke Codex, Claude, Gemini, or any external CLI
- no GitHub branch, commit, pull request, webhook, frontend, sandbox, or
  enterprise automation is created
