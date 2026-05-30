# Plan — Memory write-back M1: BACKEND track (for Codex)

You are implementing the **backend** half of M1 automatic memory write-back in
the GoodTeam repo. A separate QA agent will verify the finished behavior. This
track is backend-only: parse completed worker `result.md` files, append memory
events through the owned WAL, and prompt real workers to emit the sections.

## Workspace

- **Repo root (run all commands from here):** `/Users/luke/WorkSpace/codeAi/GoodTeam`
- **You edit only:** `src/goodteam_os/` and `tests/`
- **You must NOT edit:** `frontend/`, project configs under `projects/`, target
  product repos, or unrelated docs
- **Do not commit.** Leave changes in the working tree for review.
- All paths in this doc are relative to the repo root above.

## Source docs and current code orientation

Read first:

- `docs/plans/memory-writeback-m1-prd.md`

Current code facts this plan is pinned to:

- `src/goodteam_os/memory.py:13` defines
  `MEMORY_HORIZONS = ("long-term", "mid-term", "short-term")`.
- `src/goodteam_os/memory.py:174` has
  `RoleMemoryStore.append_memory_event(role_key, *, horizon, text, source="", action="add")`.
  It validates horizon/action/non-empty text and always appends. It has **no
  deduplication**.
- `src/goodteam_os/memory.py:236` has `retrieve_relevant_events(...)`; it is
  horizon-agnostic by default (`horizons=None` searches all events).
- `src/goodteam_os/memory.py:312` has `rebuild_role_memory(...)`, and the class
  already has private event-reading logic. Do not call private memory helpers
  from `WorkerJobStore`; add a small public read-only accessor for write-back
  dedup instead.
- `src/goodteam_os/workers.py:151` has `class WorkerJobStore`.
- `src/goodteam_os/workers.py:156` `create_job(...)` writes
  `role_pickup_packet.json` at job creation time using
  `RoleMemoryStore.build_pickup_packet(role, query=task)`.
- `src/goodteam_os/workers.py:243` `run_job(job_id)` currently returns directly
  from each adapter branch.
- `src/goodteam_os/workers.py:314` `_run_cli_worker(...)` captures Codex final
  messages via `--output-last-message` and Claude final messages via stdout,
  writes `result.md`, then writes `status.json`.
- `src/goodteam_os/workers.py:434` `_worker_prompt(...)` is where the additive
  prompt instruction belongs.
- `src/goodteam_os/graph.py:1002-1047` creates all three worker jobs
  (`ui`, `implementation`, `qa`) before any worker is run, and
  `src/goodteam_os/graph.py:1048` then runs them.

## Required behavior

When any worker job completes with `status == "completed"`, GoodTeam parses that
job's `result.md` for memory sections and appends one memory event per captured
bullet/prose item to the role's memory store. All writes must go through
`RoleMemoryStore.append_memory_event(...)`, which writes `events.jsonl` and
updates horizon markdown. Do not write memory files directly from this feature.

Expected same-run behavior:

- A job's write-back does **not** reach downstream jobs in the same
  `run_implementation_workers(...)` call.
- This is expected, not a bug. `create_job(...)` builds each pickup packet before
  `run_job(...)`; `graph.py` creates `ui`, `implementation`, and `qa` jobs first,
  then runs them. M1 memory write-back benefits later jobs/runs whose pickup
  packets are created after the write-back. Intra-run propagation is out of
  scope.

Adapters:

- `codex` / `anthropic` / `claude`: write-back runs after successful CLI
  completion if `result.md` contains sections.
- `fake` / `noop`: they complete but emit no write-back sections, so this is a
  clean no-op.
- `human`: returns `needs_review`, not `completed`, so write-back does not run.
  Accept this gap for M1.

## Exact implementation plan

### Task 1 — add a pure parser in `workers.py`

Modify `src/goodteam_os/workers.py`.

Add module-level constants near the worker constants at the top:

- `MEMORY_WRITEBACK_SECTION_HORIZONS`
  - `"decisions"` -> `"long-term"`
  - `"learned"` -> `"mid-term"`
  - `"caveats"` -> `"mid-term"`
  - `"open questions"` -> `"short-term"`
- `MEMORY_WRITEBACK_MAX_ITEMS_PER_SECTION = 10`

Add this exact public helper signature at module level, outside
`WorkerJobStore`:

```python
def parse_result_sections(text: str) -> list[tuple[str, str]]:
```

Parser requirements:

- Pure function: no file IO, no memory store access, no timestamps, no mutation
  outside local variables.
- Return a flat list of `(horizon, text)` tuples.
- Recognize section headers case-insensitively.
- Accept `##` and `###` headers.
- Accept an optional trailing colon, e.g. `## Learned:`.
- Recognize exactly these section names after normalization:
  `Learned`, `Caveats`, `Decisions`, `Open Questions`.
- Normalize section names with `lower()`, remove the trailing colon, and
  collapse internal whitespace so `## Open Questions:` matches the
  `"open questions"` mapping key.
- Accept bullet markers `-`, `*`, and `•`.
- Strip whitespace around captured text.
- Skip empty bullet/prose text.
- Stop a section at the next markdown heading (`#` through `######`) so prose
  from unrelated sections is not swallowed.
- If a recognized section has bullets, each bullet becomes one event.
- If a recognized section has no bullets, fall back to the section's prose:
  capture only the first non-empty paragraph as one event, stopping at the next
  markdown heading so later unrelated content is never included.
- Cap each section at `MEMORY_WRITEBACK_MAX_ITEMS_PER_SECTION` items, so the
  parser returns at most 40 events.

Do not put deduplication in `parse_result_sections(...)`; dedup needs existing
events and belongs in `_write_back_memory(...)`.

### Task 2 — add a public event-text accessor for dedup

Modify `src/goodteam_os/memory.py`, inside `RoleMemoryStore`.

Add this exact public method signature near `read_memory_index(...)` /
`retrieve_relevant_events(...)`:

```python
def event_texts(self, role_key: str) -> set[str]:
```

Implementation requirements:

- Return the exact existing `text` values for all events for that role.
- This is read-only; it must not write memory files or refresh indexes.
- It may internally reuse `RoleMemoryStore`'s existing private event-reading
  logic.
- It is the only API `WorkerJobStore` should use for write-back dedup; do not
  reach across classes to call private memory helpers.

Do not modify `append_memory_event(...)`; it intentionally has no deduplication.

### Task 3 — add `_write_back_memory(...)` as the thin WAL wrapper

Modify `src/goodteam_os/workers.py`, inside `WorkerJobStore`.

Add this exact method signature near `run_job(...)` / adapter helpers:

```python
def _write_back_memory(self, job_root: Path, job: dict[str, Any]) -> dict[str, Any]:
```

Implementation requirements:

- Read `job_root / "result.md"`.
- Call `parse_result_sections(result_text)`.
- If there are no parsed items, return a note like:
  `{"status": "skipped", "reason": "no_sections", "appended": 0}`.
- Instantiate `RoleMemoryStore(self.project_root)`.
- Deduplicate here, before calling `append_memory_event(...)`.
- Use the public memory accessor to build an exact-text set:
  - `role = str(job["role"])`
  - `existing_texts = store.event_texts(role)`
  - skip a parsed item if its exact text is already in `existing_texts`
  - when appending a new item, also add its text to `existing_texts` so duplicate
    bullets inside the same `result.md` are skipped too
- Do **not** modify `append_memory_event(...)`; it intentionally has no dedup.
- Call `append_memory_event(...)` once per non-duplicate parsed item.
- Use source exactly:
  `source=f"{job['id']}/result.md"`
- Return a small machine-readable note with at least:
  - `status`: `"ok"` or `"skipped"`
  - `source`
  - `parsed`
  - `appended`
  - `skipped_duplicates`

All memory writes must go through `append_memory_event(...)`. Do not write
`events.jsonl`, horizon markdown, or `index.json` directly, and do not call
private `RoleMemoryStore` event-reading helpers from `workers.py`.

### Task 4 — refactor `run_job(...)` to call write-back after completion

Modify `src/goodteam_os/workers.py:243`.

Change `run_job(...)` from direct returns to a single captured status payload:

- Read `job_root`, `job`, and `adapter` as today.
- Dispatch as today, but assign the result to `status_payload`.
- After dispatch, if `status_payload.get("status") == "completed"`, call
  `_write_back_memory(job_root, job)`.
- Attach the result to `status_payload["memory_writeback"]`.
- Re-write `job_root / "status.json"` with the updated `status_payload` so the
  note is durable and visible to QA.
- Return `status_payload`.

Safety requirement:

- Wrap only the write-back call/update in `try/except Exception`.
- If write-back fails, do **not** flip the job to failed and do **not** raise.
- Instead attach:
  `{"status": "error", "error": "<exception message>"}`
  under `memory_writeback`, re-write `status.json`, and return the original
  completed job status plus that note.

Dispatch behavior must remain unchanged for failures and `needs_review`:

- CLI missing / nonzero / timeout stays `failed` and does not write back.
- `human` stays `needs_review` and does not write back.
- Unknown adapter still raises `ValueError`.

### Task 5 — update `_worker_prompt(...)` so real workers emit sections

Modify `src/goodteam_os/workers.py:434`.

Append this exact instruction string to the prompt list after
`## Expected Artifacts` content:

```text
## Memory Write-Back Sections
End your final response with these markdown sections when you have concise, non-secret takeaways to preserve. Omit a section only when it has no useful bullets.
Do not copy the parenthetical guidance text itself into your final answer.

## Learned
(one short bullet per reusable fact or lesson — omit this section if none)

## Caveats
(one short bullet per risk, gotcha, flaky command, environment constraint, or future-worker warning — omit this section if none)

## Decisions
(one short bullet per durable decision or changed contract — omit this section if none)

## Open Questions
(one short bullet per unresolved question or follow-up decision needed next — omit this section if none)

Keep each bullet short, factual, and free of secrets.
```

This is required for real CLI workers to produce parseable `result.md` content:
Codex writes its final message to `result.md` via `--output-last-message`;
Claude writes stdout into `result.md` via the current `capture == "stdout"`
path.

Test risk: `tests/test_workers.py` already asserts prompt contents, especially
around `_worker_prompt` behavior. Re-check and update/add focused assertions
without weakening the existing bounded-smoke cleanup checks.

## Test plan

Write tests before implementation where practical. Keep most coverage on the
pure parser; it is the highest-risk part.

### Parser unit tests

Add tests in `tests/test_workers.py` for `parse_result_sections(...)`.

Cover all of these:

- Standard sections with `-` bullets:
  - `## Decisions` maps to `long-term`
  - `## Learned` maps to `mid-term`
  - `## Caveats` maps to `mid-term`
  - `## Open Questions` maps to `short-term`
- Header tolerance:
  - lowercase / mixed case headers
  - `###` headers
  - trailing colon (`## Learned:`)
- Bullet tolerance:
  - `- item`
  - `* item`
  - `• item`
- Freeform result text around the sections; unrelated prose must be ignored.
- Prose fallback when no bullets exist under a recognized section.
- Empty sections are skipped.
- Each section is capped at 10 captured items.
- A later unrelated heading stops the current section.

Expected import:

```python
from goodteam_os.workers import WorkerJobStore, parse_result_sections, worker_backend_doctor
```

### Write-back and dedup tests

Add focused tests in `tests/test_workers.py`:

- CLI completed job with a seeded/fake `result.md` containing all four sections
  writes events to the expected horizons:
  - Decisions -> `long-term`
  - Learned -> `mid-term`
  - Caveats -> `mid-term`
  - Open Questions -> `short-term`
  - `events.jsonl` exists under
    `.goodteam/memory/roles/<role-slug>/events.jsonl`
  - event `source` is exactly `"<job_id>/result.md"`
  - returned status and `status.json` include `memory_writeback`
- Re-running the same job/result does not append duplicates. Assert event count
  is unchanged on the second `run_job(job["id"])`.
- Duplicate bullet text inside one `result.md` is appended only once.
- Existing event text from a different source also suppresses a duplicate.

Add a small unit test in `tests/test_memory.py` for
`RoleMemoryStore.event_texts(...)`:

- Empty role memory returns `set()`.
- After appending two events, it returns exactly those two `text` values.
- It does not expose timestamps, sources, horizons, or other event fields.

### No-op and safety tests

Add/adjust tests in `tests/test_workers.py`:

- `fake` completed job emits no sections and does not append events; status may
  include `memory_writeback.status == "skipped"`.
- `noop` completed job emits no sections and does not append events.
- `human` job returns `needs_review`, does not call write-back, and creates no
  events.
- Write-back exception path: monkeypatch `_write_back_memory` to raise after a
  completed fake/CLI job; assert the returned job remains `completed` and
  `status.json["memory_writeback"]["status"] == "error"`.

### Thin integration test

Use the existing fake `subprocess.Popen` pattern in `tests/test_workers.py` for a
Codex or Anthropic job so the test exercises:

`run_job(...)` -> `_run_cli_backend(...)` -> `_run_cli_worker(...)` ->
`_write_status(...)` -> `_write_back_memory(...)` -> `append_memory_event(...)`.

Do not add a graph-level same-run propagation test unless needed. If you do, the
assertion must be that downstream jobs created earlier do **not** see same-run
write-back in their existing `role_pickup_packet.json`.

## Acceptance

Run from `/Users/luke/WorkSpace/codeAi/GoodTeam`:

```bash
PYTHONPATH=src python -m pytest tests -q
ruff check src/goodteam_os/ tests/
```

Both commands must be clean before handing off to QA.

## Out of scope

- Frontend work.
- Memory panel UI.
- Embeddings, Graphiti, LangGraph Store projections, cross-project memory.
- Review-gate decision write-back.
- Intra-run propagation of newly written memory into already-created jobs.
